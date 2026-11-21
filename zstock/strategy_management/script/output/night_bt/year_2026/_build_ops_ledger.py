# -*- coding: utf-8 -*-
"""One-off: build 2026 ops ledger from night_bt year_2026 outputs."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from zstock.common.book_codes import (
    BOND,
    BOND30,
    BOND_FALLBACK,
    GOLD,
    INDUSTRY_ETFS,
    POLICY,
)

base = Path(__file__).resolve().parent
trades = pd.read_csv(base / "backtest_trades_20261118_001806.csv")
holds = pd.read_csv(base / "backtest_holdings_20261118_001806.csv")
curve = pd.read_csv(base / "backtest_curve_20261118_001806.csv")

holds["code"] = holds["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
trades["trade_date"] = trades["trade_date"].astype(str)
holds["trade_date"] = holds["trade_date"].astype(str)
curve["date"] = curve["date"].astype(str)

NAME = {
    BOND: "国债ETF",
    BOND_FALLBACK: "国债ETF备",
    BOND30: "30年国债",
    GOLD: "黄金ETF",
    POLICY: "政金债",
    "159928": "消费",
    "512010": "医药",
    "512880": "证券",
    "512660": "军工",
    "512400": "有色",
    "512800": "银行",
    "512480": "半导体",
}
BONDISH = {BOND, BOND_FALLBACK, BOND30, GOLD, POLICY}
IND = set(INDUSTRY_ETFS)


def sleeve(codes: set[str]) -> str:
    if not codes:
        return "空仓"
    if codes <= BONDISH:
        if codes <= {BOND, BOND_FALLBACK}:
            return "纯债/停车"
        if GOLD in codes:
            return "防守(含黄金)"
        return "防守混合"
    if codes <= (BONDISH | IND) and (codes & IND):
        return "中性行业ETF"
    if any(c not in BONDISH | IND for c in codes):
        return "进攻股票"
    return "混合"


def fmt_port(df: pd.DataFrame | None) -> str:
    if df is None or df.empty:
        return "空仓"
    rows = []
    for _, r in df.sort_values("weight", ascending=False).iterrows():
        c = str(r["code"])
        rows.append(f"{c}({NAME.get(c, '股票')}) {float(r['weight']) * 100:.1f}%")
    return " | ".join(rows)


by = {d: g.copy() for d, g in holds.groupby("trade_date")}
prev: pd.DataFrame | None = None
events: list[dict] = []

for _, t in trades.iterrows():
    d = t["trade_date"]
    cur = by.get(d)
    if cur is None and int(t["n_holdings"]) == 0:
        cur = pd.DataFrame(columns=["code", "weight"])
    prev_map = {} if prev is None else dict(zip(prev["code"], prev["weight"].astype(float)))
    cur_map = {} if cur is None or cur.empty else dict(zip(cur["code"], cur["weight"].astype(float)))
    sells, buys, adjusts = [], [], []
    for c in sorted(set(prev_map) | set(cur_map)):
        pw, cw = float(prev_map.get(c, 0.0)), float(cur_map.get(c, 0.0))
        if abs(cw - pw) < 1e-6:
            continue
        label = f"{c}({NAME.get(c, '股票')})"
        if pw > 0 and cw <= 1e-9:
            sells.append(f"卖清 {label} {pw * 100:.1f}%")
        elif pw <= 1e-9 and cw > 0:
            buys.append(f"新买 {label} {cw * 100:.1f}%")
        else:
            adjusts.append(f"调仓 {label} {pw * 100:.1f}%→{cw * 100:.1f}%")
    cr = curve[curve["date"] == d]
    events.append(
        {
            "i": len(events) + 1,
            "date": d,
            "prev_sleeve": sleeve(set(prev_map)) if prev is not None else "-",
            "sleeve": sleeve(set(cur_map)),
            "turnover": float(t["turnover"]),
            "cost_pct": float(t["cost_pct"]),
            "n": int(t["n_holdings"]),
            "scale": float(t["position_scale"]),
            "risk": str(t["risk_status"]),
            "issues": "" if pd.isna(t["risk_issues"]) else str(t["risk_issues"]),
            "from": fmt_port(prev),
            "to": fmt_port(cur),
            "sells": "; ".join(sells) or "-",
            "buys": "; ".join(buys) or "-",
            "adjust": "; ".join(adjusts) or "-",
            "day_ret": float(cr["daily_return"].iloc[0]) if len(cr) else None,
            "equity": float(cr["equity"].iloc[0]) if len(cr) else None,
        }
    )
    prev = cur if cur is not None and not cur.empty else pd.DataFrame(columns=["code", "weight"])

(base / "ops_ledger_2026.json").write_text(
    json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
)

lines = [
    "# 2026 年每一次再平衡操作清单（year_2026 冷启动）",
    "",
    "来源: night_bt/year_2026 backtest_trades + holdings（20261118）",
    "说明: 持仓日志主要落在再平衡日；体制标签由标的集合推断（非日志内嵌 grade）。",
    "",
]
for e in events:
    lines.append(
        f"## #{e['i']:02d} {e['date']}  "
        f"`{e['prev_sleeve']}` → `{e['sleeve']}`"
    )
    lines.append(
        f"- 换手 {e['turnover']:.2%} · 成本 {e['cost_pct']*100:.3f}% · "
        f"持仓数 {e['n']} · scale {e['scale']:.2f} · risk {e['risk']}"
        + (f" ({e['issues']})" if e["issues"] else "")
    )
    if e["day_ret"] is not None:
        lines.append(f"- 当日净值收益 {e['day_ret']*100:+.2f}% · equity {e['equity']:.4f}")
    lines.append(f"- **卖**: {e['sells']}")
    lines.append(f"- **买**: {e['buys']}")
    if e["adjust"] != "-":
        lines.append(f"- **调**: {e['adjust']}")
    lines.append(f"- 前仓: {e['from']}")
    lines.append(f"- 后仓: {e['to']}")
    lines.append("")

(base / "ops_ledger_2026.md").write_text("\n".join(lines), encoding="utf-8")
print(f"events={len(events)}")
for e in events:
    print(
        f"#{e['i']:02d} {e['date']} [{e['prev_sleeve']}->{e['sleeve']}] "
        f"tov={e['turnover']:.2%} cost={e['cost_pct']*100:.3f}% "
        f"n={e['n']} ret={(e['day_ret'] or 0)*100:+.2f}%"
    )
    print(f"  SELL {e['sells']}")
    print(f"  BUY  {e['buys']}")
    if e["adjust"] != "-":
        print(f"  ADJ  {e['adjust']}")
    print(f"  TO   {e['to']}")
