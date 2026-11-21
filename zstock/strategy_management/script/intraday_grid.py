"""日内阈值网格。行情只加载一次，每组阈值重跑 2025-01-02 ~ 2026-11-18。

结果写到 script/output/intraday_grid/。每组跑完就追加 results.csv。
这是样本内搜索，不能把夏普最高的一行直接当成参数。

    python -m zstock.strategy_management.script.intraday_grid
"""

from __future__ import annotations

import asyncio
import csv
import itertools
import logging
from pathlib import Path

from zstock.common.book_codes import (
    BOND,
    BOND30,
    BOND_FALLBACK,
    GOLD,
    INDUSTRY_ETFS,
    POLICY,
)
from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
from zstock.order_management.intraday import RiskRow, RiskTable
from zstock.strategy_management.script.intraday_backtest import load_context, replay

OUT = Path(__file__).resolve().parent / "output" / "intraday_grid"
START = "2025-01-02"
END = "2026-11-18"

_ETF_NAMES = {
    "159928": "消费",
    "512010": "医药",
    "512880": "证券",
    "512660": "军工",
    "512400": "有色",
    "512800": "银行",
    "512480": "半导体",
}
_BOND_NAMES = {
    BOND: "十年国债",
    BOND_FALLBACK: "五年国债",
    BOND30: "三十年国债",
    POLICY: "政金债",
}

# 债券持仓回撤、盈利激活、盈利回撤，以及黄金的盈利两档，这轮不扫。
_BOND_FIXED = {"position_dd": 0.02, "profit_activate_cny": 30000, "profit_dd": 0.02}
_GOLD_FIXED = {"profit_activate_cny": 80000, "profit_dd": 0.03}

_STOCKS = (
    {"position_dd": 0.08, "intraday_dd": 0.06, "profit_activate_cny": 100000, "profit_dd": 0.03},
    {"position_dd": 0.12, "intraday_dd": 0.10, "profit_activate_cny": 250000, "profit_dd": 0.06},
)
_ETF_POS = (0.06, 0.10)
_ETF_INTRA = (0.03, 0.06)
_ETF_ACT = (100000, 250000)
_ETF_PDD = (0.03, 0.06)
_BOND_INTRA = (0.01, 0.02)
_GOLD_POS = (0.06, 0.10)
_GOLD_INTRA = (0.02, 0.04)

_FIELDS = [
    "tag",
    "stock_position_dd", "stock_intraday_dd", "stock_profit_activate", "stock_profit_dd",
    "etf_position_dd", "etf_intraday_dd", "etf_profit_activate", "etf_profit_dd",
    "bond_intraday_dd", "gold_position_dd", "gold_intraday_dd",
    "total_return", "max_dd", "sharpe",
    "ret_2025", "mdd_2025", "ret_2026", "mdd_2026",
    "risk_sells", "rebalance_sells",
]


def _row(name: str, settle: str, spec: dict, code: str = "") -> RiskRow:
    return RiskRow(
        name=name,
        settle=settle,
        position_dd=float(spec["position_dd"]),
        intraday_dd=float(spec["intraday_dd"]),
        profit_activate_cny=float(spec["profit_activate_cny"]),
        profit_dd=float(spec["profit_dd"]),
        limit_pct=0.10,
        code=code,
    )


def make_table(stock: dict, etf: dict, bond: dict, gold: dict) -> RiskTable:
    codes = {}
    for code in INDUSTRY_ETFS:
        codes[code] = _row(_ETF_NAMES.get(code, code), "T1", etf, code)
    for code, name in _BOND_NAMES.items():
        codes[code] = _row(name, "T0", bond, code)
    codes[GOLD] = _row("黄金", "T0", gold, GOLD)
    return RiskTable(default=_row("股票", "T1", stock), codes=codes)


def _cases():
    for stock, etf_pos, etf_intra, etf_act, etf_pdd, bond_intra, gold_pos, gold_intra in itertools.product(
        _STOCKS, _ETF_POS, _ETF_INTRA, _ETF_ACT, _ETF_PDD, _BOND_INTRA, _GOLD_POS, _GOLD_INTRA,
    ):
        etf = {
            "position_dd": etf_pos,
            "intraday_dd": etf_intra,
            "profit_activate_cny": etf_act,
            "profit_dd": etf_pdd,
        }
        bond = {**_BOND_FIXED, "intraday_dd": bond_intra}
        gold = {**_GOLD_FIXED, "position_dd": gold_pos, "intraday_dd": gold_intra}
        yield stock, etf, bond, gold


def _record(tag: str, stock, etf, bond, gold, played: dict) -> dict:
    stats = played["stats"]
    yearly = {row["year"]: row for row in stats.get("yearly") or []}
    reasons = played["reasons"]
    risk_sells = sum(n for name, n in reasons.items() if name != "调仓")
    y25 = yearly.get("2025") or {}
    y26 = yearly.get("2026") or {}
    return {
        "tag": tag,
        "stock_position_dd": stock["position_dd"] if stock else "",
        "stock_intraday_dd": stock["intraday_dd"] if stock else "",
        "stock_profit_activate": stock["profit_activate_cny"] if stock else "",
        "stock_profit_dd": stock["profit_dd"] if stock else "",
        "etf_position_dd": etf["position_dd"] if etf else "",
        "etf_intraday_dd": etf["intraday_dd"] if etf else "",
        "etf_profit_activate": etf["profit_activate_cny"] if etf else "",
        "etf_profit_dd": etf["profit_dd"] if etf else "",
        "bond_intraday_dd": bond["intraday_dd"] if bond else "",
        "gold_position_dd": gold["position_dd"] if gold else "",
        "gold_intraday_dd": gold["intraday_dd"] if gold else "",
        "total_return": stats.get("return"),
        "max_dd": stats.get("mdd"),
        "sharpe": stats.get("sharpe"),
        "ret_2025": y25.get("return"),
        "mdd_2025": y25.get("mdd"),
        "ret_2026": y26.get("return"),
        "mdd_2026": y26.get("mdd"),
        "risk_sells": risk_sells,
        "rebalance_sells": reasons.get("调仓", 0),
    }


def _write_summary(rows: list) -> None:
    base = next((r for r in rows if r["tag"] == "no_risk"), None)
    lines = [
        "# 日内阈值网格 2025-01-02 ~ 2026-11-18",
        "",
        "同一套 09:30 调仓，只改卖出阈值。5 分钟不复权，收盘价立即成交。",
        "14:00 重挂和 14:50 撤单在这套成交里没有未成交委托，所以这次网格没有覆盖「挂了一天不成交」。",
        "网格是在这段行情里扫的。夏普最高的一行不能直接当参数。",
        "",
        "没扫的固定值：国债持仓回撤 2%、盈利激活 3 万、盈利回撤 2%；黄金盈利激活 8 万、盈利回撤 3%；涨跌停都是 10%。",
        "",
    ]
    if base:
        lines.append(
            f"日内卖出关掉：收益 {float(base['total_return']):.2%}，回撤 {float(base['max_dd']):.2%}，"
            f"夏普 {float(base['sharpe']):.2f}，2025 {float(base['ret_2025']):.2%}，2026 {float(base['ret_2026']):.2%}。"
        )
        lines.append("")
    ranked = [r for r in rows if r["tag"] != "no_risk" and r.get("sharpe") not in ("", None)]
    ranked.sort(key=lambda r: float(r["sharpe"]), reverse=True)
    if base and ranked:
        better = [
            r for r in ranked
            if float(r["total_return"]) > float(base["total_return"])
            and float(r["max_dd"]) > float(base["max_dd"])
        ]
        lines.append(f"共 {len(ranked)} 组阈值。收益和回撤同时好于「关掉日内卖出」的有 {len(better)} 组。")
        lines.append("")
    lines.append("| 标记 | 收益 | 回撤 | 夏普 | 2025 | 2026 | 风控卖出 | 股票持仓/日内 | ETF持仓/日内/激活/盈利回撤 | 国债日内 | 黄金持仓/日内 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---|---|---:|---|")
    show = [r for r in rows if r["tag"] == "no_risk"] + ranked
    for r in show:
        def pct(key):
            val = r.get(key)
            return "" if val in ("", None) else f"{float(val):.2%}"
        def num(key):
            val = r.get(key)
            return "" if val in ("", None) else f"{float(val):.2f}"
        lines.append(
            f"| {r['tag']} | {pct('total_return')} | {pct('max_dd')} | {num('sharpe')} | "
            f"{pct('ret_2025')} | {pct('ret_2026')} | {r['risk_sells']} | "
            f"{r['stock_position_dd']}/{r['stock_intraday_dd']} | "
            f"{r['etf_position_dd']}/{r['etf_intraday_dd']}/{r['etf_profit_activate']}/{r['etf_profit_dd']} | "
            f"{r['bond_intraday_dd']} | {r['gold_position_dd']}/{r['gold_intraday_dd']} |"
        )
    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _go() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    csv_path = OUT / "results.csv"
    await init_zstock_database()
    try:
        ctx = await load_context(START, END)
        original = ctx.strategy.score_signals
        cache = {}

        async def _cached(trade_date: str):
            if trade_date not in cache:
                cache[trade_date] = await original(trade_date)
            src = cache[trade_date]
            out = src.copy()
            out.attrs = dict(getattr(src, "attrs", {}) or {})
            return out

        ctx.strategy.score_signals = _cached
        logging.getLogger("zstock").setLevel(logging.WARNING)
        cases = list(_cases())
        rows = []
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=_FIELDS)
            writer.writeheader()
            handle.flush()
            played = await replay(ctx, risk=False, verbose=False)
            base = _record("no_risk", None, None, None, None, played)
            rows.append(base)
            writer.writerow(base)
            handle.flush()
            print(
                f"GRID 0/{len(cases)} no_risk ret={base['total_return']:.4f} sharpe={base['sharpe']:.3f}",
                flush=True,
            )
            for i, (stock, etf, bond, gold) in enumerate(cases, start=1):
                table = make_table(stock, etf, bond, gold)
                try:
                    played = await replay(ctx, risk=True, table=table, verbose=False)
                    rec = _record(f"g{i:03d}", stock, etf, bond, gold, played)
                except Exception as exc:
                    print(f"GRID {i}/{len(cases)} FAIL {exc}", flush=True)
                    continue
                rows.append(rec)
                writer.writerow(rec)
                handle.flush()
                if i == 1 or i % 10 == 0 or i == len(cases):
                    print(
                        f"GRID {i}/{len(cases)} ret={rec['total_return']:.4f} "
                        f"mdd={rec['max_dd']:.4f} sharpe={rec['sharpe']:.3f}",
                        flush=True,
                    )
                if i % 20 == 0:
                    _write_summary(rows)
        _write_summary(rows)
        print(f"GRID_DONE {len(rows)} rows -> {OUT / 'summary.md'}", flush=True)
    finally:
        await close_zstock_database()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(_go())
