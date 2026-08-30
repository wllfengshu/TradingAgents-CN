"""
回测 vs API 信号一致性校验 CLI

用法:
  python -m zstock.strategy_management.script.signal_consistency_check --date 2024-06-03
  python -m zstock.strategy_management.script.signal_consistency_check --date 2024-06-03 --dates 2024-06-03,2024-09-15
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)


async def _run_checks(dates: list[str], include_pipeline: bool, tolerance: float) -> int:
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.strategy_management.signal_service import get_strategy_signal_service

    await init_zstock_database()
    svc = get_strategy_signal_service()
    failed = 0

    try:
        for td in dates:
            print(f"\n{'=' * 60}")
            print(f"一致性校验: {td}")
            print("=" * 60)
            try:
                result = await svc.validate_consistency(
                    td,
                    score_tolerance=tolerance,
                    include_pipeline=include_pipeline,
                )
            except ValueError as e:
                print(f"  SKIP: {e}")
                failed += 1
                continue

            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            if result["consistent"]:
                print(f"  [PASS] {td}")
            else:
                print(f"  [FAIL] {td} ({len(result['diffs'])} diffs)")
                failed += 1
    finally:
        await close_zstock_database()

    print(f"\n合计: {len(dates) - failed}/{len(dates)} 通过")
    return 1 if failed else 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    p = argparse.ArgumentParser(description="回测 vs API 信号一致性校验")
    p.add_argument("--date", type=str, help="单个交易日 YYYY-MM-DD")
    p.add_argument(
        "--dates",
        type=str,
        help="多个交易日，逗号分隔",
    )
    p.add_argument(
        "--no-pipeline",
        action="store_true",
        help="跳过 StrategyPipeline 层校验",
    )
    p.add_argument(
        "--tolerance",
        type=float,
        default=1e-6,
        help="分数/权重容差",
    )
    args = p.parse_args()

    dates: list[str] = []
    if args.date:
        dates.append(args.date.strip())
    if args.dates:
        dates.extend(d.strip() for d in args.dates.split(",") if d.strip())
    if not dates:
        p.error("请指定 --date 或 --dates")

    return asyncio.run(
        _run_checks(
            dates,
            include_pipeline=not args.no_pipeline,
            tolerance=args.tolerance,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
