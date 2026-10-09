"""
2020–2026 逐年回测：strategy_params（当前 prod）vs strategy_params_v117（预计算因子）。

用法：
    cd E:\\TradingAgents-CN
    .venv\\Scripts\\python.exe -m zstock.strategy_management.script.run_history_2020_2026
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

logger = logging.getLogger(__name__)

CONFIG_DIR = PROJECT_ROOT / "zstock" / "common" / "config"
PARAMS_PROD = CONFIG_DIR / "strategy_params.json"
PARAMS_BASELINE = CONFIG_DIR / "strategy_params_v119_frozen.json"
INDEX_CODE = "399300"


def _setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S")
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    logging.getLogger("zstock.data_management").setLevel(logging.WARNING)
    logging.getLogger("zstock.factor_management").setLevel(logging.WARNING)


async def _latest_trade_date_in_factors() -> str:
    from zstock.data_management.database_service import get_database_service
    from zstock.data_management.query_service import COL_FACTOR_MARKET

    db = get_database_service().db
    dates = await db[COL_FACTOR_MARKET].distinct("trade_date")
    dates = sorted(str(d) for d in dates if d)
    return dates[-1] if dates else datetime.now().strftime("%Y-%m-%d")


def _year_ranges(end_2026: str) -> List[Tuple[int, str, str]]:
    return [
        (2020, "2020-01-01", "2020-12-31"),
        (2021, "2021-01-01", "2021-12-31"),
        (2022, "2022-01-01", "2022-12-31"),
        (2023, "2023-01-01", "2023-12-31"),
        (2024, "2024-01-01", "2024-12-31"),
        (2025, "2025-01-01", "2025-12-31"),
        (2026, "2026-01-01", end_2026),
    ]


async def _index_return(start: str, end: str) -> float:
    from zstock.data_management.query_service import get_data_query_service

    qs = get_data_query_service()
    df, _ = await qs.get_ohlcv(INDEX_CODE, start, end, period="daily")
    if df is None or df.empty or len(df) < 2:
        return float("nan")
    df = df.sort_values("trade_date")
    c0 = float(df["close"].iloc[0])
    c1 = float(df["close"].iloc[-1])
    if c0 <= 0:
        return float("nan")
    return c1 / c0 - 1.0


async def _run_one(
    label: str,
    start: str,
    end: str,
    params_path: Path,
    out_seg: Path,
) -> Dict[str, Any]:
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
    from zstock.strategy_management.script.backtester import Backtester

    with open(params_path, encoding="utf-8") as f:
        params = json.load(f)

    fp = CrossSectionStrategyPipeline(config_path=str(params_path))
    bt = Backtester(
        fee_rate=float(params.get("backtest", {}).get("fee_rate", 0.0015)),
        initial_capital=float(params.get("backtest", {}).get("initial_capital", 1e7)),
        factor_pipeline=fp,
    )
    t0 = time.time()
    result = await bt.run_real_data(
        start_date=start,
        end_date=end,
        use_precomputed_factors=True,
        strategy_config=params,
        output_dir=str(out_seg),
        save_outputs=True,
        verbose=True,
    )
    elapsed = time.time() - t0
    metrics = dict(result.metrics or {})
    idx_ret = await _index_return(start, end)
    row = {
        "label": label,
        "start": start,
        "end": end,
        "elapsed_sec": round(elapsed, 1),
        "total_return": metrics.get("total_return"),
        "annual_return": metrics.get("annual_return"),
        "max_drawdown": metrics.get("max_drawdown"),
        "sharpe": metrics.get("sharpe"),
        "win_rate": metrics.get("win_rate"),
        "index_return_hs300": idx_ret,
        "excess_vs_index": (
            (metrics.get("total_return") - idx_ret)
            if metrics.get("total_return") is not None and idx_ret == idx_ret
            else None
        ),
    }
    summary_path = out_seg / "summary.json"
    summary_path.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    with open(out_seg / "summary.txt", "w", encoding="utf-8") as f:
        f.write(result.summary())
    _release_backtest_memory(bt, fp)
    return row


def _release_backtest_memory(bt: Any, fp: Any) -> None:
    """单段回测结束后释放 OHLCV / 预计算缓存，降低批跑内存峰值。"""
    import gc

    try:
        bt._ohlcv_full = None  # noqa: SLF001
    except Exception:
        pass
    try:
        if fp is not None:
            fp._reset_precomputed()  # noqa: SLF001
    except Exception:
        pass
    gc.collect()


def _worker_run_segment(payload: Dict[str, str]) -> Dict[str, Any]:
    """多进程 worker：独立事件循环 + Mongo 连接。"""
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database

    async def _inner() -> Dict[str, Any]:
        await init_zstock_database()
        try:
            return await _run_one(
                payload["label"],
                payload["start"],
                payload["end"],
                Path(payload["params_path"]),
                Path(payload["out_seg"]),
            )
        finally:
            await close_zstock_database()

    return asyncio.run(_inner())


def _segment_summary_ok(summary_path: Path) -> bool:
    if not summary_path.is_file():
        return False
    try:
        row = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return not row.get("error") and row.get("total_return") is not None


async def run_segments_parallel(
    jobs: List[Dict[str, Any]],
    *,
    workers: int = 1,
) -> List[Dict[str, Any]]:
    """并行跑多个 (label, start, end, path, seg) 任务；workers=1 为串行。"""
    pending = [j for j in jobs if j.get("run")]
    results: List[Dict[str, Any]] = [j["row"] for j in jobs if not j.get("run")]

    if workers <= 1 or len(pending) <= 1:
        for j in pending:
            row = await _run_one(
                j["label"], j["start"], j["end"], Path(j["params_path"]), Path(j["out_seg"])
            )
            results.append(row)
        return results

    logger.info("并行回测 asyncio workers=%d 任务数=%d", workers, len(pending))
    sem = asyncio.Semaphore(workers)

    async def _one(j: Dict[str, Any]) -> Dict[str, Any]:
        async with sem:
            try:
                return await _run_one(
                    j["label"],
                    j["start"],
                    j["end"],
                    Path(j["params_path"]),
                    Path(j["out_seg"]),
                )
            except Exception as e:
                logger.error(
                    "并行任务失败 %s %s→%s: %s", j["label"], j["start"], j["end"], e
                )
                row = {
                    "label": j["label"],
                    "start": j["start"],
                    "end": j["end"],
                    "error": str(e),
                }
                err_path = Path(j["out_seg"]) / "summary.json"
                err_path.write_text(
                    json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                return row

    gathered = await asyncio.gather(*[_one(j) for j in pending], return_exceptions=True)
    for item in gathered:
        if isinstance(item, BaseException):
            results.append({"error": str(item)})
        else:
            results.append(item)
    return results


def _write_report(out_dir: Path, rows: List[Dict[str, Any]], end_2026: str) -> None:
    from zstock.strategy_management.script.backtest_report import write_backtest_report

    write_backtest_report(
        out_dir,
        rows,
        title="历史回测 2020–2026  prod vs v1.19 冻结",
        subtitle=f"2026 截止: {end_2026}",
    )
    report = (out_dir / "backtest_report.txt").read_text(encoding="utf-8")
    (out_dir / "history_report.txt").write_text(report, encoding="utf-8")
    (out_dir / "history_results.json").write_text(
        (out_dir / "backtest_results.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    logger.info("\n%s", report)


async def async_main() -> int:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(__file__).resolve().parent / "output" / f"history_2020_2026_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    _setup_logging(out_dir / "run.log")

    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.data_management.query_service import get_data_query_service

    await init_zstock_database()
    try:
        await get_data_query_service().ensure_indexes()
    except Exception as e:
        logger.warning("索引: %s", e)

    end_2026 = await _latest_trade_date_in_factors()
    logger.info("输出: %s | 2026 截止 %s", out_dir, end_2026)

    rows: List[Dict[str, Any]] = []
    jobs = []
    for year, start, end in _year_ranges(end_2026):
        for label, path in (("prod", PARAMS_PROD), ("v119", PARAMS_BASELINE)):
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
            _write_report(out_dir, rows, end_2026)
        except Exception as e:
            logger.error("失败 %s %s: %s", label, year, e)
            traceback.print_exc()
            rows.append(
                {
                    "label": label,
                    "start": start,
                    "end": end,
                    "error": str(e),
                }
            )
            _write_report(out_dir, rows, end_2026)

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
