import pandas as pd
from pathlib import Path

seg = Path(r"E:\TradingAgents-CN\zstock\strategy_management\script\output\v128_deploy_20261002_193258\v128_2020")
curves = list(seg.glob("backtest_curve_*.csv"))
if curves:
    curve = pd.read_csv(curves[0])
    mdd_idx = curve["drawdown"].idxmin()
    mdd_val = curve.loc[mdd_idx, "drawdown"]
    trough_date = curve.loc[mdd_idx, "date"]
    peak_idx = curve.loc[:mdd_idx, "equity"].idxmax()
    peak_date = curve.loc[peak_idx, "date"]
    print(f"Peak: {peak_date} ({curve.loc[peak_idx, 'equity']:.4f}) -> Trough: {trough_date} ({curve.loc[mdd_idx, 'equity']:.4f}) MDD: {mdd_val:.2%}")

    hd = pd.read_csv(list(seg.glob("backtest_holdings_*.csv"))[0])
    print("Holdings columns:", list(hd.columns))
    hd_sub = hd[(hd["trade_date"] >= peak_date) & (hd["trade_date"] <= trough_date)]
    print("\nHoldings during DD:")
    print(hd_sub.to_string())
