"""黄灯对照，2025-01-02 ~ 2026-11-13，账户连续。不改生产参数。

nostock_lowvol：黄灯清掉股票，全部给低波。
keep_lowvol：黄灯仍留截面还要的旧股票，腾出的权重给低波，不买行业 ETF。
nostock_etf：黄灯清掉股票，行业 ETF 按自身权重留下，剩下给低波。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

import pandas as pd

from zstock.common.book_codes import FEE_FREE_CODES, INDUSTRY_ETFS
from zstock.strategy_management.pipeline import StrategyPipeline, _state_for_grade
from zstock.strategy_management.script.backtester import (
    Backtester,
    make_ohlcv_provider_from_dict,
)
from zstock.strategy_management.strategy_industry_etf import IndustryEtfStrategy
from zstock.strategy_management.strategy_low_volatility import LowVolatilityStrategy
import zstock.strategy_management.sleeve_combine as sleeve_combine

START = "2025-01-02"
END = "2026-11-13"
OUT = Path(__file__).resolve().parent / "output" / "verify_light"
INDUSTRY = {str(c).zfill(6) for c in INDUSTRY_ETFS}
FREE = {str(c).zfill(6) for c in FEE_FREE_CODES}
# 银行既是行业 ETF，也是低波 30% 袖套。上限是否生效，看其余六只行业。
INDUSTRY_EX_BANK = INDUSTRY - {"512800"}


class _Ctx:
    hysteresis: "RedHysteresis | None" = None
    industry_cap: float | None = None
    yellow_mode: str | None = None
    grades: list[tuple[str, str, str]] | None = None


CTX = _Ctx()


class RedHysteresis:
    """进红灯、离开红灯都要连续 n 天。黄灯和绿灯之间当天切换。"""

    def __init__(self, days: int):
        self.days = int(days)
        self.effective: str | None = None
        self.red_run = 0
        self.off_run = 0

    def step(self, raw: str) -> str:
        raw = raw if raw in ("red", "yellow", "green") else "yellow"
        if raw == "red":
            self.red_run += 1
            self.off_run = 0
            if self.effective != "red" and self.red_run >= self.days:
                self.effective = "red"
            elif self.effective is None:
                self.effective = "yellow"
        else:
            self.off_run += 1
            self.red_run = 0
            if self.effective == "red":
                if self.off_run >= self.days:
                    self.effective = raw
            else:
                self.effective = raw
        return self.effective or "yellow"


def _yellow_targets(mode, current, stocks, industry, low_vol):
    """黄灯少留股票。空出来的权重给低波，不再把整份行业组合缩放进去。"""
    current_w = sleeve_combine._clean(current)
    stock_w = {
        c: w
        for c, w in sleeve_combine._clean(stocks).items()
        if sleeve_combine.is_stock(c)
    }
    industry_w = sleeve_combine._clean(industry)
    low_w = sleeve_combine._clean(low_vol)
    if mode == "nostock_lowvol":
        return sleeve_combine._clean(sleeve_combine._fill(low_w, 1.0))
    if mode == "keep_lowvol":
        kept = {}
        for code, cur in current_w.items():
            if sleeve_combine.is_stock(code) and stock_w.get(code, 0.0) > 0:
                kept[code] = min(cur, stock_w[code])
        out = sleeve_combine._fit(kept, 1.0)
        residual = 1.0 - sum(out.values())
        sleeve_combine._add(out, sleeve_combine._fill(low_w, residual))
        return sleeve_combine._clean(out)
    if mode == "nostock_etf":
        etf = {c: w for c, w in industry_w.items() if c in sleeve_combine._INDUSTRY}
        out = sleeve_combine._fit(etf, 1.0)
        residual = 1.0 - sum(out.values())
        sleeve_combine._add(out, sleeve_combine._fill(low_w, residual))
        return sleeve_combine._clean(out)
    raise ValueError(mode)


def _cap_red(grade, weights, low_vol, cap):
    if str(grade).lower() != "red" or cap is None:
        return weights
    ind_sum = sum(w for c, w in weights.items() if c in INDUSTRY)
    if ind_sum <= float(cap) + 1e-12:
        return weights
    scale = 0.0 if float(cap) <= 1e-12 or ind_sum <= 0 else float(cap) / ind_sum
    freed = ind_sum * (1.0 - scale)
    out = {}
    for code, weight in weights.items():
        if code in INDUSTRY:
            kept = weight * scale
            if kept > 1e-8:
                out[code] = kept
        elif weight > 1e-8:
            out[code] = weight
    sleeve = {c: w for c, w in (low_vol or {}).items() if w > 1e-8}
    total = sum(sleeve.values())
    if freed > 1e-12 and total > 0:
        for code, weight in sleeve.items():
            out[code] = out.get(code, 0.0) + freed * weight / total
    return out


def _install_patches() -> None:
    orig_targets = StrategyPipeline._targets_by_light
    orig_score = StrategyPipeline.score_signals
    orig_ind = IndustryEtfStrategy.select
    orig_low = LowVolatilityStrategy.select
    orig_combine = sleeve_combine.combine_by_light
    signals: dict[str, pd.DataFrame] = {}
    industry: dict[str, dict] = {}
    lowvol: dict[str, dict] = {}
    seen = {"n": 0}

    async def score(self, trade_date: str):
        td = str(trade_date)[:10]
        hit = signals.get(td)
        if hit is not None:
            out = hit.copy()
            out.attrs.update(hit.attrs)
            return out
        df = await orig_score(self, td)
        cols = [c for c in ("code", "rank") if c in df.columns]
        snap = df[cols].copy() if cols else df.copy()
        snap.attrs.update(dict(df.attrs))
        signals[td] = snap
        seen["n"] += 1
        if seen["n"] % 50 == 0:
            print(f"  ... {td}  已算 {seen['n']} 天", flush=True)
        return df

    async def select_ind(self, trade_date=None):
        td = str(trade_date or "")[:10]
        hit = industry.get(td)
        if hit is not None:
            return dict(hit)
        out = await orig_ind(self, trade_date)
        industry[td] = dict(out)
        return out

    async def select_low(self, trade_date=None):
        td = str(trade_date or "")[:10]
        hit = lowvol.get(td)
        if hit is not None:
            return dict(hit)
        out = await orig_low(self, trade_date)
        lowvol[td] = dict(out)
        return out

    async def targets(self, trade_date, *, grade, state, results, **kwargs):
        raw = str(grade or "yellow").lower()
        use = CTX.hysteresis.step(raw) if CTX.hysteresis is not None else raw
        results["market_grade"] = use
        results["raw_grade"] = raw
        if CTX.grades is not None:
            CTX.grades.append((str(trade_date)[:10], raw, use))
        return await orig_targets(
            self,
            trade_date,
            grade=use,
            state=_state_for_grade(use),
            results=results,
            **kwargs,
        )

    def combine(grade, current, stocks, industry_w, low_w):
        if str(grade or "").lower() == "yellow" and CTX.yellow_mode:
            out = _yellow_targets(CTX.yellow_mode, current, stocks, industry_w, low_w)
        else:
            out = orig_combine(grade, current, stocks, industry_w, low_w)
        return _cap_red(grade, out, low_w, CTX.industry_cap)

    StrategyPipeline.score_signals = score
    StrategyPipeline._targets_by_light = targets
    IndustryEtfStrategy.select = select_ind
    LowVolatilityStrategy.select = select_low
    sleeve_combine.combine_by_light = combine


def _sleeve(code: str) -> str:
    c = str(code).zfill(6) if str(code).isdigit() else str(code)
    if c in FREE:
        return "lowvol"
    if c in INDUSTRY_EX_BANK:
        return "sector"
    if c == "512800":
        return "bank"
    return "stock"


def _gross_stats(daily_net: pd.Series, cost: pd.Series) -> dict:
    """成本从净值里乘掉，权重不变，所以把成本乘回去就是零手续费路径。"""
    net = daily_net.astype(float)
    c = cost.reindex(net.index).fillna(0.0).astype(float).clip(upper=0.5)
    gross = (1.0 + net) / (1.0 - c) - 1.0
    wealth = (1.0 + gross).cumprod()
    years = max(len(net) / 252.0, 1e-6)
    total = float(wealth.iloc[-1] - 1.0) if len(wealth) else 0.0
    ann = (1.0 + total) ** (1.0 / years) - 1.0 if total > -1 else -1.0
    dd = float((wealth / wealth.cummax() - 1.0).min()) if len(wealth) else 0.0
    return {"gross_total": total, "gross_ann": ann, "gross_mdd": dd}


def _yearly(equity: pd.Series) -> list[dict]:
    eq = equity.copy()
    eq.index = eq.index.astype(str)
    prev = 1.0
    rows = []
    for year, chunk in eq.groupby(eq.index.str[:4]):
        end = float(chunk.iloc[-1])
        rows.append({"year": str(year), "return": end / prev - 1.0, "end_nav": end})
        prev = end
    return rows


def _weights(result, grades: list[tuple[str, str, str]]) -> dict:
    rows = []
    for snap in result.holdings_log:
        td = str(snap.get("trade_date"))[:10]
        bucket = {"stock": 0.0, "sector": 0.0, "bank": 0.0, "lowvol": 0.0}
        for h in snap.get("holdings") or []:
            bucket[_sleeve(str(h.get("code")))] += float(h.get("weight") or 0.0)
        rows.append({"date": td, **bucket})
    hold = pd.DataFrame(rows)
    if hold.empty:
        return {}
    g = pd.DataFrame(grades, columns=["date", "raw", "effective"])
    g = g.drop_duplicates("date", keep="last")
    m = hold.merge(g, on="date", how="left")
    out = {"avg": {k: float(m[k].mean()) for k in ("stock", "sector", "bank", "lowvol")}}
    for key in ("raw", "effective"):
        if key not in m.columns or m[key].isna().all():
            continue
        out[key] = {
            str(name): {c: float(chunk[c].mean()) for c in ("stock", "sector", "bank", "lowvol")}
            for name, chunk in m.groupby(key)
        }
    if "raw" in m.columns:
        red = m[m["raw"] == "red"]
        out["raw_red_days"] = int(len(red))
        out["raw_red_stock"] = float(red["stock"].mean()) if len(red) else 0.0
        out["raw_red_sector"] = float(red["sector"].mean()) if len(red) else 0.0
    return out


def _pack(name: str, result, grades: list[tuple[str, str, str]]) -> dict:
    m = result.metrics
    n = len(result.daily_returns)
    years = max(n / 252.0, 1e-6)
    gross = _gross_stats(result.daily_returns, result.cost_series)
    gross["gross_ann_from_price"] = (1.0 + float(m.get("total_return_gross", 0.0))) ** (
        1.0 / years
    ) - 1.0
    return {
        "name": name,
        "days": n,
        "start": str(result.equity_curve.index.min()),
        "end": str(result.equity_curve.index.max()),
        "total": float(m.get("total_return", 0.0)),
        "ann": float(m.get("annualized_return", 0.0)),
        "mdd": float(m.get("max_drawdown", 0.0)),
        "sharpe": float(m.get("sharpe", 0.0)),
        "vol": float(m.get("annualized_vol", 0.0)),
        "cost": float(m.get("total_cost", 0.0)),
        "turnover": float(result.turnover_series.sum()),
        "rebalance": int(m.get("rebalance_count", 0)),
        **gross,
        "years": _yearly(result.equity_curve),
        "weights": _weights(result, grades),
    }


async def _run_variant(bt: Backtester, provider, name: str, yellow: str) -> dict:
    print(f"\n=== {name} ===", flush=True)
    CTX.hysteresis = None
    CTX.industry_cap = None
    CTX.yellow_mode = yellow
    CTX.grades = []
    bt.strategy.book.reset()
    bt.fee_rate = 0.0015
    bt.strategy._turnover_controller.fee_rate = 0.0015
    result = await bt.run(
        start_date=START,
        end_date=END,
        ohlcv_provider=provider,
        rebalance_freq=10,
        verbose=False,
        use_precomputed_factors=True,
    )
    packed = _pack(name, result, CTX.grades)
    yel = (packed.get("weights") or {}).get("raw", {}).get("yellow", {})
    print(
        f"{name}: 扣费年化 {packed['ann']:.2%}  回撤 {packed['mdd']:.2%}  "
        f"费用 {packed['cost']:.2%}  毛年化 {packed['gross_ann']:.2%}  "
        f"毛回撤 {packed['gross_mdd']:.2%}  "
        f"黄灯股票 {yel.get('stock', 0):.0%} 低波 {yel.get('lowvol', 0):.0%}",
        flush=True,
    )
    return packed


async def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    logging.getLogger("zstock").setLevel(logging.WARNING)
    _install_patches()
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database

    await init_zstock_database()
    try:
        bt = Backtester(fee_rate=0.0015, initial_capital=10_000_000)
        out_path = OUT / "summary_yellow.json"
        first_name, first_mode = "nostock_lowvol", "nostock_lowvol"
        print(f"{first_name} {START} → {END}（同时加载行情并填缓存）", flush=True)
        CTX.hysteresis = None
        CTX.industry_cap = None
        CTX.yellow_mode = first_mode
        CTX.grades = []
        first = await bt.run_real_data(
            start_date=START,
            end_date=END,
            lookback_days=250,
            use_precomputed_factors=True,
            rebalance_freq=10,
            output_dir=None,
            verbose=False,
            save_outputs=False,
        )
        rows = [_pack(first_name, first, CTX.grades)]
        yel = (rows[0].get("weights") or {}).get("raw", {}).get("yellow", {})
        print(
            f"{first_name}: 扣费年化 {rows[0]['ann']:.2%}  回撤 {rows[0]['mdd']:.2%}  "
            f"费用 {rows[0]['cost']:.2%}  毛年化 {rows[0]['gross_ann']:.2%}  "
            f"黄灯股票 {yel.get('stock', 0):.0%} 低波 {yel.get('lowvol', 0):.0%}",
            flush=True,
        )
        out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        provider = make_ohlcv_provider_from_dict(bt._ohlcv_full)
        for name, mode in (
            ("keep_lowvol", "keep_lowvol"),
            ("nostock_etf", "nostock_etf"),
        ):
            packed = await _run_variant(bt, provider, name, mode)
            rows.append(packed)
            out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n写好", out_path, flush=True)
        return 0
    finally:
        await close_zstock_database()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
