"""
隔夜全流程：预计算因子 → 全量因子测评 → 网格搜索(IS) → 多段回测(OOS)

默认流水线（约 6~10 小时，视机器与数据量而定）：
  0. 预计算 2024-01-01 ~ 今日 全市场因子 → 写入 zstock_factor_*
  1. 全量因子 IC 测评（2024 / 2025 / 2026 YTD 三段）
  2. 加载 OHLCV
  3. IS 网格搜索（默认 2026-01-05 ~ 2026-07-27，80 组）
  4. OOS 回测：2025 全年(OOS) / 2024 全年(参照) / 2026 H2 前瞻 / 2026 YTD
  5. 汇总报告

用法：
    cd E:\\TradingAgents-CN
    .\\.venv\\Scripts\\Activate.ps1
    python -m zstock.factor_management.script.网格搜索.overnight_validation

    # 仅预计算 + 测评（跳过网格/回测）
    python -m zstock.factor_management.script.网格搜索.overnight_validation --skip-grid

    # 因子已预计算，只做测评 + 回测
    python -m zstock.factor_management.script.网格搜索.overnight_validation --skip-precompute

    # 全部跳过预计算/测评，只跑回测验证
    python -m zstock.factor_management.script.网格搜索.overnight_validation \\
        --skip-precompute --skip-eval
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

_STRATEGY_PARAMS_PATH = PROJECT_ROOT / "zstock" / "common" / "config" / "strategy_params.json"

logger = logging.getLogger(__name__)

CAPITAL = 10_000_000
FEE = 0.0015
IS_START_DEFAULT = "2026-01-05"
IS_END_DEFAULT = "2026-07-27"


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _min_date(a: str, b: str) -> str:
    return a if a <= b else b


def _max_date(a: str, b: str) -> str:
    return a if a >= b else b


def _periods_for_year(year: int) -> Dict[str, str]:
    """单年模式：预计算/测评/回测均限定在该自然年。"""
    y = int(year)
    end = f"{y}-12-31"
    return {
        "precompute_start": f"{y}-01-01",
        "precompute_end": end,
        "ohlcv_start": f"{y}-01-01",
        "ohlcv_end": end,
        "is_start": f"{y}-01-01",
        "is_end": f"{y}-09-30",
        "is_label": f"IS {y} Q1-Q3",
        "oos_q4_start": f"{y}-10-01",
        "oos_q4_end": end,
        "full_2024_start": f"{y}-01-01",
        "full_2024_end": end,
        "oos_2025_start": "2025-01-02",
        "oos_2025_end": "2025-12-31",
        "oos_forward_start": "2026-07-28",
        "oos_forward_end": end,
        "full_2026_start": f"{y}-01-01",
        "full_2026_end": end,
        "year_scope": y,
    }


def _default_periods(today: str) -> Dict[str, str]:
    is_start = IS_START_DEFAULT
    is_end = _min_date(today, IS_END_DEFAULT)
    if is_end < is_start:
        is_end = is_start
    oos_forward_start = "2026-07-28"
    oos_forward_end = today if today > oos_forward_start else oos_forward_start
    return {
        "precompute_start": "2024-01-01",
        "precompute_end": today,
        "ohlcv_start": "2024-01-01",
        "ohlcv_end": today,
        "is_start": is_start,
        "is_end": is_end,
        "is_label": f"IS 2026 ({is_start}~{is_end})",
        "oos_2025_start": "2025-01-02",
        "oos_2025_end": "2025-12-31",
        "oos_forward_start": oos_forward_start,
        "oos_forward_end": oos_forward_end,
        "full_2024_start": "2024-01-01",
        "full_2024_end": "2024-12-31",
        "full_2026_start": "2026-01-01",
        "full_2026_end": today,
        "oos_q4_start": "2024-10-01",
        "oos_q4_end": "2024-12-31",
    }


class _StepCounter:
    def __init__(self, total: int):
        self.total = total
        self.current = 0

    def next(self, title: str) -> None:
        self.current += 1
        logger.info("")
        logger.info("=" * 60)
        logger.info("[%d/%d] %s", self.current, self.total, title)
        logger.info("=" * 60)


def _quiet_loggers() -> None:
    for name in (
        "zstock",
        "zstock.strategy_management",
        "zstock.data_management",
        "app",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)
    # 因子预计算需可见进度（多进程/并发加载）
    logging.getLogger("zstock.factor_management").setLevel(logging.WARNING)
    logging.getLogger(
        "zstock.factor_management.script.precompute_factors"
    ).setLevel(logging.INFO)


def _load_active_factor_summary() -> List[str]:
    try:
        with open(_STRATEGY_PARAMS_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return ["(无法读取 strategy_params.json)"]

    active = cfg.get("active_factors") or {}
    lines: List[str] = []
    for layer in ("sector", "dragon", "force"):
        items = active.get(layer) or []
        if not items:
            continue
        names = []
        for item in items:
            if isinstance(item, dict):
                names.append(f"{item.get('field', '?')}({item.get('grade', '?')})")
            else:
                names.append(str(item))
        lines.append(f"  {layer}层: " + " + ".join(names))
    desc = cfg.get("description")
    if desc:
        lines.insert(0, f"  策略: {desc[:120]}")
    return lines or ["  (active_factors 为空)"]


async def load_ohlcv(start: str, end: str) -> dict:
    from zstock.common.utils.common_utils import normalize_date
    from zstock.data_management.query_service import get_data_query_service

    qs = get_data_query_service()
    all_stocks_docs, _ = await qs.get_all_stocks()
    mainboard_codes = [
        d["code"] for d in all_stocks_docs
        if d.get("is_mainboard") and not d.get("is_st")
    ]
    logger.info("主板非ST: %d 只，加载 OHLCV %s → %s...", len(mainboard_codes), start, end)
    ohlcv_data: dict = {}
    chunk_size = 500
    total_chunks = (len(mainboard_codes) + chunk_size - 1) // chunk_size
    for ci, i in enumerate(range(0, len(mainboard_codes), chunk_size), 1):
        chunk = mainboard_codes[i : i + chunk_size]
        logger.info("  OHLCV 批次 [%d/%d] %d 只...", ci, total_chunks, len(chunk))
        try:
            batch = await qs.get_ohlcv_batch(chunk, start, end)
            if batch:
                ohlcv_data.update(batch)
        except Exception as e:
            logger.warning("OHLCV 批次失败: %s", e)
    for code, df in ohlcv_data.items():
        if "trade_date" in df.columns:
            df["trade_date"] = df["trade_date"].apply(normalize_date)
    logger.info("OHLCV 就绪: %d 只", len(ohlcv_data))
    return ohlcv_data


async def run_precompute(
    start: str,
    end: str,
    lookback: int,
    *,
    compute_workers: Optional[int] = None,
    load_workers: Optional[int] = None,
    query_workers: Optional[int] = None,
    resource_fraction: Optional[float] = None,
) -> Dict[str, int]:
    from zstock.factor_management.script.precompute_factors import FactorPrecomputeService
    from zstock.data_management.query_service import get_data_query_service

    await get_data_query_service().ensure_indexes()
    service = FactorPrecomputeService(
        compute_workers=compute_workers,
        load_workers=load_workers,
        query_workers=query_workers,
        resource_fraction=resource_fraction,
    )
    logger.info("预计算区间: %s → %s (lookback=%d 天)", start, end, lookback)
    return await service.precompute_date_range(start, end, lookback_days=lookback)


async def run_factor_eval(
    start: str,
    end: str,
    output_dir: Path,
    *,
    plot: bool = False,
    period: int = 5,
) -> Optional[Path]:
    from zstock.factor_management.script.因子测评.factor_evaluation import (
        FactorEvaluationPipeline,
    )

    label = f"eval_{start[:4]}_{end[:4] if start[:4] != end[:4] else end[5:7]}"
    eval_dir = output_dir / "factor_eval" / label
    eval_dir.mkdir(parents=True, exist_ok=True)
    logger.info("因子测评: %s → %s → %s", start, end, eval_dir)

    pipeline = FactorEvaluationPipeline(
        start_date=start,
        end_date=end,
        period=period,
        n_quantiles=5,
        conditional=True,
        top_sectors=3,
        invert_negative=True,
        plot=plot,
        output_dir=str(eval_dir),
        decay_max=0,
        layer=None,
        field=None,
    )
    await pipeline.run()
    summary = eval_dir / "summary.csv"
    return summary if summary.exists() else None


async def run_backtest(
    optimizer,
    params: Dict[str, Any],
    ohlcv_provider,
    start: str,
    end: str,
    capital: float,
    fee: float,
    label: str,
) -> Dict[str, Any]:
    logger.info("=" * 50)
    logger.info("%s: %s → %s", label, start, end)
    logger.info("=" * 50)
    row = await optimizer.run_one(params, ohlcv_provider, start, end, capital, fee)
    status = row.get("status", "unknown")
    if status == "success":
        logger.info(
            "  %s 结果: ret=%.2f%% sharpe=%.3f mdd=%.2f%% calmar=%.3f obj=%.4f",
            label,
            row.get("total_return", 0) * 100,
            row.get("sharpe", 0),
            row.get("max_drawdown", 0) * 100,
            row.get("calmar", 0),
            row.get("objective", 0),
        )
    else:
        logger.warning("  %s 失败: %s", label, status)
    return row


def _top_factors_from_summary(summary_path: Path, top_n: int = 5) -> List[str]:
    try:
        df = pd.read_csv(summary_path)
        if df.empty or "Total_Score" not in df.columns:
            return []
        cols = [c for c in ("layer", "field", "Total_Score", "Grade") if c in df.columns]
        top = df.nlargest(top_n, "Total_Score")[cols]
        return [f"    {r['layer']}.{r['field']} score={r['Total_Score']:.1f} ({r.get('Grade', '')})"
                for _, r in top.iterrows()]
    except Exception as e:
        return [f"    (读取失败: {e})"]


def generate_summary(all_results: Dict[str, Any], periods: Dict[str, str]) -> str:
    lines = [
        "",
        "=" * 90,
        "隔夜全面验证报告",
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"数据截止: {periods['full_2026_end']}",
        "=" * 90,
        "",
        "当前因子配置 (strategy_params.json):",
        *_load_active_factor_summary(),
        "",
        "── 回测结果 ──",
        f"{'实验':<25} {'总收益':>10} {'年化':>10} {'夏普':>8} {'卡玛':>8} {'回撤':>10} {'换手':>8} {'成本':>8}",
        "─" * 90,
    ]

    is_label = periods.get("is_label", "IS")
    if periods.get("year_scope"):
        experiments = [
            (is_label, "is_main"),
            (f"OOS Q4 {periods['year_scope']}", "oos_q4"),
            (f"Full {periods['year_scope']}", "full_year"),
        ]
    else:
        experiments = [
            (is_label, "is_main"),
            ("OOS 2025 Full", "oos_2025"),
            ("Ref 2024 Full", "full_2024"),
            ("Forward 2026 H2", "oos_forward_2026"),
            ("Full 2026 YTD", "full_2026"),
        ]

    for label, key in experiments:
        r = all_results.get(key, {})
        if not r or r.get("status") != "success":
            status = r.get("status", "skipped") if r else "skipped"
            lines.append(
                f"{label:<25} {'—':>10} {'—':>10} {'—':>8} {'—':>8} {'—':>10} {'—':>8} {'—':>8}  [{status}]"
            )
            continue
        lines.append(
            f"{label:<25} "
            f"{r.get('total_return', 0) * 100:>9.2f}% "
            f"{r.get('annualized_return', 0) * 100:>9.2f}% "
            f"{r.get('sharpe', 0):>8.3f} "
            f"{r.get('calmar', 0):>8.3f} "
            f"{r.get('max_drawdown', 0) * 100:>9.2f}% "
            f"{r.get('avg_turnover', 0) * 100:>7.2f}% "
            f"{r.get('total_cost', 0) * 100:>7.2f}%"
        )

    eval_summaries = all_results.get("eval_summaries") or {}
    if eval_summaries:
        lines += ["", "── 因子测评 Top5 (Total_Score) ──"]
        for label, path in eval_summaries.items():
            lines.append(f"  [{label}]")
            lines.extend(_top_factors_from_summary(Path(path)))

    precompute_stats = all_results.get("precompute_stats")
    if precompute_stats:
        lines += [
            "",
            "── 预计算统计 ──",
            f"  market={precompute_stats.get('market', 0):,}  "
            f"sector={precompute_stats.get('sector', 0):,}  "
            f"dragon={precompute_stats.get('dragon', 0):,}  "
            f"force={precompute_stats.get('force', 0):,}",
        ]

    is_r = all_results.get("is_main", {})
    oos_r = all_results.get("oos_q4") or all_results.get("oos_2025", {})
    if is_r.get("status") == "success" and oos_r.get("status") == "success":
        is_s = is_r.get("sharpe", 0)
        oos_s = oos_r.get("sharpe", 0)
        oos_label = "OOS Q4" if periods.get("year_scope") else "OOS 2025"
        lines += ["", f"── 过拟合诊断 ({is_label} vs {oos_label}) ──"]
        if abs(oos_s) > 1e-9:
            ratio = is_s / oos_s
            if ratio < 0:
                verdict = "IS/OOS 反向"
            elif ratio < 2:
                verdict = "过拟合可控"
            elif ratio < 5:
                verdict = "过拟合明显"
            else:
                verdict = "过拟合极严重"
            lines.append(f"  IS Sharpe: {is_s:.3f}  OOS Sharpe: {oos_s:.3f}  比率: {ratio:.2f}x → {verdict}")

    f25 = all_results.get("oos_2025", {})
    if f25.get("status") == "success":
        ret_25 = f25.get("total_return", 0)
        sharpe_25 = f25.get("sharpe", 0)
        tag = "策略泛化能力 OK" if ret_25 > 0 and sharpe_25 > 0 else "泛化失败"
        lines.append(f"\n  2025 OOS: 收益 {ret_25 * 100:.2f}%, Sharpe {sharpe_25:.3f} — {tag}")

    fwd = all_results.get("oos_forward_2026", {})
    if fwd.get("status") == "success":
        ret_fwd = fwd.get("total_return", 0)
        sharpe_fwd = fwd.get("sharpe", 0)
        lines.append(
            f"  2026 H2 前瞻: 收益 {ret_fwd * 100:.2f}%, Sharpe {sharpe_fwd:.3f}"
        )

    best_params = all_results.get("best_params", {})
    if best_params:
        lines += ["", "IS 最优参数:"]
        for k, v in best_params.items():
            lines.append(f"  {k}: {v}")

    lines += ["", "=" * 90, ""]
    return "\n".join(lines)


def _eval_windows(periods: Dict[str, str]) -> List[Tuple[str, str, str]]:
    """根据 periods 生成因子测评窗口（单年模式只测该年）。"""
    if periods.get("year_scope"):
        y = periods["year_scope"]
        return [(str(y), periods["precompute_start"], periods["precompute_end"])]
    end_cap = periods["precompute_end"]
    candidates = [
        ("2024", periods["full_2024_start"], periods["full_2024_end"]),
        ("2025", periods["oos_2025_start"], periods["oos_2025_end"]),
        ("2026_YTD", periods["full_2026_start"], periods["full_2026_end"]),
    ]
    out: List[Tuple[str, str, str]] = []
    for label, estart, eend in candidates:
        if estart <= end_cap:
            out.append((label, estart, _min_date(eend, end_cap)))
    return out


def _backtest_jobs(periods: Dict[str, str]) -> List[Tuple[str, str, str, str]]:
    """回测任务列表。"""
    if periods.get("year_scope"):
        y = periods["year_scope"]
        return [
            ("oos_q4", periods["oos_q4_start"], periods["oos_q4_end"], f"OOS Q4 {y}"),
            ("full_year", periods["precompute_start"], periods["precompute_end"], f"Full {y}"),
        ]
    jobs: List[Tuple[str, str, str, str]] = [
        ("oos_2025", periods["oos_2025_start"], periods["oos_2025_end"], "OOS 2025 Full"),
        ("full_2024", periods["full_2024_start"], periods["full_2024_end"], "Ref 2024 Full"),
        ("full_2026", periods["full_2026_start"], periods["full_2026_end"], "Full 2026 YTD"),
    ]
    if periods["oos_forward_end"] > periods["oos_forward_start"]:
        jobs.insert(
            2,
            (
                "oos_forward_2026",
                periods["oos_forward_start"],
                periods["oos_forward_end"],
                "Forward 2026 H2",
            ),
        )
    return jobs


def _count_steps(args: argparse.Namespace) -> int:
    n = 1  # 报告
    if not args.skip_precompute:
        n += 1
    if not args.skip_eval:
        n += 1
    if not args.skip_grid:
        n += 1  # OHLCV
        n += 1  # IS grid
        n += len(_backtest_jobs(_periods_for_year(args.year) if args.year else _default_periods(args.end_date)))
    return n


def build_parser() -> argparse.ArgumentParser:
    today = _today_str()
    p = argparse.ArgumentParser(description="zstock 隔夜全流程：预计算 → 测评 → 网格搜索 → 回测")
    p.add_argument("--skip-precompute", action="store_true", help="跳过因子预计算")
    p.add_argument("--skip-eval", action="store_true", help="跳过全量因子测评")
    p.add_argument("--skip-grid", action="store_true", help="跳过网格搜索与回测（仅预计算+测评）")
    p.add_argument("--lookback", type=int, default=120, help="预计算 OHLCV 回看天数（默认 120）")
    p.add_argument(
        "--resource-fraction",
        type=float,
        default=0.5,
        help="资源占用比例（默认 0.5=最多占一半 CPU/内存）",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=None,
        help="因子预计算进程数（默认按资源预算，会被封顶）",
    )
    p.add_argument(
        "--load-workers",
        type=int,
        default=None,
        help="MongoDB 预加载并发数（默认按资源预算）",
    )
    p.add_argument(
        "--query-workers",
        type=int,
        default=None,
        help="MongoDB 子查询并发数（默认按资源预算）",
    )
    p.add_argument("--max-combinations", type=int, default=80, help="IS 网格搜索组合数")
    p.add_argument("--space", default="core", choices=["core", "wide", "full"], help="搜索空间")
    p.add_argument("--plot", action="store_true", help="因子测评保存分析图（较慢）")
    p.add_argument("--eval-period", type=int, default=5, help="因子测评预测周期（交易日）")
    p.add_argument("--year", type=int, default=None, help="快捷：仅跑指定年份（如 2024）")
    p.add_argument("--end-date", default=today, help=f"全局结束日期（默认 {today}）")
    p.add_argument("--precompute-start", default="2024-01-01", help="预计算开始日期")
    p.add_argument("--is-start", default=IS_START_DEFAULT, help="IS 网格搜索起点（默认 2026-01-05）")
    p.add_argument("--is-end", default=IS_END_DEFAULT, help="IS 网格搜索终点（默认 2026-07-27）")
    return p


async def async_main(args: argparse.Namespace) -> int:
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.strategy_management.script.backtester import make_ohlcv_provider_from_dict
    from grid_search_real import RealGridSearchOptimizer

    if args.year:
        periods = _periods_for_year(args.year)
    else:
        periods = _default_periods(args.end_date)
        periods["precompute_start"] = args.precompute_start
        periods["is_start"] = args.is_start
        periods["is_end"] = _min_date(args.is_end, args.end_date)
        if periods["is_end"] < periods["is_start"]:
            periods["is_end"] = periods["is_start"]
        periods["is_label"] = f"IS ({periods['is_start']}~{periods['is_end']})"
        periods["precompute_end"] = _min_date(periods["precompute_end"], args.end_date)
        periods["ohlcv_end"] = periods["precompute_end"]
        periods["oos_forward_end"] = args.end_date
        periods["full_2026_end"] = _min_date(periods["full_2026_end"], args.end_date)

    output_dir = (
        Path(__file__).resolve().parent
        / "output"
        / f"overnight_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info("输出目录: %s", output_dir)

    steps = _StepCounter(_count_steps(args))
    all_results: Dict[str, Any] = {}

    try:
        await init_zstock_database()
    except Exception as e:
        logger.warning("init_zstock_database 失败，尝试直连 MongoDB: %s", e)
        from app.core.database import db_manager
        await db_manager.init_mongodb()

    try:
        # ── 0. 预计算因子 ──
        if not args.skip_precompute:
            steps.next(
                f"预计算因子 {periods['precompute_start']} → {periods['precompute_end']}"
            )
            stats = await run_precompute(
                periods["precompute_start"],
                periods["precompute_end"],
                args.lookback,
                compute_workers=args.workers,
                load_workers=args.load_workers,
                query_workers=args.query_workers,
                resource_fraction=args.resource_fraction,
            )
            all_results["precompute_stats"] = stats

        # ── 1. 全量因子测评 ──
        if not args.skip_eval:
            eval_windows = _eval_windows(periods)
            steps.next(
                "全量因子 IC 测评（"
                + " / ".join(w[0] for w in eval_windows)
                + "）"
            )
            eval_summaries: Dict[str, str] = {}
            for label, estart, eend in eval_windows:
                summary_path = await run_factor_eval(
                    estart, eend, output_dir, plot=args.plot, period=args.eval_period
                )
                if summary_path:
                    eval_summaries[label] = str(summary_path)
            all_results["eval_summaries"] = eval_summaries

        if args.skip_grid:
            steps.next("生成报告（跳过网格/回测）")
            report = generate_summary(all_results, periods)
            (output_dir / "overnight_validation_report.txt").write_text(
                report, encoding="utf-8"
            )
            print(report)
            logger.info("报告目录: %s", output_dir)
            return 0

        # ── 2. 加载 OHLCV ──
        steps.next(f"加载 OHLCV {periods['ohlcv_start']} → {periods['ohlcv_end']}")
        ohlcv = await load_ohlcv(periods["ohlcv_start"], periods["ohlcv_end"])
        if not ohlcv:
            logger.error("无 OHLCV 数据，无法回测")
            return 1
        provider = make_ohlcv_provider_from_dict(ohlcv)

        # ── 3. IS 网格搜索 ──
        steps.next(
            f"IS 网格搜索 {periods['is_start']} → {periods['is_end']} "
            f"({args.max_combinations} 组, space={args.space})"
        )
        optimizer = RealGridSearchOptimizer(output_dir=output_dir / "is_grid_search")
        is_df = await optimizer.run_grid_search(
            ohlcv_provider=provider,
            start=periods["is_start"],
            end=periods["is_end"],
            capital=CAPITAL,
            fee=FEE,
            max_combinations=args.max_combinations,
            space_name=args.space,
            seed=42,
        )
        optimizer.save_results(is_df)

        is_success = is_df[is_df["status"] == "success"]
        if is_success.empty:
            logger.error("IS 网格搜索无成功组合")
            return 1
        best_is = is_success.iloc[0].to_dict()
        best_params = {
            k: best_is[k]
            for k in optimizer.baseline_params().keys()
            if k in best_is
        }
        best_params["weight_coop"] = best_is.get("weight_coop", 0)
        all_results["is_main"] = best_is
        all_results["best_params"] = best_params
        logger.info(
            "IS 最优: ret=%.2f%% sharpe=%.3f",
            best_is["total_return"] * 100,
            best_is["sharpe"],
        )
        logger.info("最优参数: %s", best_params)

        # ── 4~7. OOS 回测 ──
        backtest_jobs = _backtest_jobs(periods)
        for key, bstart, bend, blabel in backtest_jobs:
            steps.next(f"回测 {blabel}: {bstart} → {bend}")
            all_results[key] = await run_backtest(
                optimizer, best_params, provider,
                bstart, bend, CAPITAL, FEE, blabel,
            )

        # ── 汇总 ──
        steps.next("生成汇总报告")
        report = generate_summary(all_results, periods)
        report_path = output_dir / "overnight_validation_report.txt"
        report_path.write_text(report, encoding="utf-8")
        print(report)

        json_path = output_dir / "overnight_results.json"
        serializable: Dict[str, Any] = {}
        for k, v in all_results.items():
            if isinstance(v, dict):
                serializable[k] = {
                    kk: vv
                    for kk, vv in v.items()
                    if isinstance(vv, (str, int, float, bool, type(None)))
                }
            else:
                serializable[k] = str(v)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)

        logger.info("报告目录: %s", output_dir)
        return 0

    finally:
        await close_zstock_database()


def _setup_console_utf8() -> None:
    import os

    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _setup_logging() -> None:
    """日志统一走 stdout，避免 PowerShell 把 stderr 当 Error 报红。"""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def main() -> int:
    _setup_console_utf8()
    _setup_logging()
    _quiet_loggers()
    logging.getLogger(__name__).setLevel(logging.INFO)
    args = build_parser().parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    sys.exit(main())
