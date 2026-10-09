import time
import json
from pathlib import Path

OUT_DIR = Path("E:/TradingAgents-CN/zstock/strategy_management/script/output/v130_7y_20261009_090129")

for step in range(30):
    summaries = list(OUT_DIR.glob("*/summary.json"))
    done_years = sorted([s.parent.name.split("_")[-1] for s in summaries])
    print(f"[{step:02d}] 完成年份 ({len(done_years)}/7): {', '.join(done_years)}")
    if len(done_years) >= 7:
        print("🎉 全部 7 年已跑完！")
        break
    time.sleep(5)
