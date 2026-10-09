import json
from pathlib import Path
import pandas as pd

base = Path(r"E:\TradingAgents-CN\zstock\strategy_management\script\output\v128_cap40_7y_20261008_221221")

for year in range(2020, 2027):
    seg = base / f"v128_cap40_{year}"
    c_files = list(seg.glob("backtest_curve_*.csv"))
    if c_files:
        curve = pd.read_csv(c_files[0])
        total_days = len(curve)
        pos_days = (curve["num_positions"] > 0).sum() if "num_positions" in curve.columns else -1
        zero_days = (curve["num_positions"] == 0).sum() if "num_positions" in curve.columns else -1
        print(f"{year}: total_days={total_days}, holding_days={pos_days} ({pos_days/total_days:.1%}), cash_days={zero_days} ({zero_days/total_days:.1%})")
