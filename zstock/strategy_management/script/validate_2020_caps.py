"""
快验 2020 年不同 cap 设置对收益和最大回撤的影响
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.strategy_management.script.run_history_2020_2026 import (
    _latest_trade_date_in_factors,
    _run_one,
    _segment_summary_ok,
    _setup_logging,
    run_segments_parallel,
)

CONFIG = PROJECT_ROOT / "zstock" / "common" / "config"
BATCH = [
    ("v128_cap35", CONFIG / "strategy_params_v128_cap35.json"),
    ("v128_cap40", CONFIG / "strategy_params_v128_cap40.json"),
    ("v128_dyn", CONFIG / "strategy_params_v128_dyn.json"),
]

async def main():
    out = Path(__file__).resolve().parent / "output" / f"cap_compare_2020_{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    _setup_logging(out / "run.log")
    if "ZSTOCK_OHLCV_CONCURRENCY" not in os.environ:
        os.environ["ZSTOCK_OHLCV_CONCURRENCY"] = "4"

    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    await init_zstock_database()

    jobs = []
    for label, path in BATCH:
        seg = out / f"{label}_2020"
        seg.mkdir(parents=True, exist_ok=True)
        jobs.append({
            "label": label,
            "start": "2020-01-01",
            "end": "2020-12-31",
            "params_path": path,
            "out_seg": seg,
            "run": True,
        })

    t0 = time.time()
    await run_segments_parallel(jobs, workers=2)

    print("\n" + "="*60)
    print("2020 年不同 Cap 方案结果对比：")
    print(f"prod_v124 基准: Return +8.80%, MDD -7.44%")
    print(f"v128 原打满版: Return +34.60%, MDD -20.25%")
    for label, _ in BATCH:
        seg = out / f"{label}_2020"
        sp = seg / "summary.json"
        if sp.exists():
            data = json.loads(sp.read_text(encoding="utf-8"))
            ret = data.get("total_return", float("nan"))
            mdd = data.get("max_drawdown", float("nan"))
            print(f"{label}: Return {ret:+.2%}, MDD {mdd:+.2%}")
    print(f"耗时: {(time.time()-t0)/60:.1f} min")
    print("="*60 + "\n")
    await close_zstock_database()

if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    asyncio.run(main())
