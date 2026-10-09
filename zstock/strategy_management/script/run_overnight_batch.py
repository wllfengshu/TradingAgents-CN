"""
夜间批量：多配置 × 2020–2026 全历史 + CAGR / 最大年回撤汇总。

    .venv\\Scripts\\python.exe -m zstock.strategy_management.script.run_overnight_batch
"""

from __future__ import annotations

import argparse
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
    _latest_trade_date_in_factors,
    _run_one,
    _setup_logging,
    _year_ranges,
)

logger = logging.getLogger(__name__)

CONFIG_DIR = PROJECT_ROOT / "zstock" / "common" / "config"

BATCH_LEGACY: List[Tuple[str, Path]] = [
    ("v120", CONFIG_DIR / "strategy_params.json"),
    ("v120b", CONFIG_DIR / "strategy_params_v120b_green100.json"),
    ("v120c", CONFIG_DIR / "strategy_params_v120c_green100_m4.json"),
    ("v120d", CONFIG_DIR / "strategy_params_v120d_green100_topk6.json"),
    ("v119", CONFIG_DIR / "strategy_params_v119_frozen.json"),
]

BATCH_NIGHT: List[Tuple[str, Path]] = [
    ("prod", CONFIG_DIR / "strategy_params.json"),
    ("v119", CONFIG_DIR / "strategy_params_v119_frozen.json"),
    ("v120j", CONFIG_DIR / "strategy_params_v120j_v119_ov_te_mom.json"),
    ("v121c", CONFIG_DIR / "strategy_params_v121c_mg_m4_off.json"),
]

PROFILES = {"legacy": BATCH_LEGACY, "night": BATCH_NIGHT}


def _cagr(rows: List[Dict[str, Any]]) -> Tuple[float, float]:
    """(几何总收益, CAGR) 按自然年段数。"""
    ok = [r for r in rows if not r.get("error") and r.get("total_return") is not None]
    if not ok:
        return float("nan"), float("nan")
    prod = 1.0
    worst_mdd = 0.0
    for r in sorted(ok, key=lambda x: x["start"]):
        prod *= 1.0 + float(r["total_return"])
        mdd = float(r.get("max_drawdown") or 0.0)
        worst_mdd = min(worst_mdd, mdd)
    n = len(ok)
    cagr = prod ** (1.0 / n) - 1.0 if n > 0 and prod > 0 else float("nan")
    return prod - 1.0, cagr


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="多配置 × 2020–2026 夜间批量回测")
    p.add_argument(
        "--resume",
        type=Path,
        default=None,
        help="在已有 overnight_batch_* 目录续跑（跳过已有 summary.json 的分段）",
    )
    p.add_argument(
        "--profile",
        choices=tuple(PROFILES.keys()),
        default="night",
        help="配置组：night=prod/v119/v120j/v121c；legacy=原 v120 批",
    )
    return p.parse_args()


async def async_main() -> int:
    args = _parse_args()
    batch = PROFILES[args.profile]
    if args.resume is not None:
        out_dir = args.resume.resolve()
        if not out_dir.is_dir():
            logger.error("续跑目录不存在: %s", out_dir)
            return 1
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path(__file__).resolve().parent / "output" / f"overnight_batch_{stamp}"
        out_dir.mkdir(parents=True, exist_ok=True)
    _setup_logging(out_dir / "run.log")

    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.data_management.query_service import get_data_query_service
    from zstock.strategy_management.script.backtest_report import write_backtest_report

    await init_zstock_database()
    try:
        await get_data_query_service().ensure_indexes()
    except Exception as e:
        logger.warning("索引: %s", e)

    end_2026 = await _latest_trade_date_in_factors()
    year_ranges = _year_ranges(end_2026)

    all_rows: List[Dict[str, Any]] = []
    summary_lines = [
        f"夜间批量验证 2020–2026  profile={args.profile}",
        f"生成: {datetime.now():%Y-%m-%d %H:%M}",
        f"2026 截止: {end_2026}",
        "",
        f"{'配置':<8} {'CAGR':>8} {'累计':>10} {'最差年MDD':>10} {'2020':>8} {'2025':>8}",
        "-" * 60,
    ]

    t_all = time.time()
    job_i = 0
    n_jobs = sum(
        1
        for label, path in batch
        if path.is_file()
        for _y in year_ranges
    )

    for label, path in batch:
        if not path.is_file():
            logger.warning("跳过缺失配置 %s", path)
            continue
        cfg_rows: List[Dict[str, Any]] = []
        for year, start, end in year_ranges:
            job_i += 1
            seg = out_dir / f"{label}_{year}"
            seg.mkdir(parents=True, exist_ok=True)
            summary_path = seg / "summary.json"
            if summary_path.is_file():
                row = json.loads(summary_path.read_text(encoding="utf-8"))
                logger.info("[%d/%d] 跳过已完成 %s %s", job_i, n_jobs, label, year)
            else:
                logger.info("[%d/%d] %s %s", job_i, n_jobs, label, year)
            try:
                if not summary_path.is_file():
                    row = await _run_one(label, start, end, path, seg)
            except Exception as e:
                logger.error("失败 %s %s: %s", label, year, e)
                traceback.print_exc()
                row = {"label": label, "start": start, "end": end, "error": str(e)}
            cfg_rows.append(row)
            all_rows.append(row)

        label_dir = out_dir / label
        label_dir.mkdir(parents=True, exist_ok=True)
        write_backtest_report(
            label_dir,
            cfg_rows,
            title=f"全历史 {label}",
            subtitle=f"2026 截止 {end_2026}",
        )
        total, cagr = _cagr(cfg_rows)
        worst_mdd = min(
            (float(r["max_drawdown"]) for r in cfg_rows if r.get("max_drawdown") is not None),
            default=float("nan"),
        )
        by_year = {r["start"][:4]: r for r in cfg_rows if not r.get("error")}
        r20 = by_year.get("2020", {}).get("total_return")
        r25 = by_year.get("2025", {}).get("total_return")
        summary_lines.append(
            f"{label:<8} {cagr:>+7.2%} {total:>+9.2%} {worst_mdd:>+9.2%} "
            f"{(r20 if r20 is not None else float('nan')):>+7.2%} "
            f"{(r25 if r25 is not None else float('nan')):>+7.2%}"
        )

    summary_lines.append("")
    summary_lines.append("目标: CAGR≥10%, 任一年 MDD≥-20%")
    summary_text = "\n".join(summary_lines)
    (out_dir / "batch_summary.txt").write_text(summary_text, encoding="utf-8")
    (out_dir / "batch_all_results.json").write_text(
        json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("\n%s", summary_text)
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
