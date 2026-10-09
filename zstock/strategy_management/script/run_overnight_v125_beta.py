"""
v1.25 指数卫星批：目标 ~10% 年化、MDD ≤20%。

    python -m zstock.strategy_management.script.run_overnight_v125_beta
    python -m zstock.strategy_management.script.run_overnight_v125_beta --resume output/overnight_v125_*
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
    ("prod_v124", CONFIG / "strategy_params.json", "v1.24 无卫星基线"),
    ("v125_green", CONFIG / "strategy_params_v125_sat_green.json", "动量绿 卫星65%"),
    ("v125_gy", CONFIG / "strategy_params_v125_sat_gy.json", "动量绿黄 75%"),
    ("v125_bull", CONFIG / "strategy_params_v125_sat_bull.json", "动量+中性 85%"),
    ("v125_agg", CONFIG / "strategy_params_v125_sat_aggressive.json", "激进卫星92%"),
]
KEY = ("2020", "2024", "2025")


def _cagr(rows: List[Dict[str, Any]]) -> Tuple[float, float]:
    ok = [r for r in rows if not r.get("error") and r.get("total_return") is not None]
    if not ok:
        return float("nan"), float("nan")
    p = 1.0
    worst = 0.0
    for r in sorted(ok, key=lambda x: x["start"]):
        p *= 1.0 + float(r["total_return"])
        worst = min(worst, float(r.get("max_drawdown") or 0))
    return p ** (1.0 / len(ok)) - 1.0, worst


def _year_ret(rows: List[Dict[str, Any]], year: str) -> float | None:
    for r in rows:
        if r.get("start", "").startswith(year) and r.get("total_return") is not None:
            return float(r["total_return"])
    return None


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="v125 卫星夜间批")
    p.add_argument("--resume", type=Path, default=None)
    cpu = os.cpu_count() or 4
    p.add_argument(
        "--workers",
        type=int,
        default=max(1, min(3, cpu // 2)),
        help="并行进程数（内存>75%% 建议 1~2；CPU 闲置可 2~3）",
    )
    return p.parse_args()


async def async_main() -> int:
    args = _parse_args()
    if args.resume:
        out = args.resume.resolve()
    else:
        out = Path(__file__).resolve().parent / "output" / f"overnight_v125_{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    _setup_logging(out / "run.log")

    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.strategy_management.script.backtest_report import write_backtest_report

    if args.workers > 1 and "ZSTOCK_OHLCV_CONCURRENCY" not in os.environ:
        os.environ["ZSTOCK_OHLCV_CONCURRENCY"] = "4"

    await init_zstock_database()
    end = await _latest_trade_date_in_factors()
    ranges = _year_ranges(end)
    all_rows: List[Dict[str, Any]] = []
    by_label: Dict[str, List[Dict[str, Any]]] = {}

    lines = [
        "overnight_v125 指数卫星",
        f"目标: 7y CAGR ~10%, worstMDD >= -20%",
        f"截止 {end}",
        "",
        f"{'配置':<12} {'7yCAGR':>8} {'worstMDD':>9} {'2020':>7} {'2024':>7} {'2025':>7}",
        "-" * 62,
    ]
    t0 = time.time()
    jobs = [(a, b) for a, b, _ in BATCH if b.is_file()]
    flat: List[Dict[str, Any]] = []
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
    logger.info(
        "任务 %d 段，待跑 %d，workers=%d（OHLCV 并发可用 ZSTOCK_OHLCV_CONCURRENCY）",
        len(flat),
        len(pending),
        args.workers,
    )
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
            # 进程池偶发失败后无 summary：串行补跑
            retry = [j for j in pending if not _segment_summary_ok(j["out_seg"] / "summary.json")]
            if retry:
                logger.warning("并行后仍缺 %d 段，改串行补跑", len(retry))
                for j in retry:
                    if _segment_summary_ok(j["out_seg"] / "summary.json"):
                        continue
                    try:
                        await _run_one(
                            j["label"], j["start"], j["end"], j["params_path"], j["out_seg"]
                        )
                    except Exception as e:
                        logger.error("补跑 %s %s: %s", j["label"], j["year"], e)
                        traceback.print_exc()

    for label, path in jobs:
        cfg_rows: List[Dict[str, Any]] = []
        for year, start, end_y in ranges:
            sp = out / f"{label}_{year}" / "summary.json"
            if sp.is_file():
                cfg_rows.append(json.loads(sp.read_text(encoding="utf-8")))
            else:
                cfg_rows.append(
                    {"label": label, "start": start, "end": end_y, "error": "missing summary"}
                )
        all_rows.extend(cfg_rows)
        by_label[label] = cfg_rows
        ld = out / label
        ld.mkdir(parents=True, exist_ok=True)
        write_backtest_report(ld, cfg_rows, title=f"v125 {label}", subtitle=f"截止 {end}")
        cagr, worst = _cagr(cfg_rows)
        yr = [_year_ret(cfg_rows, y) for y in KEY]
        lines.append(
            f"{label:<12} {cagr:>+7.2%} {worst:>+8.2%} "
            + " ".join(f"{(v if v is not None else float('nan')):>+6.2%}" for v in yr)
        )

    best = max(
        ((lb, _cagr(rows)[0]) for lb, rows in by_label.items() if rows),
        key=lambda x: x[1] if x[1] == x[1] else -999,
        default=("?", float("nan")),
    )
    lines.extend(
        [
            "",
            f"本批最高 7yCAGR: {best[0]} {best[1]:+.2%}",
            "晋级: CAGR>=9% 且 worstMDD>=-20% 且 2022/2023 仍为正",
            f"用时 {(time.time()-t0)/60:.1f} min",
            f"目录: {out}",
        ]
    )
    text = "\n".join(lines)
    (out / "v125_summary.txt").write_text(text, encoding="utf-8")
    (out / "v125_results.json").write_text(json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8")
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
