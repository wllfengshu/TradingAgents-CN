"""
v1.18 vs v1.17 OOS：2020 / 2024 / 2025 预计算回测。

    cd E:\\TradingAgents-CN
    .venv\\Scripts\\python.exe -m zstock.strategy_management.script.run_v118_oos_validation
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.strategy_management.script.run_history_2020_2026 import (  # noqa: E402
    _run_one,
    _setup_logging,
    _write_report,
)

logger = logging.getLogger(__name__)

CONFIG_DIR = PROJECT_ROOT / "zstock" / "common" / "config"
PARAMS_V118 = CONFIG_DIR / "strategy_params.json"
PARAMS_V117 = CONFIG_DIR / "strategy_params_v117.json"

YEAR_RANGES: List[Tuple[int, str, str]] = [
    (2020, "2020-01-01", "2020-12-31"),
    (2024, "2024-01-01", "2024-12-31"),
    (2025, "2025-01-01", "2025-12-31"),
]


async def async_main() -> int:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(__file__).resolve().parent / "output" / f"v118_oos_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    _setup_logging(out_dir / "run.log")

    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.data_management.query_service import get_data_query_service

    await init_zstock_database()
    try:
        await get_data_query_service().ensure_indexes()
    except Exception as e:
        logger.warning("索引: %s", e)

    logger.info("输出: %s", out_dir)

    rows: List[Dict[str, Any]] = []
    jobs = []
    for year, start, end in YEAR_RANGES:
        for label, path in (("prod", PARAMS_V118), ("v117", PARAMS_V117)):
            seg = out_dir / f"{label}_{year}"
            jobs.append((label, year, start, end, path, seg))

    t_all = time.time()
    for i, (label, year, start, end, path, seg) in enumerate(jobs, 1):
        logger.info("=" * 60)
        logger.info("[%d/%d] %s %s %s → %s", i, len(jobs), label, year, start, end)
        logger.info("=" * 60)
        seg.mkdir(parents=True, exist_ok=True)
        try:
            row = await _run_one(label, start, end, path, seg)
            rows.append(row)
        except Exception as e:
            logger.error("失败 %s %s: %s", label, year, e)
            traceback.print_exc()
            rows.append({"label": label, "start": start, "end": end, "error": str(e)})

    report_lines = [
        "strategy_params vs v1.17 OOS（2020 / 2024 / 2025）",
        f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    _write_report(out_dir, rows, "2025-12-31")
    txt = (out_dir / "history_report.txt").read_text(encoding="utf-8")
    header = "strategy_params vs v1.17 OOS（2020 / 2024 / 2025）"
    (out_dir / "oos_report.txt").write_text(
        header + "\n" + txt.split("\n", 1)[-1], encoding="utf-8"
    )
    logger.info("全部完成，用时 %.1f 分钟", (time.time() - t_all) / 60)
    await close_zstock_database()
    return 0


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
