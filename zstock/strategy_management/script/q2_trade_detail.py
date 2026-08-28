"""
从季度回测 holdings + equity 重建 Q2 完整交易明细（买入/卖出/盈亏）。

用法:
    python -m zstock.strategy_management.script.q2_trade_detail
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

INITIAL_CAPITAL = 1_000_000.0
BASE = Path(__file__).parent / "output" / "quarterly_compare"
YEARS = ("2024", "2025", "2026")


def _find_holdings_csv(qdir: Path) -> Optional[Path]:
    files = sorted(qdir.glob("backtest_holdings_*.csv"))
    return files[-1] if files else None


def _find_equity_csv(qdir: Path) -> Optional[Path]:
    p = qdir / "equity_curve.csv"
    if p.exists():
        return p
    files = sorted(qdir.glob("backtest_curve_*.csv"))
    return files[-1] if files else None


def _equity_on(eq: pd.Series, date: str) -> float:
    date = str(date)[:10]
    if date in eq.index:
        return float(eq.loc[date])
    # 取最近不晚于该日的净值
    prior = eq[eq.index <= date]
    return float(prior.iloc[-1]) if len(prior) else 1.0


async def _fetch_prices_batch(codes: List[str], start: str, end: str) -> Dict[Tuple[str, str], float]:
    from zstock.data_management.query_service import get_data_query_service

    qs = get_data_query_service()
    out: Dict[Tuple[str, str], float] = {}
    batch = await qs.get_ohlcv_batch(list(set(codes)), start, end)
    for code, df in batch.items():
        code = str(code).zfill(6)
        if df is None or df.empty:
            continue
        for _, row in df.iterrows():
            d = str(row.get("trade_date", ""))[:10]
            px = float(row.get("open") or row.get("close") or 0)
            if d and px > 0:
                out[(code, d)] = px
    return out


def _reconstruct_roundtrips(
    holdings: pd.DataFrame,
    equity: pd.Series,
    year: str,
) -> List[Dict[str, Any]]:
    """从再平衡日持仓快照重建买卖回合。"""
    holdings = holdings.copy()
    holdings["trade_date"] = holdings["trade_date"].astype(str).str[:10]
    holdings["code"] = holdings["code"].astype(str).str.zfill(6)
    reb_dates = sorted(holdings["trade_date"].unique())

    open_pos: Dict[Tuple[str, str], Dict[str, Any]] = {}
    roundtrips: List[Dict[str, Any]] = []

    def _pos_key(row) -> Tuple[str, str]:
        return (str(row["code"]).zfill(6), str(row["entry_date"])[:10])

    for td in reb_dates:
        snap = holdings[holdings["trade_date"] == td]
        current_keys = {_pos_key(r) for _, r in snap.iterrows()}

        to_close = [k for k in list(open_pos.keys()) if k not in current_keys]
        for key in to_close:
            pos = open_pos.pop(key)
            pos["sell_date"] = td
            pos["exit_reason"] = "调仓卖出"
            roundtrips.append(pos)

        for _, r in snap.iterrows():
            k = _pos_key(r)
            eq = _equity_on(equity, td)
            amt = float(r["weight"]) * eq * INITIAL_CAPITAL
            if k in open_pos:
                open_pos[k]["weight"] = float(r["weight"])
                open_pos[k]["position_amount"] = amt
                open_pos[k]["last_rebalance"] = td
            else:
                open_pos[k] = {
                    "year": year,
                    "code": k[0],
                    "sector": r.get("sector_code", ""),
                    "buy_date": str(r["entry_date"])[:10],
                    "entry_price": float(r["entry_price"]),
                    "weight": float(r["weight"]),
                    "position_amount": amt,
                    "equity_at_buy": eq,
                    "first_rebalance": td,
                    "last_rebalance": td,
                    "sell_date": None,
                    "exit_reason": None,
                }

    last_td = reb_dates[-1] if reb_dates else None
    for pos in open_pos.values():
        pos["sell_date"] = last_td
        pos["exit_reason"] = "持有至季末"
        roundtrips.append(pos)

    return roundtrips


def _enrich_with_prices(rows: List[Dict[str, Any]], prices: Dict[Tuple[str, str], float]) -> List[Dict[str, Any]]:
    for r in rows:
        buy_px = prices.get((r["code"], r["buy_date"])) or r["entry_price"]
        sell_date = r["sell_date"]
        sell_px = prices.get((r["code"], sell_date)) if sell_date else buy_px
        if not sell_px or sell_px <= 0:
            sell_px = r["entry_price"]

        r["buy_price"] = round(buy_px, 4)
        r["sell_price"] = round(sell_px, 4)
        shares = r["position_amount"] / buy_px if buy_px > 0 else 0
        r["shares_approx"] = round(shares, 0)
        sell_value = shares * sell_px
        r["sell_amount"] = round(sell_value, 2)
        r["position_amount"] = round(r["position_amount"], 2)
        pnl = sell_value - r["position_amount"]
        r["pnl_amount"] = round(pnl, 2)
        r["pnl_pct"] = round((sell_px / buy_px - 1) * 100, 2) if buy_px > 0 else 0.0
        r["weighted_pnl_pct"] = round(r["pnl_pct"] * r["weight"], 2)
    return rows


def _timeline_events(year: str, equity: pd.Series, trades: pd.DataFrame) -> List[str]:
    eq = equity.copy()
    eq.index = eq.index.astype(str).str[:10]
    lines = [f"=== {year} Q2 净值时间线（选关键节点）==="]
    q2_start = f"{year}-04-01"
    q2_end = f"{year}-06-30" if year != "2026" else "2026-06-30"
    sub = eq[(eq.index >= q2_start) & (eq.index <= q2_end)]
    if sub.empty:
        return lines

    # 季度起止、峰值、谷值
    peak_d, peak_v = sub.idxmax(), float(sub.max())
    trough_d, trough_v = sub.idxmin(), float(sub.min())
    lines.append(f"  Q2初 {sub.index[0]}: 净值 {float(sub.iloc[0]):.4f} ({(float(sub.iloc[0])-1)*100:+.2f}%)")
    lines.append(f"  Q2末 {sub.index[-1]}: 净值 {float(sub.iloc[-1]):.4f} ({(float(sub.iloc[-1])-1)*100:+.2f}%)")
    lines.append(f"  峰值 {peak_d}: {peak_v:.4f} ({(peak_v-1)*100:+.2f}%)")
    lines.append(f"  谷值 {trough_d}: {trough_v:.4f} ({(trough_v-1)*100:+.2f}%)")

    # 大回撤日（日跌幅 > 3%）
    if "daily_return" not in trades.columns:
        daily = sub.pct_change().fillna(0)
    else:
        daily = sub.pct_change().fillna(0)
    big_drop = daily[daily <= -0.03]
    if len(big_drop):
        lines.append("  大回撤日（日跌≥3%）:")
        for d, v in big_drop.items():
            lines.append(f"    {d}: {v*100:+.2f}%  净值→{float(sub.loc[d]):.4f}")

    # 空仓/flat 事件
    if "risk_status" in trades.columns:
        flat = trades[trades["risk_status"].astype(str).str.contains("flat|hold_no", na=False)]
        if len(flat):
            lines.append("  空仓/无信号事件:")
            for _, r in flat.iterrows():
                lines.append(f"    {r['trade_date']}: {r['risk_status']} {r.get('risk_issues','')}")

    return lines


async def main() -> None:
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database

    await init_zstock_database()
    all_rows: List[Dict[str, Any]] = []
    pending_rows: List[Dict[str, Any]] = []
    report_lines: List[str] = [
        "Q2 交易明细报告（v1.16.0 季度独立回测，初始资金 100 万）",
        "",
    ]

    try:
        for year in YEARS:
            qdir = BASE / f"{year}_Q2"
            hpath = _find_holdings_csv(qdir)
            epath = _find_equity_csv(qdir)
            tpath = sorted(qdir.glob("backtest_trades_*.csv"))
            if not hpath or not epath:
                report_lines.append(f"⚠️ {year} Q2 数据缺失")
                continue

            holdings = pd.read_csv(hpath)
            eq_df = pd.read_csv(epath, index_col=0)
            eq_df.index = eq_df.index.astype(str).str[:10]
            eq = eq_df.iloc[:, 0] if eq_df.shape[1] == 1 else eq_df["equity"]

            trades_df = pd.read_csv(tpath[-1]) if tpath else pd.DataFrame()
            report_lines.extend(_timeline_events(year, eq, trades_df))
            report_lines.append("")

            rows = _reconstruct_roundtrips(holdings, eq, year)
            pending_rows.extend(rows)

        # 一次性拉 Q2 行情
        all_codes = [r["code"] for r in pending_rows]
        prices = await _fetch_prices_batch(all_codes, "2024-04-01", "2026-06-30")
        pending_rows = _enrich_with_prices(pending_rows, prices)
        all_rows = pending_rows

        for year in YEARS:
            rows = [r for r in all_rows if r["year"] == year]
            if not rows:
                continue
            rows.sort(key=lambda x: (x["buy_date"], x["code"]))
            report_lines.append(f"--- {year} Q2 交易明细（按买入日）---")
            report_lines.append(
                f"{'代码':<8} {'板块':<12} {'买入':<12} {'卖出':<12} "
                f"{'买入价':>8} {'卖出价':>8} {'权重':>6} {'持仓额':>10} {'盈亏额':>10} {'盈亏%':>8} {'说明'}"
            )
            for r in rows:
                report_lines.append(
                    f"{r['code']:<8} {str(r['sector'])[:10]:<12} {r['buy_date']:<12} "
                    f"{str(r['sell_date'] or '-'):<12} {r['buy_price']:>8.2f} {r['sell_price']:>8.2f} "
                    f"{r['weight']:>6.0%} {r['position_amount']:>10,.0f} {r['pnl_amount']:>10,.0f} "
                    f"{r['pnl_pct']:>7.1f}% {r['exit_reason']}"
                )
            heavy = [r for r in rows if r["weight"] >= 0.5 or r["position_amount"] >= 300_000]
            if heavy:
                report_lines.append(f"\n  【{year} Q2 重仓回合】")
                for r in sorted(heavy, key=lambda x: -x["position_amount"]):
                    report_lines.append(
                        f"  * {r['code']} {r['sector']} | 买 {r['buy_date']} @{r['buy_price']:.2f} "
                        f"-> 卖 {r['sell_date']} @{r['sell_price']:.2f} | "
                        f"持仓 {r['position_amount']:,.0f}元 盈亏 {r['pnl_amount']:+,.0f}元 ({r['pnl_pct']:+.1f}%)"
                    )
            report_lines.append("")
    finally:
        await close_zstock_database()

    out_dir = BASE
    out_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(all_rows)
    csv_path = out_dir / "q2_trade_details.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    txt_path = out_dir / "q2_trade_details_report.txt"
    txt_path.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"CSV: {csv_path}")
    print(f"Report: {txt_path}")


if __name__ == "__main__":
    asyncio.run(main())
