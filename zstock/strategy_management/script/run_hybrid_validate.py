"""
混合仓位方案：prod / v119 / v120h-j-k × 2022,2024,2025。

    python -m zstock.strategy_management.script.run_hybrid_validate
"""

from __future__ import annotations

import asyncio
import json
import logging
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
    _setup_logging,
    _year_ranges,
)

logger = logging.getLogger(__name__)
CONFIG = PROJECT_ROOT / "zstock" / "common" / "config"

BATCH: List[Tuple[str, Path]] = [
    ("prod", CONFIG / "strategy_params.json"),
    ("v119", CONFIG / "strategy_params_v119_frozen.json"),
    ("v120h", CONFIG / "strategy_params_v120h_v119_ov_full_te.json"),
    ("v120j", CONFIG / "strategy_params_v120j_v119_ov_te_mom.json"),
    ("v120k", CONFIG / "strategy_params_v120k_te_mom_only.json"),
]
KEY_YEARS = {"2022", "2024", "2025"}


def _cagr(rows: List[Dict[str, Any]]) -> float:
    ok = [r for r in rows if not r.get("error") and r.get("total_return") is not None]
    if not ok:
        return float("nan")
    prod = 1.0
    for r in sorted(ok, key=lambda x: x["start"]):
        prod *= 1.0 + float(r["total_return"])
    return prod ** (1.0 / len(ok)) - 1.0


async def async_main() -> int:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(__file__).resolve().parent / "output" / f"hybrid_validate_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    _setup_logging(out_dir / "run.log")

    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database

    await init_zstock_database()
    end_2026 = await _latest_trade_date_in_factors()
    ranges = [(y, s, e) for y, s, e in _year_ranges(end_2026) if str(y) in KEY_YEARS]

    all_rows: List[Dict[str, Any]] = []
    by_label: Dict[str, List[Dict[str, Any]]] = {lb: [] for lb, _ in BATCH}
    n_jobs = len(BATCH) * len(ranges)
    t0 = time.time()
    job = 0

    for label, path in BATCH:
        if not path.is_file():
            logger.error("缺失 %s", path)
            return 1
        for year, start, end in ranges:
            job += 1
            seg = out_dir / f"{label}_{year}"
            seg.mkdir(parents=True, exist_ok=True)
            logger.info("[%d/%d] %s %s", job, n_jobs, label, year)
            row = await _run_one(label, start, end, path, seg)
            all_rows.append(row)
            by_label[label].append(row)

    years = sorted(KEY_YEARS)
    header = f"{'配置':<8} {'三年CAGR':>10}" + "".join(f"{y:>8}" for y in years)
    lines = [
        "混合仓位 关键年验证 (2022/2024/2025)",
        f"生成: {datetime.now():%Y-%m-%d %H:%M}",
        "",
        header,
        "-" * len(header),
    ]
    for label, _ in BATCH:
        rows = by_label[label]
        by_y = {r["start"][:4]: r for r in rows if not r.get("error")}
        cagr = _cagr(rows)
        cols = []
        for y in years:
            tr = by_y.get(y, {}).get("total_return")
            cols.append(f"{float(tr):+7.2%}" if tr is not None else "     n/a")
        lines.append(f"{label:<8} {cagr:>+9.2%}" + "".join(cols))

    text = "\n".join(lines)
    (out_dir / "hybrid_summary.txt").write_text(text, encoding="utf-8")
    (out_dir / "hybrid_results.json").write_text(
        json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("\n%s", text)
    logger.info("用时 %.1f 分钟", (time.time() - t0) / 60)
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
