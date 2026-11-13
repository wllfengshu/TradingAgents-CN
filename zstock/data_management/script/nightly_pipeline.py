"""
每晚生产流水线：stockdb 同步行情/资金流 → 全市场因子预计算写入 MongoDB。

步骤：
  1. sync_ohlcv          （stockdb → zstock_ohlcv）
  2. sync_capital_flow   （stockdb → zstock_capital_flow, period=L2_daily）
  3. precompute_factors  （写入 zstock_factor_*）

默认增量窗口：最近 15 个自然日同步数据；因子预计算最近 5 个自然日
（加载 lookback 天历史做滚动计算）。

用法：
    python -m zstock.data_management.script.nightly_pipeline
    python -m zstock.data_management.script.nightly_pipeline --sync-days 20 --factor-days 3
    python -m zstock.data_management.script.nightly_pipeline --skip-sync
    python -m zstock.data_management.script.nightly_pipeline --skip-factors

定时调度（Python，随 Web API 启动，也可单独跑）：
    python -m zstock.data_management.script.nightly_scheduler
    python -m zstock.data_management.script.nightly_scheduler --time 21:00
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=max(0, n))).strftime("%Y-%m-%d")


def _setup_logging(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"nightly_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
    for h in list(root.handlers):
        root.removeHandler(h)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    return log_path


async def _run_ohlcv_sync(start: str, end: str) -> None:
    from zstock.data_management.script import sync_ohlcv

    await sync_ohlcv.main([
        "--freq", "1d",
        "--start", start,
        "--end", end,
        "--newest-first",
    ])


async def _run_capital_flow_sync(start: str, end: str) -> int:
    from zstock.data_management.script.sync_capital_flow import sync_capital_flow_range

    return await sync_capital_flow_range(start, end)


async def _run_precompute(start: str, end: str, lookback: int) -> Dict[str, Any]:
    from zstock.common.utils import db_utils as db_module
    from zstock.data_management.query_service import get_data_query_service
    from zstock.factor_management.script.precompute_factors import FactorPrecomputeService

    await db_module.db_manager.init_mongodb()
    try:
        await get_data_query_service().ensure_indexes()
    except Exception as e:
        logger.warning("索引创建失败（非致命）: %s", e)

    service = FactorPrecomputeService()
    return await service.precompute_date_range(start, end, lookback_days=lookback)


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="每晚 stockdb 同步 + 因子预计算")
    p.add_argument("--sync-days", type=int, default=15, help="同步回看自然日，默认 15")
    p.add_argument("--factor-days", type=int, default=5, help="因子计算回看自然日，默认 5")
    p.add_argument("--lookback", type=int, default=120, help="因子滚动窗口天数，默认 120")
    p.add_argument("--start", default=None, help="覆盖同步/因子起始日 YYYY-MM-DD")
    p.add_argument("--end", default=None, help="覆盖结束日 YYYY-MM-DD，默认今天")
    p.add_argument("--skip-sync", action="store_true", help="跳过 stockdb 同步")
    p.add_argument("--skip-ohlcv", action="store_true", help="跳过日线同步")
    p.add_argument("--skip-flow", action="store_true", help="跳过资金流同步")
    p.add_argument("--skip-factors", action="store_true", help="跳过因子预计算")
    p.add_argument(
        "--log-dir",
        default=str(PROJECT_ROOT / "zstock" / "data_management" / "script" / "output" / "nightly"),
        help="日志目录",
    )
    return p.parse_args(argv)


async def run_nightly(args: argparse.Namespace) -> int:
    end = args.end or _today()
    sync_start = args.start or _days_ago(int(args.sync_days))
    factor_start = args.start or _days_ago(int(args.factor_days))
    summary: Dict[str, Any] = {
        "end": end,
        "sync_start": sync_start,
        "factor_start": factor_start,
        "steps": {},
    }
    t0 = datetime.now()
    logger.info("=" * 60)
    logger.info("每晚流水线开始  end=%s sync=%s~  factors=%s~ lookback=%s", end, sync_start, factor_start, args.lookback)
    logger.info("=" * 60)

    errors: List[str] = []

    if not args.skip_sync and not args.skip_ohlcv:
        logger.info("[1/3] sync_ohlcv (stockdb)")
        try:
            await _run_ohlcv_sync(sync_start, end)
            summary["steps"]["ohlcv"] = "ok"
        except Exception as e:
            errors.append(f"ohlcv: {e}")
            summary["steps"]["ohlcv"] = f"fail: {e}"
            logger.error("ohlcv 同步失败: %s\n%s", e, traceback.format_exc())
    else:
        summary["steps"]["ohlcv"] = "skipped"
        logger.info("[1/3] sync_ohlcv 跳过")

    if not args.skip_sync and not args.skip_flow:
        logger.info("[2/3] sync_capital_flow (stockdb)")
        try:
            n = await _run_capital_flow_sync(sync_start, end)
            summary["steps"]["capital_flow"] = {"ok": True, "written": n}
        except Exception as e:
            errors.append(f"capital_flow: {e}")
            summary["steps"]["capital_flow"] = f"fail: {e}"
            logger.error("资金流同步失败: %s\n%s", e, traceback.format_exc())
    else:
        summary["steps"]["capital_flow"] = "skipped"
        logger.info("[2/3] sync_capital_flow 跳过")

    if not args.skip_factors:
        logger.info("[3/3] precompute_factors -> MongoDB")
        try:
            result = await _run_precompute(factor_start, end, int(args.lookback))
            summary["steps"]["factors"] = result
        except Exception as e:
            errors.append(f"factors: {e}")
            summary["steps"]["factors"] = f"fail: {e}"
            logger.error("因子预计算失败: %s\n%s", e, traceback.format_exc())
    else:
        summary["steps"]["factors"] = "skipped"
        logger.info("[3/3] precompute_factors 跳过")

    elapsed = (datetime.now() - t0).total_seconds()
    summary["elapsed_sec"] = round(elapsed, 1)
    summary["ok"] = not errors
    logger.info("=" * 60)
    logger.info("每晚流水线结束  ok=%s  耗时 %.1fs  errors=%s", summary["ok"], elapsed, errors or "无")
    logger.info("摘要: %s", summary)
    logger.info("=" * 60)

    try:
        from zstock.common.utils import db_utils as db_module

        await db_module.db_manager.close_connections()
    except Exception:
        pass

    return 0 if not errors else 1


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    log_path = _setup_logging(Path(args.log_dir))
    logger.info("日志文件: %s", log_path)
    return asyncio.run(run_nightly(args))


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
