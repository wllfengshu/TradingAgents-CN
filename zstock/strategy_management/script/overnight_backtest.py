"""
隔夜策略回测（完整版）：预计算 → 因子测评 → 多段回测 → 双 IS 网格 → 基准对比 → 汇总

默认完整流水线（约 8~14 小时，视机器与数据量）：
  0.  预检 MongoDB / 备份 strategy_params / 写 run.log
  1.  （可选）预计算因子 2024-01-01 ~ 今日
  2.  因子覆盖审计 + 全量 IC 测评（2024 / 2025 / 2026 YTD）
  3.  一次性加载 OHLCV
  4.  当前配置回测：2024/2025/2026 全年 + H1/H2 + 分季度
  5.  沪深300 基准对比（2024/2025/2026）
  6.  三年 IS 网格（2024/2025/2026 各 80 组 wide）→ 各年 OOS 交叉验证
  7.  参数敏感性（2024/2025/2026）：再平衡 3/5/8 天
  8.  汇总报告 + 对比 CSV + checkpoint

用法：
    cd E:\\TradingAgents-CN
    .\\.venv\\Scripts\\Activate.ps1

    # 完整隔夜（推荐）
    python -m zstock.strategy_management.script.overnight_backtest

    # 因子已预计算，跳过最耗时的预计算段
    python -m zstock.strategy_management.script.overnight_backtest --skip-precompute

    # 仅回测+网格，跳过 IC 测评
    python -m zstock.strategy_management.script.overnight_backtest --skip-precompute --skip-eval

    # 断点续跑（跳过 checkpoint 中已完成步骤）
    python -m zstock.strategy_management.script.overnight_backtest --resume --output path/to/prior_run

    # 快速冒烟
    python -m zstock.strategy_management.script.overnight_backtest \\
        --year 2024 --end-date 2024-01-31 --skip-precompute --skip-eval --skip-grid
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_STRATEGY_PARAMS_PATH = PROJECT_ROOT / "zstock" / "common" / "config" / "strategy_params.json"
_CHECKPOINT_FILE = "checkpoint.json"
_MANIFEST_FILE = "run_manifest.json"

logger = logging.getLogger(__name__)

CAPITAL_DEFAULT = 1_000_000.0
FEE_DEFAULT = 0.0015
GRID_WORKERS_DEFAULT = 1 if sys.platform == "win32" else 4
IS_2024_START = "2024-01-01"
IS_2024_END = "2024-09-30"
IS_2025_START = "2025-01-02"
IS_2025_END = "2025-09-30"
IS_2026_START = "2026-01-05"
IS_2026_END = "2026-07-27"
INDEX_CODE = "399300"


# ─────────────────────────── 工具 ───────────────────────────


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _min_date(a: str, b: str) -> str:
    return a if a <= b else b


def _max_date(a: str, b: str) -> str:
    return a if a >= b else b


def _elapsed_str(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class _StepCounter:
    def __init__(self, total: int):
        self.total = total
        self.current = 0
        self.t0 = time.time()

    def next(self, title: str) -> None:
        self.current += 1
        logger.info("")
        logger.info("=" * 70)
        logger.info("[%d/%d] %s  (已用 %s)", self.current, self.total, title, _elapsed_str(time.time() - self.t0))
        logger.info("=" * 70)


def _quiet_loggers() -> None:
    for name in ("zstock", "zstock.strategy_management", "zstock.data_management", "app"):
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.getLogger("zstock.factor_management").setLevel(logging.WARNING)
    logging.getLogger("zstock.factor_management.script.precompute_factors").setLevel(logging.INFO)


def _setup_console_utf8() -> None:
    import os

    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _setup_logging(log_file: Optional[Path] = None) -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(sh)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    root.setLevel(logging.INFO)


def _default_periods(today: str) -> Dict[str, str]:
    is_2026_end = _min_date(today, IS_2026_END)
    oos_fwd_start = "2026-07-28"
    oos_fwd_end = today if today > oos_fwd_start else oos_fwd_start
    return {
        "precompute_start": "2024-01-01",
        "precompute_end": today,
        "ohlcv_start": "2024-01-01",
        "ohlcv_end": today,
        "is_2024_start": IS_2024_START,
        "is_2024_end": IS_2024_END,
        "is_2025_start": IS_2025_START,
        "is_2025_end": IS_2025_END,
        "is_2026_start": IS_2026_START,
        "is_2026_end": is_2026_end,
        "full_2024_start": "2024-01-01",
        "full_2024_end": "2024-12-31",
        "full_2025_start": "2025-01-02",
        "full_2025_end": "2025-12-31",
        "full_2026_start": "2026-01-01",
        "full_2026_end": today,
        "oos_fwd_start": oos_fwd_start,
        "oos_fwd_end": oos_fwd_end,
    }


def _periods_for_year(year: int, end_cap: str) -> Dict[str, str]:
    y = int(year)
    end = _min_date(f"{y}-12-31", end_cap)
    p = _default_periods(end)
    p["ohlcv_start"] = f"{y}-01-01"
    p["ohlcv_end"] = end
    p["precompute_start"] = f"{y}-01-01"
    p["precompute_end"] = end
    p["full_2024_start"] = f"{y}-01-01"
    p["full_2024_end"] = end
    p["full_2026_start"] = f"{y}-01-01"
    p["full_2026_end"] = end
    p["year_scope"] = y
    return p


def _load_strategy_summary() -> List[str]:
    try:
        with open(_STRATEGY_PARAMS_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return ["  (无法读取 strategy_params.json)"]
    return [
        f"  版本: {cfg.get('version', '?')}",
        f"  描述: {(cfg.get('description') or '')[:120]}",
        f"  top_k: {cfg.get('final_score', {}).get('top_k', '?')}",
        f"  rebalance: {cfg.get('backtest', {}).get('rebalance_freq', '?')} 天",
        f"  max_weight: {cfg.get('portfolio', {}).get('max_weight_per_stock', '?')}",
        f"  coop: {cfg.get('cooperative_force', {}).get('threshold_pct', '?')}",
        f"  yellow_scale: {(cfg.get('market_overlay') or {}).get('position_scale_yellow', 'default')}",
    ]


def _default_rebalance_freq() -> int:
    try:
        with open(_STRATEGY_PARAMS_PATH, "r", encoding="utf-8") as f:
            return int(json.load(f).get("backtest", {}).get("rebalance_freq", 5))
    except Exception:
        return 5


def _clear_strategy_caches() -> None:
    from zstock.strategy_management.pipeline import StrategyPipeline
    from zstock.strategy_management.risk_manager import _load_risk_limits_from_config
    from zstock.common.config import strategy_config

    StrategyPipeline._config_cache = None
    _load_risk_limits_from_config._cache = None
    strategy_config._clear_cache()


def _avg_exposure(holdings_log: List[Dict[str, Any]]) -> float:
    ws: List[float] = []
    for snap in holdings_log or []:
        hs = snap.get("holdings") or []
        ws.append(sum(float(h.get("weight", 0)) for h in hs) if hs else 0.0)
    return float(sum(ws) / len(ws)) if ws else 0.0


# ─────────────────────────── Checkpoint ───────────────────────────


def _load_checkpoint(output_dir: Path) -> Dict[str, Any]:
    p = output_dir / _CHECKPOINT_FILE
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"completed_steps": [], "results": {}}


def _save_checkpoint(output_dir: Path | str, ckpt: Dict[str, Any]) -> None:
    output_dir = Path(output_dir)
    ckpt["updated_at"] = datetime.now().isoformat()
    (output_dir / _CHECKPOINT_FILE).write_text(
        json.dumps(ckpt, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _step_done(ckpt: Dict[str, Any], step_id: str) -> bool:
    return step_id in ckpt.get("completed_steps", [])


def _mark_done(ckpt: Dict[str, Any], step_id: str, result: Any = None) -> None:
    ckpt.setdefault("completed_steps", [])
    if step_id not in ckpt["completed_steps"]:
        ckpt["completed_steps"].append(step_id)
    if result is not None:
        ckpt.setdefault("results", {})[step_id] = result
    _save_checkpoint(ckpt["output_dir"], ckpt)  # type: ignore[arg-type]


# ─────────────────────────── 任务列表 ───────────────────────────


def _clip_range(start: str, end: str, cap_end: str) -> Optional[Tuple[str, str]]:
    """将区间截断到 cap_end；若 start > cap_end 则返回 None。"""
    clipped_end = _min_date(end, cap_end)
    if start > clipped_end:
        return None
    return start, clipped_end


def _append_range(
    jobs: List[Tuple[str, str, str, str]],
    key: str,
    start: str,
    end: str,
    label: str,
    cap_end: str,
) -> None:
    clipped = _clip_range(start, end, cap_end)
    if clipped:
        jobs.append((key, clipped[0], clipped[1], label))


def _quarterly_jobs(year: int, cap_end: str) -> List[Tuple[str, str, str, str]]:
    y = year
    jobs: List[Tuple[str, str, str, str]] = []
    for key, start, end, label in [
        (f"current_{y}_q1", f"{y}-01-01", f"{y}-03-31", f"Current {y} Q1"),
        (f"current_{y}_q2", f"{y}-04-01", f"{y}-06-30", f"Current {y} Q2"),
        (f"current_{y}_q3", f"{y}-07-01", f"{y}-09-30", f"Current {y} Q3"),
        (f"current_{y}_q4", f"{y}-10-01", f"{y}-12-31", f"Current {y} Q4"),
    ]:
        _append_range(jobs, key, start, end, label, cap_end)
    return jobs


def _backtest_jobs_current(periods: Dict[str, str], *, include_quarters: bool) -> List[Tuple[str, str, str, str]]:
    if periods.get("year_scope"):
        y = periods["year_scope"]
        cap = periods["full_2024_end"]
        jobs = [(f"current_full", periods["full_2024_start"], cap, f"Current Full {y}")]
        if include_quarters:
            jobs.extend(_quarterly_jobs(y, cap))
        return jobs
    jobs: List[Tuple[str, str, str, str]] = []
    _append_range(jobs, "current_2024", periods["full_2024_start"], periods["full_2024_end"], "Current 2024", periods["full_2024_end"])
    _append_range(jobs, "current_2024_h1", "2024-01-01", "2024-06-30", "Current 2024 H1", periods["full_2024_end"])
    _append_range(jobs, "current_2024_h2", "2024-07-01", "2024-12-31", "Current 2024 H2", periods["full_2024_end"])
    _append_range(jobs, "current_2025", periods["full_2025_start"], periods["full_2025_end"], "Current 2025", periods["full_2025_end"])
    _append_range(jobs, "current_2025_h1", "2025-01-02", "2025-06-30", "Current 2025 H1", periods["full_2025_end"])
    _append_range(jobs, "current_2025_h2", "2025-07-01", "2025-12-31", "Current 2025 H2", periods["full_2025_end"])
    _append_range(jobs, "current_2026", periods["full_2026_start"], periods["full_2026_end"], "Current 2026 YTD", periods["full_2026_end"])
    _append_range(jobs, "current_2026_h1", "2026-01-01", "2026-06-30", "Current 2026 H1", periods["full_2026_end"])
    _append_range(jobs, "current_2026_h2", "2026-07-01", "2026-12-31", "Current 2026 H2", periods["full_2026_end"])
    if include_quarters:
        jobs.extend(_quarterly_jobs(2024, periods["full_2024_end"]))
        jobs.extend(_quarterly_jobs(2025, periods["full_2025_end"]))
        jobs.extend(_quarterly_jobs(2026, periods["full_2026_end"]))
    return jobs


def _sensitivity_jobs(periods: Dict[str, str]) -> List[Tuple[str, str, str, str, int]]:
    """2024/2025/2026 再平衡频率敏感性。"""
    jobs: List[Tuple[str, str, str, str, int]] = []
    for year, ystart, yend in [
        ("2024", periods["full_2024_start"], periods["full_2024_end"]),
        ("2025", periods["full_2025_start"], periods["full_2025_end"]),
        ("2026", periods["full_2026_start"], periods["full_2026_end"]),
    ]:
        clipped = _clip_range(ystart, yend, yend)
        if not clipped:
            continue
        s, e = clipped
        for reb in (3, 5, 8):
            jobs.append((f"sens_{year}_reb{reb}", s, e, f"Sensitivity {year} reb={reb}", reb))
    return jobs


def _grid_oos_jobs_2024(periods: Dict[str, str]) -> List[Tuple[str, str, str, str]]:
    return [
        ("grid24_oos_q4", "2024-10-01", "2024-12-31", "Grid24 OOS 2024 Q4"),
        ("grid24_val_2025", periods["full_2025_start"], periods["full_2025_end"], "Grid24 Val 2025"),
        ("grid24_val_2026", periods["full_2026_start"], periods["full_2026_end"], "Grid24 Val 2026 YTD"),
    ]


def _grid_oos_jobs_2025(periods: Dict[str, str]) -> List[Tuple[str, str, str, str]]:
    return [
        ("grid25_oos_q4", "2025-10-01", "2025-12-31", "Grid25 OOS 2025 Q4"),
        ("grid25_ref_2024", periods["full_2024_start"], periods["full_2024_end"], "Grid25 Ref 2024"),
        ("grid25_val_2026", periods["full_2026_start"], periods["full_2026_end"], "Grid25 Val 2026 YTD"),
    ]


def _grid_oos_jobs_2026(periods: Dict[str, str]) -> List[Tuple[str, str, str, str]]:
    jobs = [
        ("grid26_oos_2025", periods["full_2025_start"], periods["full_2025_end"], "Grid26 OOS 2025"),
        ("grid26_ref_2024", periods["full_2024_start"], periods["full_2024_end"], "Grid26 Ref 2024"),
        ("grid26_ytd_2026", periods["full_2026_start"], periods["full_2026_end"], "Grid26 2026 YTD"),
    ]
    if periods["oos_fwd_end"] > periods["oos_fwd_start"]:
        jobs.insert(2, ("grid26_fwd_h2", periods["oos_fwd_start"], periods["oos_fwd_end"], "Grid26 Fwd 2026 H2"))
    return jobs


def _benchmark_jobs(periods: Dict[str, str]) -> List[Tuple[str, str, str]]:
    if periods.get("year_scope"):
        y = periods["year_scope"]
        return [(f"bench_{y}", periods["full_2024_start"], periods["full_2024_end"])]
    return [
        ("bench_2024", periods["full_2024_start"], periods["full_2024_end"]),
        ("bench_2025", periods["full_2025_start"], periods["full_2025_end"]),
        ("bench_2026", periods["full_2026_start"], periods["full_2026_end"]),
    ]


def _eval_windows(periods: Dict[str, str]) -> List[Tuple[str, str, str]]:
    if periods.get("year_scope"):
        y = periods["year_scope"]
        return [(str(y), periods["ohlcv_start"], periods["ohlcv_end"])]
    end = periods["ohlcv_end"]
    return [
        ("2024", periods["full_2024_start"], _min_date(periods["full_2024_end"], end)),
        ("2025", periods["full_2025_start"], _min_date(periods["full_2025_end"], end)),
        ("2026_YTD", periods["full_2026_start"], _min_date(periods["full_2026_end"], end)),
    ]


def _count_steps(args: argparse.Namespace, periods: Dict[str, str]) -> int:
    n = 3  # preflight, checkpoint init, report
    if not args.skip_precompute:
        n += 1
    if not args.skip_eval:
        n += 1
    n += 1  # ohlcv
    n += len(_backtest_jobs_current(periods, include_quarters=not args.no_quarters))
    n += len(_benchmark_jobs(periods))
    if not args.skip_grid:
        n += 3  # IS grids: 2024 / 2025 / 2026
        n += len(_grid_oos_jobs_2024(periods))
        n += len(_grid_oos_jobs_2025(periods))
        n += len(_grid_oos_jobs_2026(periods))
    if not args.skip_sensitivity and not periods.get("year_scope"):
        n += len(_sensitivity_jobs(periods))
    return n


# ─────────────────────────── 核心步骤 ───────────────────────────


async def preflight_checks() -> Dict[str, Any]:
    from app.core.database import db_manager

    info: Dict[str, Any] = {"ok": True, "issues": []}
    try:
        await db_manager.mongo_db.command("ping")
        info["mongo"] = "ok"
    except Exception as e:
        info["ok"] = False
        info["issues"].append(f"MongoDB ping 失败: {e}")

    try:
        n = await db_manager.mongo_db["zstock_stock_info"].estimated_document_count()
        info["stock_info_count"] = n
        if n < 1000:
            info["issues"].append(f"stock_info 仅 {n} 条，可能未同步")
    except Exception as e:
        info["issues"].append(f"stock_info 检查失败: {e}")

    for label, path in [("strategy_params", _STRATEGY_PARAMS_PATH)]:
        if path.exists():
            info[f"{label}_exists"] = True
        else:
            info["ok"] = False
            info["issues"].append(f"缺少 {path}")

    if info["issues"]:
        for msg in info["issues"]:
            logger.warning("预检: %s", msg)
    else:
        logger.info("预检通过: MongoDB + stock_info + strategy_params")
    return info


def backup_strategy_params(output_dir: Path) -> Path:
    backup_dir = output_dir / "config_backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    dst = backup_dir / f"strategy_params_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    shutil.copy2(_STRATEGY_PARAMS_PATH, dst)
    logger.info("已备份 strategy_params → %s", dst)
    return dst


async def check_factor_coverage(start: str, end: str) -> Dict[str, Any]:
    from app.core.database import db_manager

    db = db_manager.mongo_db
    mkt = await db["zstock_factor_market"].count_documents({"trade_date": {"$gte": start, "$lte": end}})
    sec = await db["zstock_factor_sector"].count_documents({"trade_date": {"$gte": start, "$lte": end}})
    drg = await db["zstock_factor_dragon"].count_documents({"trade_date": {"$gte": start, "$lte": end}})
    frc = await db["zstock_factor_force"].count_documents({"trade_date": {"$gte": start, "$lte": end}})
    info = {"start": start, "end": end, "market_days": mkt, "sector_docs": sec, "dragon_docs": drg, "force_docs": frc}
    logger.info("因子覆盖 %s~%s: M1=%d M2=%d M3=%d M4=%d", start, end, mkt, sec, drg, frc)
    if mkt == 0:
        logger.warning("无 M1 预计算数据，建议先运行 precompute 或加 --skip-precompute 仅测已有区间")
    return info


async def check_factor_coverage_by_year(periods: Dict[str, str]) -> Dict[str, Any]:
    """按年审计 2024/2025/2026 预计算因子覆盖。"""
    years = [
        ("2024", periods["full_2024_start"], periods["full_2024_end"]),
        ("2025", periods["full_2025_start"], periods["full_2025_end"]),
        ("2026", periods["full_2026_start"], periods["full_2026_end"]),
    ]
    if periods.get("year_scope"):
        y = periods["year_scope"]
        years = [(str(y), periods["ohlcv_start"], periods["ohlcv_end"])]

    by_year: Dict[str, Any] = {}
    for label, start, end in years:
        clipped = _clip_range(start, end, end)
        if not clipped:
            continue
        s, e = clipped
        by_year[label] = await check_factor_coverage(s, e)
    return {"by_year": by_year, "start": periods["ohlcv_start"], "end": periods["ohlcv_end"]}


async def run_precompute(
    start: str, end: str, lookback: int, *, workers: Optional[int], load_workers: Optional[int], query_workers: Optional[int], resource_fraction: float,
) -> Dict[str, int]:
    from zstock.factor_management.script.precompute_factors import FactorPrecomputeService
    from zstock.data_management.query_service import get_data_query_service

    await get_data_query_service().ensure_indexes()
    svc = FactorPrecomputeService(
        compute_workers=workers,
        load_workers=load_workers,
        query_workers=query_workers,
        resource_fraction=resource_fraction,
    )
    logger.info("预计算 %s → %s (lookback=%d)", start, end, lookback)
    return await svc.precompute_date_range(start, end, lookback_days=lookback)


async def run_factor_eval(start: str, end: str, output_dir: Path, label: str, *, plot: bool, period: int) -> Optional[str]:
    from zstock.factor_management.script.因子测评.factor_evaluation import FactorEvaluationPipeline

    eval_dir = output_dir / "factor_eval" / label
    eval_dir.mkdir(parents=True, exist_ok=True)
    logger.info("因子测评 [%s]: %s → %s", label, start, end)
    pipeline = FactorEvaluationPipeline(
        start_date=start, end_date=end, period=period, n_quantiles=5,
        conditional=True, top_sectors=None, invert_negative=True, plot=plot,
        output_dir=str(eval_dir), decay_max=0, layer=None, field=None,
    )
    await pipeline.run()
    summary = eval_dir / "summary.csv"
    return str(summary) if summary.exists() else None


async def run_backtest_current(
    provider: Callable[[str], Dict[str, pd.DataFrame]],
    start: str, end: str, capital: float, fee: float, rebalance_freq: int,
    output_dir: Path, label: str,
) -> Dict[str, Any]:
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
    from zstock.strategy_management.script.backtester import Backtester

    _clear_strategy_caches()
    seg_dir = output_dir / "segments" / label
    seg_dir.mkdir(parents=True, exist_ok=True)
    logger.info("%s: %s → %s (reb=%d)", label, start, end, rebalance_freq)

    bt = Backtester(fee_rate=fee, initial_capital=capital, factor_pipeline=CrossSectionStrategyPipeline())
    try:
        result = await bt.run(
            start_date=start, end_date=end, ohlcv_provider=provider,
            rebalance_freq=rebalance_freq, use_precomputed_factors=True, verbose=False,
        )
    except Exception as e:
        logger.error("回测失败: %s", e)
        return {"status": f"failed:{type(e).__name__}", "error": str(e), "label": label, "start": start, "end": end}

    chart = result.plot(output_path=str(seg_dir / "equity.png"), title=f"{label} {start}~{end}")
    result.export_csv(str(seg_dir))
    (seg_dir / "summary.txt").write_text(result.summary(), encoding="utf-8")
    m = result.metrics
    row = {
        "status": "success", "label": label, "start": start, "end": end,
        "total_return": float(m.get("total_return", 0)), "total_return_gross": float(m.get("total_return_gross", 0)),
        "annualized_return": float(m.get("annualized_return", 0)), "sharpe": float(m.get("sharpe", 0)),
        "calmar": float(m.get("calmar", 0)), "max_drawdown": float(m.get("max_drawdown", 0)),
        "avg_turnover": float(m.get("avg_turnover", 0)), "total_cost": float(m.get("total_cost", 0)),
        "rebalance_count": int(m.get("rebalance_count", 0)), "avg_exposure": _avg_exposure(result.holdings_log),
        "final_equity": float(result.equity_curve.iloc[-1]) if len(result.equity_curve) else 1.0,
        "chart": chart, "output_dir": str(seg_dir),
    }
    logger.info(
        "  ret=%.2f%% gross=%.2f%% sharpe=%.3f mdd=%.2f%% exp=%.1f%% cost=%.2f%%",
        row["total_return"] * 100, row["total_return_gross"] * 100, row["sharpe"],
        row["max_drawdown"] * 100, row["avg_exposure"] * 100, row["total_cost"] * 100,
    )
    return row


async def run_backtest_grid(optimizer, params: Dict[str, Any], provider, start: str, end: str, capital: float, fee: float, label: str) -> Dict[str, Any]:
    logger.info("%s (grid): %s → %s", label, start, end)
    row = await optimizer.run_one(params, provider, start, end, capital, fee)
    row["label"] = label
    row["start"] = start
    row["end"] = end
    if row.get("status") == "success":
        logger.info("  ret=%.2f%% sharpe=%.3f mdd=%.2f%% obj=%.4f",
                    row.get("total_return", 0) * 100, row.get("sharpe", 0),
                    row.get("max_drawdown", 0) * 100, row.get("objective", 0))
    else:
        logger.warning("  失败: %s", row.get("status"))
    return row


async def compute_benchmark(start: str, end: str) -> Dict[str, Any]:
    """沪深300 买入持有收益（用于对比）。"""
    from zstock.data_management.query_service import get_data_query_service

    qs = get_data_query_service()
    try:
        df, _ = await qs.get_ohlcv(INDEX_CODE, start, end, period="daily")
    except Exception as e:
        return {"status": "failed", "error": str(e), "label": f"benchmark {start}~{end}"}
    if df is None or df.empty or "close" not in df.columns:
        return {"status": "failed", "error": "empty index ohlcv"}
    df = df.sort_values("trade_date")
    c0 = float(df["close"].iloc[0])
    c1 = float(df["close"].iloc[-1])
    if c0 <= 0:
        return {"status": "failed", "error": "invalid close"}
    total_return = c1 / c0 - 1.0
    daily = df["close"].pct_change().dropna()
    sharpe = float(daily.mean() / daily.std() * (252 ** 0.5)) if daily.std() > 0 else 0.0
    eq = df["close"] / c0
    dd = (eq / eq.cummax() - 1).min()
    return {
        "status": "success", "index": INDEX_CODE, "start": start, "end": end,
        "total_return": total_return, "sharpe": sharpe, "max_drawdown": float(dd),
        "label": f"HS300 {start}~{end}",
    }


def _top_factors(summary_path: str, top_n: int = 8) -> List[str]:
    try:
        df = pd.read_csv(summary_path)
        if df.empty or "Total_Score" not in df.columns:
            return []
        cols = [c for c in ("layer", "field", "Total_Score", "Grade") if c in df.columns]
        return [
            f"    {r['layer']}.{r['field']} score={r['Total_Score']:.1f} ({r.get('Grade', '')})"
            for _, r in df.nlargest(top_n, "Total_Score")[cols].iterrows()
        ]
    except Exception as e:
        return [f"    (读取失败: {e})"]


def _serialize_results(all_results: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in all_results.items():
        if isinstance(v, dict):
            out[k] = {kk: vv for kk, vv in v.items() if isinstance(vv, (str, int, float, bool, type(None)))}
        elif isinstance(v, list):
            out[k] = v
        else:
            out[k] = str(v)
    return out


def generate_report(all_results: Dict[str, Any], periods: Dict[str, str], manifest: Dict[str, Any]) -> str:
    lines = [
        "", "=" * 100, "隔夜策略回测完整报告",
        f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}  数据截止: {periods.get('ohlcv_end')}",
        f"总耗时: {manifest.get('elapsed', '?')}", "=" * 100, "",
        "strategy_params.json:", *_load_strategy_summary(), "",
        "── 策略回测 ──",
        f"{'实验':<30} {'收益':>8} {'毛收益':>8} {'夏普':>7} {'回撤':>8} {'仓位':>6} {'成本':>6} {'状态':>6}",
        "─" * 100,
    ]

    skip_keys = {
        "factor_coverage", "preflight", "precompute_stats", "eval_summaries",
        "best_params_2024", "best_params_2025", "best_params_2026",
        "is_main_2024", "is_main_2025", "is_main_2026",
        "benchmarks", "manifest",
    }
    for key in sorted(all_results.keys()):
        if key in skip_keys or key.startswith("bench_"):
            continue
        r = all_results.get(key)
        if not isinstance(r, dict):
            continue
        label = (r.get("label") or key)[:30]
        if r.get("status") != "success":
            lines.append(f"{label:<30} {'—':>8} {'—':>8} {'—':>7} {'—':>8} {'—':>6} {'—':>6} {r.get('status', '?')[:6]:>6}")
            continue
        lines.append(
            f"{label:<30} {r.get('total_return', 0)*100:>7.2f}% {r.get('total_return_gross', r.get('total_return',0))*100:>7.2f}% "
            f"{r.get('sharpe', 0):>7.3f} {r.get('max_drawdown', 0)*100:>7.2f}% "
            f"{r.get('avg_exposure', 0)*100:>5.1f}% {r.get('total_cost', 0)*100:>5.2f}% {'OK':>6}"
        )

    benches = all_results.get("benchmarks") or {}
    if benches:
        lines += ["", "── 沪深300 基准 ──"]
        for k, b in benches.items():
            if b.get("status") == "success":
                lines.append(f"  {b.get('label', k)}: 收益 {b['total_return']*100:.2f}%  夏普 {b.get('sharpe', 0):.3f}  MDD {b.get('max_drawdown', 0)*100:.2f}%")

    # 策略 vs 基准（全年段）
    for year, bk, sk in [
        ("2024", "bench_2024", "current_2024"),
        ("2025", "bench_2025", "current_2025"),
        ("2026", "bench_2026", "current_2026"),
    ]:
        b = benches.get(bk, {})
        s = all_results.get(sk, {})
        if b.get("status") == "success" and s.get("status") == "success":
            alpha = s["total_return"] - b["total_return"]
            lines.append(f"  {year} 超额(Alpha): {alpha*100:+.2f}%  (策略 {s['total_return']*100:.2f}% vs 基准 {b['total_return']*100:.2f}%)")

    cov = all_results.get("factor_coverage")
    if cov:
        by_year = cov.get("by_year") or {}
        lines += ["", "── 因子覆盖（按年）──"]
        if by_year:
            for y, info in sorted(by_year.items()):
                lines.append(
                    f"  {y} ({info.get('start')}~{info.get('end')}): "
                    f"M1={info.get('market_days')} M2={info.get('sector_docs')} "
                    f"M3={info.get('dragon_docs')} M4={info.get('force_docs')}"
                )
        else:
            lines += [
                f"  区间: {cov.get('start')} ~ {cov.get('end')}",
                f"  M1={cov.get('market_days')} M2={cov.get('sector_docs')} "
                f"M3={cov.get('dragon_docs')} M4={cov.get('force_docs')}",
            ]

    pre = all_results.get("precompute_stats")
    if pre:
        lines += ["", "── 预计算 ──", f"  market={pre.get('market')} sector={pre.get('sector')} dragon={pre.get('dragon')} force={pre.get('force')}"]

    evals = all_results.get("eval_summaries") or {}
    if evals:
        lines += ["", "── 因子测评 Top8 ──"]
        for lbl, path in evals.items():
            lines.append(f"  [{lbl}]")
            lines.extend(_top_factors(path))

    for tag, is_key, oos_key in [
        ("2024 IS→Q4", "is_main_2024", "grid24_oos_q4"),
        ("2025 IS→Q4", "is_main_2025", "grid25_oos_q4"),
        ("2026 IS→2025", "is_main_2026", "grid26_oos_2025"),
    ]:
        is_r, oos_r = all_results.get(is_key, {}), all_results.get(oos_key, {})
        if is_r.get("status") == "success" and oos_r.get("status") == "success":
            is_s, oos_s = is_r.get("sharpe", 0), oos_r.get("sharpe", 0)
            lines += ["", f"── 过拟合诊断 {tag} ──", f"  IS Sharpe={is_s:.3f}  OOS Sharpe={oos_s:.3f}  IS ret={is_r.get('total_return',0)*100:.2f}%  OOS ret={oos_r.get('total_return',0)*100:.2f}%"]

    for tag, pk in [("2024 IS", "best_params_2024"), ("2025 IS", "best_params_2025"), ("2026 IS", "best_params_2026")]:
        bp = all_results.get(pk)
        if bp:
            lines += ["", f"── 网格最优参数 {tag} ──"]
            for k, v in bp.items():
                lines.append(f"  {k}: {v}")

    sens = {k: v for k, v in all_results.items() if k.startswith("sens_") and isinstance(v, dict)}
    if sens:
        lines += ["", "── 再平衡敏感性 (2024/2025/2026) ──"]
        for k in sorted(sens.keys()):
            r = sens[k]
            if r.get("status") == "success":
                lines.append(f"  {r.get('label', k)}: ret={r['total_return']*100:.2f}% sharpe={r.get('sharpe', 0):.3f}")

    lines += [
        "", "── 输出目录说明 ──",
        "  segments/     各段净值图 + CSV + summary",
        "  factor_eval/  IC 测评 summary.csv",
        "  is_grid_2024/ is_grid_2025/ is_grid_2026/  网格搜索结果",
        "  config_backup/  运行前 strategy_params 快照",
        "  run.log         完整日志",
        "", "=" * 100, "",
    ]
    return "\n".join(lines)


def _results_to_comparison_csv(all_results: Dict[str, Any], path: Path) -> None:
    rows = []
    for k, r in sorted(all_results.items()):
        if not isinstance(r, dict) or r.get("status") != "success":
            continue
        if k in ("factor_coverage", "preflight", "benchmarks", "eval_summaries"):
            continue
        rows.append({
            "key": k,
            "label": r.get("label", k),
            "start": r.get("start", ""),
            "end": r.get("end", ""),
            "total_return": r.get("total_return"),
            "total_return_gross": r.get("total_return_gross"),
            "annualized_return": r.get("annualized_return"),
            "sharpe": r.get("sharpe"),
            "calmar": r.get("calmar"),
            "max_drawdown": r.get("max_drawdown"),
            "avg_exposure": r.get("avg_exposure"),
            "avg_turnover": r.get("avg_turnover"),
            "total_cost": r.get("total_cost"),
            "rebalance_count": r.get("rebalance_count"),
            "final_equity": r.get("final_equity"),
        })
    if rows:
        pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


# ─────────────────────────── 主流程 ───────────────────────────


def build_parser() -> argparse.ArgumentParser:
    today = _today_str()
    p = argparse.ArgumentParser(description="zstock 隔夜策略回测（完整版）")
    p.add_argument("--skip-precompute", action="store_true", help="跳过因子预计算")
    p.add_argument("--skip-eval", action="store_true", help="跳过 IC 因子测评")
    p.add_argument("--skip-grid", action="store_true", help="跳过三年 IS 网格搜索（2024/2025/2026）")
    p.add_argument("--skip-sensitivity", action="store_true", help="跳过再平衡敏感性")
    p.add_argument("--no-quarters", action="store_true", help="跳过分季度回测（加速）")
    p.add_argument("--year", type=int, default=None, help="快捷：仅跑指定年份")
    p.add_argument("--end-date", default=today, help=f"全局结束日期（默认 {today}）")
    p.add_argument("--capital", type=float, default=CAPITAL_DEFAULT)
    p.add_argument("--fee", type=float, default=FEE_DEFAULT)
    p.add_argument("--rebalance", type=int, default=None, help="默认读 strategy_params")
    p.add_argument("--lookback", type=int, default=120, help="预计算回看天数")
    p.add_argument("--resource-fraction", type=float, default=0.5, help="资源占用比例")
    p.add_argument("--workers", type=int, default=None, help="预计算进程数")
    p.add_argument("--load-workers", type=int, default=None)
    p.add_argument("--query-workers", type=int, default=None)
    p.add_argument("--max-combinations", type=int, default=80, help="每个 IS 网格组合数")
    p.add_argument("--space", default="wide", choices=["core", "wide", "full"])
    p.add_argument(
        "--grid-workers",
        type=int,
        default=GRID_WORKERS_DEFAULT,
        help=f"网格搜索进程池（Windows 默认 {GRID_WORKERS_DEFAULT}，Linux 可设 4+）",
    )
    p.add_argument("--eval-period", type=int, default=5, help="因子测评预测周期")
    p.add_argument("--plot-eval", action="store_true", help="因子测评保存分析图（更慢）")
    p.add_argument("--apply", action="store_true", help="将 2026 IS 最优参数写入 strategy_params（会先备份）")
    p.add_argument("--output", default=None, help="输出目录")
    p.add_argument("--resume", action="store_true", help="断点续跑（需指定同一 --output 目录）")
    return p


async def async_main(args: argparse.Namespace) -> int:
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.factor_management.script.网格搜索.grid_search_real import RealGridSearchOptimizer, load_ohlcv
    from zstock.strategy_management.script.backtester import make_ohlcv_provider_from_dict

    t_start = time.time()
    today = args.end_date

    if args.year:
        periods = _periods_for_year(args.year, today)
    else:
        periods = _default_periods(today)
        periods["full_2026_end"] = _min_date(periods["full_2026_end"], today)
        periods["ohlcv_end"] = today
        periods["precompute_end"] = today
        periods["oos_fwd_end"] = today
        periods["is_2026_end"] = _min_date(periods["is_2026_end"], today)

    output_dir = Path(args.output) if args.output else Path(__file__).resolve().parent / "output" / f"overnight_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    output_dir.mkdir(parents=True, exist_ok=True)
    _setup_logging(output_dir / "run.log")

    rebalance_freq = args.rebalance if args.rebalance is not None else _default_rebalance_freq()
    ckpt = _load_checkpoint(output_dir) if args.resume else {"completed_steps": [], "results": {}}
    ckpt["output_dir"] = str(output_dir)
    all_results: Dict[str, Any] = dict(ckpt.get("results") or {})

    logger.info("输出目录: %s", output_dir)
    logger.info(
        "覆盖区间: OHLCV %s→%s | 回测 2024/2025/2026 | IC测评 2024/2025/2026",
        periods["ohlcv_start"], periods["ohlcv_end"],
    )
    logger.info("resume=%s  rebalance=%d  max_combinations=%d  space=%s  grid_workers=%d", args.resume, rebalance_freq, args.max_combinations, args.space, args.grid_workers)
    if sys.platform == "win32" and args.grid_workers > 1:
        logger.info("Windows 多进程网格: OHLCV 经磁盘缓存加载；若仍报错请加 --grid-workers 1")
    steps = _StepCounter(_count_steps(args, periods))

    try:
        await init_zstock_database()
    except Exception as e:
        logger.warning("init_zstock_database 失败: %s", e)
        from app.core.database import db_manager
        await db_manager.init_mongodb()

    try:
        # ── 0. 预检 + 备份 ──
        step_id = "preflight"
        if not _step_done(ckpt, step_id):
            steps.next("预检 + 备份 strategy_params")
            all_results["preflight"] = await preflight_checks()
            backup_strategy_params(output_dir)
            _mark_done(ckpt, step_id, all_results["preflight"])
        else:
            logger.info("跳过已完成: %s", step_id)

        # ── 1. 预计算 ──
        if not args.skip_precompute:
            step_id = "precompute"
            if not _step_done(ckpt, step_id):
                steps.next(f"预计算因子 {periods['precompute_start']} → {periods['precompute_end']}")
                all_results["precompute_stats"] = await run_precompute(
                    periods["precompute_start"], periods["precompute_end"], args.lookback,
                    workers=args.workers, load_workers=args.load_workers,
                    query_workers=args.query_workers, resource_fraction=args.resource_fraction,
                )
                _mark_done(ckpt, step_id, all_results["precompute_stats"])
            else:
                logger.info("跳过已完成: %s", step_id)

        # ── 2. 覆盖检查 ──
        step_id = "factor_coverage"
        if not _step_done(ckpt, step_id):
            steps.next("因子覆盖审计（2024 / 2025 / 2026）")
            all_results["factor_coverage"] = await check_factor_coverage_by_year(periods)
            _mark_done(ckpt, step_id, all_results["factor_coverage"])
        else:
            all_results["factor_coverage"] = ckpt["results"].get(step_id, all_results.get("factor_coverage"))

        # ── 3. 因子测评 ──
        if not args.skip_eval:
            step_id = "factor_eval"
            if not _step_done(ckpt, step_id):
                windows = _eval_windows(periods)
                steps.next("因子 IC 测评: " + " / ".join(w[0] for w in windows))
                eval_summaries: Dict[str, str] = {}
                for label, estart, eend in windows:
                    sp = await run_factor_eval(estart, eend, output_dir, label, plot=args.plot_eval, period=args.eval_period)
                    if sp:
                        eval_summaries[label] = sp
                all_results["eval_summaries"] = eval_summaries
                _mark_done(ckpt, step_id, eval_summaries)
            else:
                all_results["eval_summaries"] = ckpt["results"].get(step_id, {})

        # ── 4. OHLCV ──
        ohlcv: dict = {}
        step_id = "ohlcv"
        if not _step_done(ckpt, step_id):
            steps.next(f"加载 OHLCV {periods['ohlcv_start']} → {periods['ohlcv_end']}")
            ohlcv = await load_ohlcv(periods["ohlcv_start"], periods["ohlcv_end"])
            if not ohlcv:
                logger.error("无 OHLCV，退出")
                return 1
            ckpt["ohlcv_count"] = len(ohlcv)
            _mark_done(ckpt, step_id, {"count": len(ohlcv)})
        else:
            logger.info("跳过已完成: %s（重新加载 OHLCV）", step_id)
            steps.next(f"重新加载 OHLCV {periods['ohlcv_start']} → {periods['ohlcv_end']}")
            ohlcv = await load_ohlcv(periods["ohlcv_start"], periods["ohlcv_end"])
        provider = make_ohlcv_provider_from_dict(ohlcv)
        logger.info("OHLCV provider: %d 只", len(ohlcv))

        # ── 5. 当前配置多段回测 ──
        for key, bstart, bend, blabel in _backtest_jobs_current(periods, include_quarters=not args.no_quarters):
            step_id = f"bt_{key}"
            if _step_done(ckpt, step_id):
                all_results[key] = ckpt["results"].get(step_id, all_results.get(key))
                continue
            steps.next(f"回测 · {blabel}")
            all_results[key] = await run_backtest_current(provider, bstart, bend, args.capital, args.fee, rebalance_freq, output_dir, key)
            _mark_done(ckpt, step_id, _serialize_results({key: all_results[key]}).get(key))

        # ── 6. 基准 ──
        benchmarks: Dict[str, Any] = dict(all_results.get("benchmarks") or {})
        for key, bstart, bend in _benchmark_jobs(periods):
            step_id = f"bench_{key}"
            if _step_done(ckpt, step_id):
                benchmarks[key] = ckpt["results"].get(step_id, benchmarks.get(key))
                continue
            steps.next(f"基准 · 沪深300 {bstart} → {bend}")
            benchmarks[key] = await compute_benchmark(bstart, bend)
            if benchmarks[key].get("status") == "success":
                logger.info("  基准收益 %.2f%%", benchmarks[key]["total_return"] * 100)
            _mark_done(ckpt, step_id, benchmarks[key])
        all_results["benchmarks"] = benchmarks

        # ── 7. 三年 IS 网格（2024 / 2025 / 2026）──
        if not args.skip_grid and not periods.get("year_scope"):
            grid_specs = [
                ("2024", "grid_is_2024", periods["is_2024_start"], periods["is_2024_end"], "is_grid_2024", _grid_oos_jobs_2024),
                ("2025", "grid_is_2025", periods["is_2025_start"], periods["is_2025_end"], "is_grid_2025", _grid_oos_jobs_2025),
                ("2026", "grid_is_2026", periods["is_2026_start"], periods["is_2026_end"], "is_grid_2026", _grid_oos_jobs_2026),
            ]
            for year_tag, step_id, is_start, is_end, grid_dir, oos_fn in grid_specs:
                if _step_done(ckpt, step_id):
                    logger.info("跳过已完成: %s", step_id)
                    continue
                steps.next(f"IS 网格 {year_tag} {is_start}→{is_end} ({args.max_combinations}组)")
                optimizer = RealGridSearchOptimizer(output_dir=output_dir / grid_dir)
                is_df = await optimizer.run_grid_search(
                    provider, is_start, is_end,
                    args.capital, args.fee, args.max_combinations, args.space, 42, args.grid_workers, ohlcv,
                )
                optimizer.save_results(is_df)
                ok = is_df[is_df["status"] == "success"]
                if ok.empty:
                    logger.error("IS 网格 %s 无成功组合，跳过 OOS", year_tag)
                    _mark_done(ckpt, step_id, {"n_success": 0})
                    continue
                best = ok.iloc[0].to_dict()
                best_params = {k: best[k] for k in optimizer.baseline_params() if k in best}
                best_params["weight_coop"] = best.get("weight_coop", 0)
                all_results[f"is_main_{year_tag}"] = best
                all_results[f"best_params_{year_tag}"] = best_params
                logger.info(
                    "IS %s 最优: ret=%.2f%% sharpe=%.3f 参数=%s",
                    year_tag, best["total_return"] * 100, best["sharpe"], best_params,
                )
                if args.apply and year_tag == "2026":
                    cfg = optimizer._build_factor_config(best_params)
                    cfg["description"] = (cfg.get("description") or "") + f" | overnight_grid26@{is_start}~{is_end}"
                    backup_strategy_params(output_dir)
                    with open(_STRATEGY_PARAMS_PATH, "w", encoding="utf-8") as f:
                        json.dump(cfg, f, ensure_ascii=False, indent=2)
                    logger.info("已 --apply 写入 strategy_params.json")
                for gkey, gs, ge, gl in oos_fn(periods):
                    sid = f"bt_{gkey}"
                    if _step_done(ckpt, sid):
                        all_results[gkey] = ckpt["results"].get(sid, all_results.get(gkey))
                        continue
                    steps.next(f"Grid{year_tag[-2:]} OOS · {gl}")
                    all_results[gkey] = await run_backtest_grid(optimizer, best_params, provider, gs, ge, args.capital, args.fee, gl)
                    _mark_done(ckpt, sid, _serialize_results({gkey: all_results[gkey]}).get(gkey))
                _mark_done(ckpt, step_id, {"n_success": len(ok)})

        # ── 8. 敏感性 ──
        if not args.skip_sensitivity and not periods.get("year_scope"):
            for key, bstart, bend, blabel, reb in _sensitivity_jobs(periods):
                step_id = f"bt_{key}"
                if _step_done(ckpt, step_id):
                    all_results[key] = ckpt["results"].get(step_id, all_results.get(key))
                    continue
                steps.next(f"敏感性 · {blabel}")
                all_results[key] = await run_backtest_current(provider, bstart, bend, args.capital, args.fee, reb, output_dir, key)
                _mark_done(ckpt, step_id, _serialize_results({key: all_results[key]}).get(key))

        # ── 9. 汇总 ──
        steps.next("生成汇总报告")
        manifest = {
            "started": datetime.fromtimestamp(t_start).isoformat(),
            "finished": datetime.now().isoformat(),
            "elapsed": _elapsed_str(time.time() - t_start),
            "output_dir": str(output_dir),
            "args": {k: getattr(args, k) for k in vars(args)},
        }
        all_results["manifest"] = manifest

        report = generate_report(all_results, periods, manifest)
        (output_dir / "overnight_backtest_report.txt").write_text(report, encoding="utf-8")
        print(report)

        with open(output_dir / "overnight_backtest_results.json", "w", encoding="utf-8") as f:
            json.dump(_serialize_results(all_results), f, ensure_ascii=False, indent=2)
        _results_to_comparison_csv(all_results, output_dir / "comparison.csv")
        with open(output_dir / _MANIFEST_FILE, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        logger.info("报告: %s", output_dir / "overnight_backtest_report.txt")
        logger.info("对比表: %s", output_dir / "comparison.csv")
        logger.info("日志: %s", output_dir / "run.log")
        logger.info("总耗时: %s", manifest["elapsed"])
        return 0

    except Exception as e:
        logger.error("流水线异常: %s", e)
        traceback.print_exc()
        _save_checkpoint(output_dir, ckpt)
        return 1
    finally:
        await close_zstock_database()


def main() -> int:
    _setup_console_utf8()
    _quiet_loggers()
    logging.getLogger(__name__).setLevel(logging.INFO)
    return asyncio.run(async_main(build_parser().parse_args()))


if __name__ == "__main__":
    sys.exit(main())
