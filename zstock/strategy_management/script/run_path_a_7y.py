"""路径 A 7y 跟进：prod / v127 ETF sleeve / v126c / v123_naked。"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.strategy_management.script.run_history_2020_2026 import (  # noqa: E402
    _latest_trade_date_in_factors,
    _run_one,
    _segment_summary_ok,
    _setup_logging,
    _year_ranges,
    run_segments_parallel,
)

logger = logging.getLogger(__name__)
CONFIG = PROJECT_ROOT / "zstock" / "common" / "config"
BATCH: List[Tuple[str, Path]] = [
    ("prod_v124", CONFIG / "strategy_params.json"),
    ("v127_etf", CONFIG / "strategy_params_v127_etf_rot45.json"),
    ("v126c", CONFIG / "strategy_params_v126c_te_boost.json"),
    ("v123_naked", CONFIG / "strategy_params_v123_naked_select.json"),
]


def _cagr(rows: List[Dict[str, Any]]) -> Tuple[float, float]:
    ok = [r for r in rows if r.get("total_return") is not None and not r.get("error")]
    if not ok:
        return float("nan"), float("nan")
    p = 1.0
    worst = 0.0
    for r in sorted(ok, key=lambda x: x["start"]):
        p *= 1.0 + float(r["total_return"])
        worst = min(worst, float(r.get("max_drawdown") or 0))
    return p ** (1.0 / len(ok)) - 1.0, worst


async def async_main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--resume", type=Path, default=None)
    args = p.parse_args()

    out = (
        args.resume.resolve()
        if args.resume
        else Path(__file__).resolve().parent / "output" / f"path_a_7y_{datetime.now():%Y%m%d_%H%M%S}"
    )
    out.mkdir(parents=True, exist_ok=True)
    _setup_logging(out / "run.log")
    if args.workers > 1 and "ZSTOCK_OHLCV_CONCURRENCY" not in os.environ:
        os.environ["ZSTOCK_OHLCV_CONCURRENCY"] = "4"

    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database

    await init_zstock_database()
    end = await _latest_trade_date_in_factors()
    ranges = _year_ranges(end)
    flat = []
    for label, path in BATCH:
        if not path.is_file():
            continue
        for year, start, end_y in ranges:
            seg = out / f"{label}_{year}"
            seg.mkdir(parents=True, exist_ok=True)
            sp = seg / "summary.json"
            flat.append(
                {
                    "label": label,
                    "start": start,
                    "end": end_y,
                    "params_path": path,
                    "out_seg": seg,
                    "run": not _segment_summary_ok(sp),
                }
            )
    pending = [j for j in flat if j["run"]]
    t0 = time.time()
    logger.info("path_a 7y 待跑 %d workers=%d", len(pending), args.workers)
    if pending:
        await run_segments_parallel(pending, workers=max(1, args.workers))

    lines = [f"path A 7y 跟进 | 截止 {end}", ""]
    for label, _ in BATCH:
        rows = []
        for year, start, end_y in ranges:
            sp = out / f"{label}_{year}" / "summary.json"
            if _segment_summary_ok(sp):
                rows.append(json.loads(sp.read_text(encoding="utf-8")))
        cagr, worst = _cagr(rows)
        lines.append(f"{label:<12} 7yCAGR {cagr:+.2%}  worstMDD {worst:+.2%}")
    lines.append(f"\n用时 {(time.time()-t0)/60:.1f} min\n{out}")
    text = "\n".join(lines)
    (out / "path_a_7y_summary.txt").write_text(text, encoding="utf-8")
    print(text)
    await close_zstock_database()
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(asyncio.run(async_main()))
