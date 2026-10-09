"""
路径 A 快验：冲 ~10% 7y — 有条件卫星 / TE 抬升 / naked 对照 × 2020/2024/2025。

    python -m zstock.strategy_management.script.run_path_a_validate
    python -m zstock.strategy_management.script.run_path_a_validate --workers 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
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
    _latest_trade_date_in_factors,
    _run_one,
    _segment_summary_ok,
    _setup_logging,
    _year_ranges,
    run_segments_parallel,
)

logger = logging.getLogger(__name__)
CONFIG = PROJECT_ROOT / "zstock" / "common" / "config"

BATCH: List[Tuple[str, Path, str]] = [
    ("prod_v124", CONFIG / "strategy_params.json", "生产 v1.24"),
    ("v127_etf", CONFIG / "strategy_params_v127_etf_rot45.json", "闲钱 ETF 轮动 sleeve45%"),
    ("v126c", CONFIG / "strategy_params_v126c_te_boost.json", "无卫星 TE 略抬"),
    ("v123_naked", CONFIG / "strategy_params_v123_naked_select.json", "纯选股上限参照"),
]
KEY_YEARS = ("2020", "2024", "2025")


def _cagr(rows: List[Dict[str, Any]]) -> Tuple[float, float]:
    ok = [r for r in rows if not r.get("error") and r.get("total_return") is not None]
    if not ok:
        return float("nan"), float("nan")
    p = 1.0
    worst = 0.0
    for r in sorted(ok, key=lambda x: x["start"]):
        p *= 1.0 + float(r["total_return"])
        worst = min(worst, float(r.get("max_drawdown") or 0))
    n = len(ok)
    return p ** (1.0 / n) - 1.0, worst


def _year_ret(rows: List[Dict[str, Any]], year: str) -> float | None:
    for r in rows:
        if r.get("start", "").startswith(year) and r.get("total_return") is not None:
            return float(r["total_return"])
    return None


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="路径 A 三年快验")
    cpu = os.cpu_count() or 4
    p.add_argument("--workers", type=int, default=max(1, min(2, cpu // 2)))
    p.add_argument("--resume", type=Path, default=None)
    return p.parse_args()


async def async_main() -> int:
    args = _parse_args()
    if args.resume:
        out = args.resume.resolve()
    else:
        out = Path(__file__).resolve().parent / "output" / f"path_a_{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    _setup_logging(out / "run.log")

    if args.workers > 1 and "ZSTOCK_OHLCV_CONCURRENCY" not in os.environ:
        os.environ["ZSTOCK_OHLCV_CONCURRENCY"] = "4"

    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database

    await init_zstock_database()
    end = await _latest_trade_date_in_factors()
    ranges = [(y, s, e) for y, s, e in _year_ranges(end) if str(y) in KEY_YEARS]

    flat: List[Dict[str, Any]] = []
    jobs = [(a, b) for a, b, _ in BATCH if b.is_file()]
    for label, path in jobs:
        for year, start, end_y in ranges:
            seg = out / f"{label}_{year}"
            seg.mkdir(parents=True, exist_ok=True)
            sp = seg / "summary.json"
            flat.append(
                {
                    "label": label,
                    "year": year,
                    "start": start,
                    "end": end_y,
                    "params_path": path,
                    "out_seg": seg,
                    "run": not _segment_summary_ok(sp),
                }
            )

    pending = [j for j in flat if j["run"]]
    logger.info("路径 A 快验 %d 段，待跑 %d，workers=%d", len(flat), len(pending), args.workers)
    t0 = time.time()
    if pending:
        if args.workers <= 1:
            for j in pending:
                try:
                    await _run_one(
                        j["label"], j["start"], j["end"], j["params_path"], j["out_seg"]
                    )
                except Exception as e:
                    logger.error("%s %s: %s", j["label"], j["year"], e)
                    traceback.print_exc()
        else:
            await run_segments_parallel(pending, workers=args.workers)
            retry = [j for j in pending if not _segment_summary_ok(j["out_seg"] / "summary.json")]
            for j in retry:
                try:
                    await _run_one(
                        j["label"], j["start"], j["end"], j["params_path"], j["out_seg"]
                    )
                except Exception as e:
                    logger.error("补跑 %s %s: %s", j["label"], j["year"], e)

    by_label: Dict[str, List[Dict[str, Any]]] = {}
    for label, _path in jobs:
        rows: List[Dict[str, Any]] = []
        for year, start, end_y in ranges:
            sp = out / f"{label}_{year}" / "summary.json"
            if _segment_summary_ok(sp):
                rows.append(json.loads(sp.read_text(encoding="utf-8")))
            else:
                rows.append({"label": label, "start": start, "end": end_y, "error": "missing"})
        by_label[label] = rows

    lines = [
        "路径 A 快验（目标 7y ~10%, MDD >= -20%）",
        f"样本年: {', '.join(KEY_YEARS)} | 因子截止 {end}",
        "",
        f"{'配置':<12} {'3yCAGR':>8} {'worstMDD':>9} "
        + " ".join(f"{y:>7}" for y in KEY_YEARS),
        "-" * 58,
    ]
    best = ("?", float("-inf"))
    for label, _path, desc in BATCH:
        if label not in by_label:
            continue
        rows = by_label[label]
        cagr, worst = _cagr(rows)
        yr = [_year_ret(rows, y) for y in KEY_YEARS]
        lines.append(
            f"{label:<12} {cagr:>+7.2%} {worst:>+8.2%} "
            + " ".join(f"{(v if v is not None else float('nan')):>+6.2%}" for v in yr)
        )
        if cagr == cagr and cagr > best[1]:
            best = (label, cagr)

    lines.extend(
        [
            "",
            f"3y 几何最高: {best[0]} {best[1]:+.2%}",
            "进 7y 全历史: 3yCAGR>=8% 且 worstMDD>=-18% 且 2020/2025 至少一年跑赢 prod",
            f"用时 {(time.time() - t0) / 60:.1f} min",
            f"目录: {out}",
        ]
    )
    text = "\n".join(lines)
    (out / "path_a_summary.txt").write_text(text, encoding="utf-8")
    (out / "path_a_results.json").write_text(
        json.dumps([r for rs in by_label.values() for r in rs], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(text)
    await close_zstock_database()
    return 0


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
