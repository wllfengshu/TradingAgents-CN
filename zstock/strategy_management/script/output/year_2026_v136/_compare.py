import pandas as pd
from pathlib import Path


def summarize(label: str, d: Path) -> None:
    curve = sorted(d.glob("backtest_curve_*.csv"))[-1]
    c = pd.read_csv(curve)
    nav_col = "nav" if "nav" in c.columns else "equity"
    nav0 = float(c[nav_col].iloc[0])
    nav1 = float(c[nav_col].iloc[-1])
    ret = (nav1 / nav0 - 1.0) * 100.0
    cost_col = next((x for x in ("cost_pct", "cost", "daily_cost") if x in c.columns), None)
    cost = float(c[cost_col].sum()) * 100.0 if cost_col else float("nan")
    print(f"{label}: nav={nav1:.4f} ret={ret:+.2f}% sum_cost={cost:.2f}% col={cost_col}")


base = Path(r"e:\TradingAgents-CN\zstock\strategy_management\script\output")
for lab in ("v135", "v136"):
    summarize(lab, base / f"year_2026_{lab}")
