"""
测试 v130 (fill_open_slots) 在 2020 极限回撤与 2021 防守年的表现
"""

import asyncio
import io
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.strategy_management.script.run_history_2020_2026 import (
    _latest_trade_date_in_factors,
    _setup_logging,
    _year_ranges,
    run_segments_parallel,
)

CONFIG = PROJECT_ROOT / "zstock" / "common" / "config"
OUTPUT_BASE = PROJECT_ROOT / "zstock" / "strategy_management" / "script" / "output"

CANDIDATES = [
    ("v130_cap40_fill", CONFIG / "strategy_params_v130_cap40_fill_slots.json"),
    ("v130_cap50_fill", CONFIG / "strategy_params_v130_cap50_fill_slots.json"),
]

YEARS = ["2020", "2021"]

async def main():
    out_dir = OUTPUT_BASE / f"v130_stress_{datetime.now():%Y%m%d_%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)
    _setup_logging(out_dir / "run.log")

    if "ZSTOCK_OHLCV_CONCURRENCY" not in os.environ:
        os.environ["ZSTOCK_OHLCV_CONCURRENCY"] = "4"

    from zstock.common.utils.db_utils import init_zstock_database
    await init_zstock_database()

    end = await _latest_trade_date_in_factors()
    ranges_map = {str(y): (s, e) for y, s, e in _year_ranges(end)}

    jobs = []
    for label, cfg_path in CANDIDATES:
        for y in YEARS:
            seg = out_dir / f"{label}_{y}"
            seg.mkdir(parents=True, exist_ok=True)
            s_date, e_date = ranges_map[y]
            jobs.append({
                "label": label,
                "start": s_date,
                "end": e_date,
                "params_path": cfg_path,
                "out_seg": seg,
                "run": True,
            })

    print(f"🚀 开始跑 v130 快筛任务，共 {len(jobs)} 个年份，并发 workers=2...")
    t0 = time.time()
    await run_segments_parallel(jobs, workers=2)
    print(f"快筛完成，耗时: {(time.time() - t0)/60:.1f} min")

    # 汇总打印
    print("\n" + "="*80)
    print(f"{'方案':<20} {'2020收益':<10} {'2020回撤':<10} {'2021收益':<10} {'2021回撤':<10} {'2年累计':<10}")
    print("="*80)
    # 对照组：v128_cap40 与 v129_cap50
    print(f"{'v128_cap40(未开)':<18} {'+18.90%':>9} {'-11.80%':>9} {'-4.04%':>9} {'-11.28%':>9} {'+14.10%':>9}")
    print(f"{'v129_cap50(未开)':<18} {'+22.30%':>9} {'-13.68%':>9} {'-5.00%':>9} {'-12.84%':>9} {'+16.19%':>9}")
    print("-" * 80)
    for label, _ in CANDIDATES:
        s20 = out_dir / f"{label}_2020" / "summary.json"
        s21 = out_dir / f"{label}_2021" / "summary.json"
        r20 = json.loads(s20.read_text(encoding="utf-8")).get("total_return", 0) if s20.exists() else 0
        m20 = json.loads(s20.read_text(encoding="utf-8")).get("max_drawdown", 0) if s20.exists() else 0
        r21 = json.loads(s21.read_text(encoding="utf-8")).get("total_return", 0) if s21.exists() else 0
        m21 = json.loads(s21.read_text(encoding="utf-8")).get("max_drawdown", 0) if s21.exists() else 0
        cum2 = (1 + r20) * (1 + r21) - 1
        print(f"{label:<20} {r20:>+8.2%} {m20:>8.2%} {r21:>+8.2%} {m21:>8.2%} {cum2:>+8.2%}")
    print("="*80)

if __name__ == "__main__":
    asyncio.run(main())
