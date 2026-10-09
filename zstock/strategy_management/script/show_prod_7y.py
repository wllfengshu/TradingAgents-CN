import json
import pandas as pd
from pathlib import Path

p = Path(r"E:\TradingAgents-CN\zstock\strategy_management\script\output\path_a_7y_20261002_114148")
print("prod_v124 7年逐年数据：")
ret_list = []
mdd_list = []
for y in range(2020, 2027):
    f = p / f"prod_v124_{y}" / "summary.json"
    if f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))
        ret = d["total_return"]
        mdd = d["max_drawdown"]
        ret_list.append(ret)
        mdd_list.append(mdd)
        print(f"  {y}: Return {ret:+.2%}, MDD {mdd:+.2%}, Sharpe {d.get('sharpe', 0):.2f}")

cagr = (1.0 + pd.Series(ret_list)).prod() ** (1.0 / len(ret_list)) - 1.0 if ret_list else 0
print(f"7y CAGR: {cagr:+.2%}, worst MDD: {min(mdd_list):+.2%}")
