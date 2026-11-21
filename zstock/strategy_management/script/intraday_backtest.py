"""日内规则回测。

信号用上一交易日收盘后的策略结果，下一交易日 09:30 的 5 分钟 K 成交。
5 分钟线来自 stockdb，用不复权价。股票默认参数，行业 ETF / 国债 / 黄金用代码表。

    python -m zstock.strategy_management.script.intraday_backtest
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from zstock.common.book_codes import BOOK_OHLCV_CODES, INDEX_CODE
from zstock.common.config.strategy_config import load_strategy_params
from zstock.common.utils.common_utils import normalize_code, normalize_date
from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
from zstock.common.utils.stockdb_utils import StockDbClient
from zstock.data_management.query_service import get_data_query_service
from zstock.order_management.intraday import IntradayBook, RiskTable
from zstock.order_management.intraday_sim import SharePos, simulate_day
from zstock.strategy_management.market_book import (
    holdings_frame,
    needs_book_trade,
    park_treasury,
    weight_map,
)
from zstock.strategy_management.pipeline import StrategyPipeline
from zstock.strategy_management.script.backtester import _append_book_bars

logger = logging.getLogger(__name__)
OUTPUT = Path(__file__).resolve().parent / "output" / "intraday_5m"


def _close_map(frame: pd.DataFrame) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if frame is None or frame.empty or "trade_date" not in frame.columns:
        return out
    for raw, px in zip(frame["trade_date"].tolist(), frame["close"].tolist() if "close" in frame.columns else []):
        day = normalize_date(str(raw)[:10])
        try:
            price = float(px)
        except (TypeError, ValueError):
            continue
        if day and price > 0:
            out[day] = price
    return out


class _Hist:
    """按交易日往前累加收盘，供账户均线使用。"""

    def __init__(self, closes: Dict[str, Dict[str, float]]):
        self.closes = closes
        self.index_rows: List[dict] = []
        self.asset: Dict[str, List[float]] = {}

    def add(self, day: str) -> None:
        day_ohlcv = {}
        for code, series in self.closes.items():
            px = series.get(day)
            if px is None:
                continue
            day_ohlcv[code] = pd.DataFrame(
                [{"trade_date": day, "close": px, "high": px, "low": px, "volume": 0.0}]
            )
        if day_ohlcv:
            _append_book_bars(self.index_rows, self.asset, day_ohlcv)


class _MinuteBook:
    def __init__(self, client: StockDbClient, start: str, end: str):
        self.client = client
        self.start = start
        self.end = end
        self.by_code_day: Dict[str, Dict[str, pd.DataFrame]] = {}

    def ensure(self, codes: List[str]) -> None:
        missing = [normalize_code(c) for c in codes if normalize_code(c) not in self.by_code_day]
        for code in missing:
            got = self.client.get_bars([code], self.start, self.end, frequency="5m", fq="bfq")
            frame = got.get(code)
            bucket: Dict[str, pd.DataFrame] = {}
            if frame is not None and not frame.empty and "trade_date" in frame.columns:
                day_key = frame["trade_date"].map(lambda x: str(x)[:10])
                for day, part in frame.groupby(day_key, sort=False):
                    bucket[str(day)] = part.reset_index(drop=True)
            self.by_code_day[code] = bucket
            logger.info("5分钟 %s %s 天", code, len(bucket))

    def day(self, codes: List[str], day: str) -> Dict[str, pd.DataFrame]:
        out = {}
        for code in codes:
            code = normalize_code(code)
            part = self.by_code_day.get(code, {}).get(day)
            if part is not None and not part.empty:
                out[code] = part
        return out


def _weights(positions: Dict[str, SharePos], cash: float, prices: Dict[str, float]) -> Dict[str, float]:
    equity = cash
    value = {}
    for code, pos in positions.items():
        px = prices.get(code) or pos.cost
        if px and px > 0 and pos.shares > 0:
            value[code] = pos.shares * px
            equity += value[code]
    if equity <= 0:
        return {}
    return {code: mv / equity for code, mv in value.items()}


async def _target_for(
    strategy: StrategyPipeline,
    decision,
    signal_day: str,
    positions: Dict[str, SharePos],
    cash: float,
    prices: Dict[str, float],
    index_rows,
    asset_hist,
    last_state: Optional[str],
    materialize: bool,
    band: float,
    min_util: float,
) -> tuple:
    current = _weights(positions, cash, prices)
    nav = cash + sum(positions[c].shares * prices.get(c, positions[c].cost) for c in positions)
    if decision.weights is not None:
        proposed = {normalize_code(k): float(v) for k, v in decision.weights.items() if float(v) > 1e-8}
        changed = last_state != decision.state
        if positions and not changed and not needs_book_trade(current, proposed, band=band, min_utilization=min_util):
            target = current
        else:
            target = weight_map(park_treasury(holdings_frame(proposed)))
        return target, decision.state
    if not materialize:
        return current, decision.state
    try:
        signals = await strategy.score_signals(signal_day)
        summary = await strategy.execute_full_pipeline(
            trade_date=signal_day,
            current_positions=holdings_frame(current) if current else None,
            total_capital=nav,
            precomputed_signals=signals,
            index_closes=[float(r["close"]) for r in index_rows],
            asset_closes=dict(asset_hist),
            index_bars=list(index_rows),
            book_decision=decision,
        )
    except Exception as exc:
        logger.warning("%s 进攻目标失败，沿用当前仓位: %s", signal_day, exc)
        return current, decision.state
    if summary.get("status") != "success":
        logger.warning("%s 进攻目标状态 %s，沿用当前仓位", signal_day, summary.get("status"))
        return current, decision.state
    frame = (summary.get("results") or {}).get("final_holdings")
    if frame is None or frame.empty:
        return weight_map(park_treasury(None)), decision.state
    return weight_map(park_treasury(frame)), decision.state


def _stats(equity: List[float], dates: List[str]) -> dict:
    if not equity:
        return {}
    start = equity[0]
    end = equity[-1]
    peak = equity[0]
    max_dd = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            max_dd = min(max_dd, value / peak - 1.0)
    rets = [equity[i] / equity[i - 1] - 1.0 for i in range(1, len(equity)) if equity[i - 1] > 0]
    mean = sum(rets) / len(rets) if rets else 0.0
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1) if len(rets) > 1 else 0.0
    sharpe = mean / math.sqrt(var) * math.sqrt(252) if var > 0 else 0.0
    yearly = []
    by_year: Dict[str, List[float]] = {}
    for day, value in zip(dates, equity):
        by_year.setdefault(day[:4], []).append(value)
    prev_end = start
    for year in sorted(by_year):
        series = by_year[year]
        y_peak = series[0]
        y_dd = 0.0
        for value in series:
            y_peak = max(y_peak, value)
            if y_peak > 0:
                y_dd = min(y_dd, value / y_peak - 1.0)
        yearly.append({
            "year": year,
            "return": series[-1] / prev_end - 1.0 if prev_end else 0.0,
            "mdd": y_dd,
            "end": series[-1],
        })
        prev_end = series[-1]
    return {
        "return": end / start - 1.0 if start else 0.0,
        "mdd": max_dd,
        "sharpe": sharpe,
        "end": end,
        "yearly": yearly,
    }


@dataclass
class IntradayContext:
    fee: float
    stamp: float
    initial: float
    band: float
    min_util: float
    minutes: _MinuteBook
    session_days: List[str]
    close_of: Dict[str, Dict[str, float]]
    calendar: List[str]
    strategy: StrategyPipeline


async def load_context(start: str, end: str) -> IntradayContext:
    params = load_strategy_params()
    bt = params.get("backtest") or {}
    book_cfg = params.get("book_regime") or {}
    client = StockDbClient()
    client.connect()
    minutes = _MinuteBook(client, start, end)
    minutes.ensure([INDEX_CODE, "511260"])
    session_days = sorted(minutes.by_code_day.get("511260", {}))
    session_days = [d for d in session_days if start <= d <= end]
    if not session_days:
        raise RuntimeError(f"{start} ~ {end} 没有 511260 的 5 分钟线")
    qs = get_data_query_service()
    daily = await qs.get_ohlcv_batch(list(BOOK_OHLCV_CODES), "2023-06-01", end, period="daily")
    daily = {normalize_code(k): v for k, v in daily.items()}
    close_of = {code: _close_map(frame) for code, frame in daily.items()}
    calendar = sorted(close_of.get(INDEX_CODE) or close_of.get("511260") or [])
    calendar = [d for d in calendar if d <= session_days[-1]]
    if not calendar:
        raise RuntimeError("日线日历为空，无法给账户补均线")
    prior = [d for d in calendar if d < session_days[0]]
    calendar = prior[-250:] + [d for d in calendar if d >= session_days[0]]
    return IntradayContext(
        fee=float(bt.get("fee_rate", 0.0015)),
        stamp=float(bt.get("stamp_tax", 0.0005)),
        initial=float(bt.get("initial_capital", 10_000_000)),
        band=float(book_cfg.get("rebalance_band", 0.03)),
        min_util=float(book_cfg.get("min_utilization", 0.90)),
        minutes=minutes,
        session_days=session_days,
        close_of=close_of,
        calendar=calendar,
        strategy=StrategyPipeline(),
    )


async def replay(
    ctx: IntradayContext,
    *,
    risk: bool = True,
    table: Optional[RiskTable] = None,
    verbose: bool = True,
) -> dict:
    """同一批行情上重跑。table 为空时用 strategy_params.json 里的阈值。"""
    ctx.strategy.book.reset()
    book = IntradayBook(rows=table) if table is not None else IntradayBook()
    hist = _Hist(ctx.close_of)
    positions: Dict[str, SharePos] = {}
    pending = None
    pending_day = ""
    last_state = None
    cash = ctx.initial
    curve: List[float] = []
    curve_dates: List[str] = []
    all_trades: List[dict] = []
    session_set = set(ctx.session_days)
    for idx, day in enumerate(ctx.calendar):
        hist.add(day)
        if day in session_set and pending is not None:
            codes = sorted(set(pending) | set(positions))
            ctx.minutes.ensure(codes)
            prev = {code: ctx.close_of.get(code, {}).get(pending_day) for code in codes}
            prev = {code: px for code, px in prev.items() if px}
            result = simulate_day(
                book, positions, cash, pending, ctx.minutes.day(codes, day), prev, day,
                fee_rate=ctx.fee, stamp_tax=ctx.stamp, risk=risk,
            )
            positions = result.positions
            cash = result.cash
            curve.append(result.equity)
            curve_dates.append(day)
            all_trades.extend(result.trades)
            if verbose and (len(curve) == 1 or len(curve) % 20 == 0 or day == ctx.session_days[-1]):
                logger.info("%s 净值 %.4f 持仓 %s", day, result.equity / ctx.initial, sorted(positions))
        if day >= ctx.session_days[-1]:
            break
        nxt = ctx.calendar[idx + 1] if idx + 1 < len(ctx.calendar) else ""
        try:
            decision = await ctx.strategy.note_market_day(
                day,
                index_closes=[float(r["close"]) for r in hist.index_rows],
                asset_closes=hist.asset,
                held=list(positions),
                nav=curve[-1] if curve else ctx.initial,
            )
        except Exception as exc:
            logger.warning("%s 账户状态失败: %s", day, exc)
            continue
        prices = {
            code: ctx.close_of.get(code, {}).get(day) or (positions[code].cost if code in positions else None)
            for code in set(positions) | set(BOOK_OHLCV_CODES)
        }
        prices = {code: px for code, px in prices.items() if px}
        target, last_state = await _target_for(
            ctx.strategy, decision, day, positions, cash, prices, hist.index_rows, hist.asset,
            last_state, nxt in session_set, ctx.band, ctx.min_util,
        )
        if nxt in session_set:
            pending = target
            pending_day = day
    reasons: Dict[str, int] = {}
    for row in all_trades:
        if row.get("side") == "sell":
            reasons[row.get("reason") or ""] = reasons.get(row.get("reason") or "", 0) + 1
    stats = _stats(curve, curve_dates)
    return {"stats": stats, "reasons": reasons, "curve": curve, "dates": curve_dates, "trades": all_trades}


async def run(start: str, end: str, *, risk: bool = True, output: Optional[Path] = None) -> Path:
    ctx = await load_context(start, end)
    played = await replay(ctx, risk=risk)
    stats = played["stats"]
    reasons = played["reasons"]
    session_days = ctx.session_days
    out_dir = output or OUTPUT
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"trade_date": played["dates"], "equity": played["curve"]}).to_csv(out_dir / "equity.csv", index=False)
    pd.DataFrame(played["trades"]).to_csv(out_dir / "trades.csv", index=False)
    lines = [
        f"# 日内回测 {session_days[0]} ~ {session_days[-1]}",
        "",
        "5 分钟不复权，收盘价成交。信号是上一交易日收盘后的策略结果，当天 09:30 调仓。",
        "日内卖出开" if risk else "日内卖出关，只保留 09:30 调仓。",
        "立即成交，因此没有 14:00 重挂和 14:50 撤单；14:50 仍会把今天还要的标的买回。",
        "",
        f"- 收益 {stats.get('return', 0):.2%}",
        f"- 最大回撤 {stats.get('mdd', 0):.2%}",
        f"- 夏普 {stats.get('sharpe', 0):.2f}",
        f"- 期末权益 {stats.get('end', 0):,.0f}",
        "",
        "| 年份 | 收益 | 年内回撤 | 期末权益 |",
        "|---|---:|---:|---:|",
    ]
    for row in stats.get("yearly") or []:
        lines.append(f"| {row['year']} | {row['return']:.2%} | {row['mdd']:.2%} | {row['end']:,.0f} |")
    lines.append("")
    lines.append("卖出次数：" + "，".join(f"{k} {v}" for k, v in sorted(reasons.items())))
    path = out_dir / "summary.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("写出 %s", path)
    return path


def main() -> None:
    params = load_strategy_params()
    parser = argparse.ArgumentParser(description="用 stockdb 5 分钟线回测日内规则")
    parser.add_argument("--start", default="2025-01-02")
    parser.add_argument("--end", default="2026-11-18")
    parser.add_argument("--no-risk", action="store_true", help="关掉持仓/日内/盈利回撤，只留 09:30 调仓")
    parser.add_argument("--output", default="", help="结果目录，默认 script/output/intraday_5m")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    async def _go():
        await init_zstock_database()
        try:
            out = Path(args.output) if args.output else None
            await run(normalize_date(args.start), normalize_date(args.end), risk=not args.no_risk, output=out)
        finally:
            await close_zstock_database()

    asyncio.run(_go())


if __name__ == "__main__":
    main()
