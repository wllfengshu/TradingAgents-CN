import json
from pathlib import Path
import pandas as pd

base = Path(r"E:\TradingAgents-CN\zstock\strategy_management\script\output\v128_cap40_7y_20261008_221221")
for y in range(2020, 2027):
    p = base / f"v128_cap40_{y}" / "summary.json"
    if p.is_file():
        d = json.loads(p.read_text(encoding="utf-8"))
        curve_files = list((base / f"v128_cap40_{y}").glob("backtest_curve_*.csv"))
        mdd_date = ""
        mdd_val = 0.0
        if curve_files:
            curve = pd.read_csv(curve_files[0])
            mdd_idx = curve["drawdown"].idxmin()
            mdd_date = curve.loc[mdd_idx, "date"]
            mdd_val = curve.loc[mdd_idx, "drawdown"]
        
        trades_files = list((base / f"v128_cap40_{y}").glob("backtest_trades_*.csv"))
        n_trades = 0
        if trades_files:
            trades = pd.read_csv(trades_files[0])
            n_trades = len(trades)
        
        print(f"{y}: ret={d.get('total_return', 0):.2%}, mdd={mdd_val:.2%} (trough={mdd_date}), win_rate={d.get('win_rate', 0):.1%}, trades={n_trades}, calmar={d.get('calmar_ratio', 0):.2f}, sharpe={d.get('sharpe_ratio', 0):.2f}")
