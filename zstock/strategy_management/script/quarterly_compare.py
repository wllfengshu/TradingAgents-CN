"""
按季度跑 2024/2025/2026 回测，汇总对比 + 2026 亏损归因（不改参数）。

用法:
    python -m zstock.strategy_management.script.quarterly_compare
    python -m zstock.strategy_management.script.quarterly_compare --skip-run  # 仅解析已有 output
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent / "output" / "quarterly_compare"
FEE = 0.0015
CAPITAL = 1_000_000.0

# (label, start, end)
QUARTERS: List[Tuple[str, str, str]] = [
    ("2024_Q1", "2024-01-01", "2024-03-31"),
    ("2024_Q2", "2024-04-01", "2024-06-30"),
    ("2024_Q3", "2024-07-01", "2024-09-30"),
    ("2024_Q4", "2024-10-01", "2024-12-31"),
    ("2025_Q1", "2025-01-02", "2025-03-31"),
    ("2025_Q2", "2025-04-01", "2025-06-30"),
    ("2025_Q3", "2025-07-01", "2025-09-30"),
    ("2025_Q4", "2025-10-01", "2025-12-31"),
    ("2026_Q1", "2026-01-01", "2026-03-31"),
    ("2026_Q2", "2026-04-01", "2026-06-30"),
    ("2026_Q3", "2026-07-01", "2026-08-26"),
]


def _metrics_row(label: str, result) -> Dict[str, Any]:
    m = result.metrics
    eq = result.equity_curve
    return {
        "label": label,
        "start": str(eq.index.min()) if len(eq) else "",
        "end": str(eq.index.max()) if len(eq) else "",
        "trading_days": len(eq),
        "total_return": float(m.get("total_return", 0.0)),
        "annualized_return": float(m.get("annualized_return", 0.0)),
        "sharpe": float(m.get("sharpe", 0.0)),
        "max_drawdown": float(m.get("max_drawdown", 0.0)),
        "win_rate": float(m.get("win_rate", 0.0)),
        "rebalance_count": len(result.trades),
        "avg_turnover": float(m.get("avg_turnover", 0.0)) if "avg_turnover" in m else None,
        "total_cost_pct": float(result.cost_series.sum()) if len(result.cost_series) else 0.0,
        "final_equity": float(eq.iloc[-1]) if len(eq) else 1.0,
    }


async def run_all_quarters() -> List[Dict[str, Any]]:
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
    from zstock.strategy_management.script.backtester import Backtester

    await init_zstock_database()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []

    try:
        bt = Backtester(
            fee_rate=FEE,
            initial_capital=CAPITAL,
            factor_pipeline=CrossSectionStrategyPipeline(),
        )
        for i, (label, start, end) in enumerate(QUARTERS, 1):
            logger.info("=" * 60)
            logger.info("[%d/%d] %s  %s → %s", i, len(QUARTERS), label, start, end)
            result = await bt.run_real_data(
                start,
                end,
                use_precomputed_factors=True,
                output_dir=f"output/quarterly_compare/{label}",
                verbose=False,
                save_outputs=True,
            )
            row = _metrics_row(label, result)
            rows.append(row)
            # 保存单季 curve / trades 供归因
            curve_path = OUTPUT_DIR / label / "equity_curve.csv"
            if len(result.equity_curve):
                result.equity_curve.to_csv(curve_path, header=["equity"])
            if result.trades:
                pd.DataFrame(result.trades).to_csv(
                    OUTPUT_DIR / label / "trades.csv", index=False
                )
            logger.info(
                "  → ret=%+.2f%%  sharpe=%.2f  mdd=%.2f%%  days=%d",
                row["total_return"] * 100,
                row["sharpe"],
                row["max_drawdown"] * 100,
                row["trading_days"],
            )
    finally:
        await close_zstock_database()

    return rows


def _pivot_table(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["year"] = df["label"].str[:4]
    df["quarter"] = df["label"].str[-2:]
    pivot_ret = df.pivot(index="quarter", columns="year", values="total_return")
    pivot_sharpe = df.pivot(index="quarter", columns="year", values="sharpe")
    pivot_mdd = df.pivot(index="quarter", columns="year", values="max_drawdown")
    return df, pivot_ret, pivot_sharpe, pivot_mdd


def _analyze_2026_attribution(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """2026 亏损归因：对比同季度历史 + 交易/环境统计（不改参）。"""
    df = pd.DataFrame(rows)
    q2026 = df[df["label"].str.startswith("2026")].set_index("label")

    # 同季度跨年度对比
    same_q: Dict[str, Dict[str, float]] = {}
    for q in ("Q1", "Q2", "Q3"):
        same_q[q] = {}
        for y in ("2024", "2025", "2026"):
            key = f"{y}_{q}"
            sub = df[df["label"] == key]
            if not sub.empty:
                same_q[q][y] = float(sub.iloc[0]["total_return"])

    # 2026 全 YTD 汇总（从各季 curve 拼接或加总）
    ytd_return = float((1 + q2026["total_return"]).prod() - 1) if len(q2026) else 0.0

    # 解析 2026 各季 trades
    env_stats: Dict[str, Any] = {}
    for label in q2026.index:
        trades_path = OUTPUT_DIR / label / "trades.csv"
        if not trades_path.exists():
            continue
        tdf = pd.read_csv(trades_path)
        n = len(tdf)
        flat = int(tdf["risk_status"].astype(str).str.contains("flat", na=False).sum()) if "risk_status" in tdf.columns else 0
        hold_ns = int(tdf["risk_status"].astype(str).str.contains("hold_no_signals", na=False).sum()) if "risk_status" in tdf.columns else 0
        zero_pos = int((tdf.get("n_holdings", pd.Series(dtype=float)).fillna(0) == 0).sum())
        issues = tdf.get("risk_issues", pd.Series(dtype=str)).fillna("").astype(str)
        red_days = int(issues.str.contains("grade=red", na=False).sum())
        yellow_days = int(issues.str.contains("grade=yellow", na=False).sum())
        avg_scale = float(tdf["position_scale"].mean()) if "position_scale" in tdf.columns else 1.0
        env_stats[label] = {
            "rebalance_events": n,
            "flat_no_position": flat,
            "hold_no_signals": hold_ns,
            "zero_holdings_rebalance": zero_pos,
            "grade_red_rebalances": red_days,
            "grade_yellow_rebalances": yellow_days,
            "avg_position_scale": round(avg_scale, 3),
        }

    # 2026 全 YTD curve 分析（若存在完整年回测 curve）
    curve_analysis: Dict[str, Any] = {}
    for curve_file in sorted((Path(__file__).parent / "output").glob("backtest_curve_*.csv")):
        c = pd.read_csv(curve_file)
        if "date" not in c.columns or len(c) < 50:
            continue
        c["date"] = pd.to_datetime(c["date"])
        if c["date"].max().year != 2026:
            continue
        if c["date"].min().year != 2026:
            continue
        # 取最新一份 2026 全长 curve
        curve_analysis = _curve_attribution(c)
        break

    # 2026 log 归因：reversal+yellow 只减不加
    log_path = Path(__file__).parent / "output" / "backtest_2026.log"
    log_stats = _parse_2026_log(log_path) if log_path.exists() else {}

    worst_q = q2026["total_return"].idxmin() if len(q2026) else ""
    best_prior_q2 = same_q.get("Q2", {}).get("2024", 0), same_q.get("Q2", {}).get("2025", 0)

    return {
        "ytd_compound_return_approx": ytd_return,
        "quarterly_returns_2026": q2026["total_return"].to_dict(),
        "same_quarter_comparison": same_q,
        "worst_quarter_2026": worst_q,
        "worst_quarter_return": float(q2026.loc[worst_q, "total_return"]) if worst_q else 0.0,
        "q2_2026_vs_prior": {
            "2026_Q2": same_q.get("Q2", {}).get("2026"),
            "2024_Q2": same_q.get("Q2", {}).get("2024"),
            "2025_Q2": same_q.get("Q2", {}).get("2025"),
        },
        "env_stats_by_quarter": env_stats,
        "curve_analysis": curve_analysis,
        "log_stats": log_stats,
        "diagnosis": _build_diagnosis(same_q, env_stats, curve_analysis, log_stats),
    }


def _curve_attribution(c: pd.DataFrame) -> Dict[str, Any]:
    c = c.sort_values("date").copy()
    c["month"] = c["date"].dt.to_period("M")
    monthly = c.groupby("month").apply(
        lambda g: float(g["equity"].iloc[-1] / g["equity"].iloc[0] - 1) if len(g) > 1 else 0.0
    )
    worst_month = str(monthly.idxmin()) if len(monthly) else ""
    worst_month_ret = float(monthly.min()) if len(monthly) else 0.0
    worst_day_idx = c["daily_return"].idxmin() if "daily_return" in c.columns else None
    worst_day = {}
    if worst_day_idx is not None and pd.notna(worst_day_idx):
        row = c.loc[worst_day_idx]
        worst_day = {
            "date": str(row["date"].date()),
            "daily_return": float(row["daily_return"]),
            "equity": float(row["equity"]),
        }
    # 估算平均仓位：用日收益为 0 的比例近似空仓
    zero_ret_days = int((c["daily_return"].abs() < 1e-9).sum()) if "daily_return" in c.columns else 0
    return {
        "monthly_returns": {str(k): float(v) for k, v in monthly.items()},
        "worst_month": worst_month,
        "worst_month_return": worst_month_ret,
        "worst_day": worst_day,
        "zero_return_days_pct": zero_ret_days / len(c) if len(c) else 0.0,
        "max_drawdown": float(c["drawdown"].min()) if "drawdown" in c.columns else 0.0,
    }


def _parse_2026_log(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    reduce_only = len(re.findall(r"只减不加", text))
    reversal = len(re.findall(r"regime=reversal", text))
    yellow = len(re.findall(r"grade=yellow", text))
    red = len(re.findall(r"grade=red", text))
    dd_throttle = len(re.findall(r"drawdown.*scale|DD.*throttle|仓位.*0\.7", text, re.I))
    hard_stop = len(re.findall(r"硬止损|hard_stop", text))
    flat = len(re.findall(r"flat_no_position|no_signals", text))
    return {
        "reduce_only_events": reduce_only,
        "regime_reversal_mentions": reversal,
        "grade_yellow_mentions": yellow,
        "grade_red_mentions": red,
        "drawdown_throttle_mentions": dd_throttle,
        "hard_stop_mentions": hard_stop,
        "no_signal_flat_mentions": flat,
    }


def _build_diagnosis(
    same_q: Dict[str, Dict[str, float]],
    env_stats: Dict[str, Any],
    curve_analysis: Dict[str, Any],
    log_stats: Dict[str, Any],
) -> List[str]:
    lines: List[str] = []
    q2_26 = same_q.get("Q2", {}).get("2026", 0)
    q2_24 = same_q.get("Q2", {}).get("2024", 0)
    q2_25 = same_q.get("Q2", {}).get("2025", 0)
    if q2_26 < q2_24 and q2_26 < q2_25:
        lines.append(
            f"2026 Q2 ({q2_26:+.1%}) 显著弱于 2024 Q2 ({q2_24:+.1%}) 和 2025 Q2 ({q2_25:+.1%})，是 YTD 主要拖累。"
        )
    q1_26 = same_q.get("Q1", {}).get("2026", 0)
    if q1_26 > 0:
        lines.append(f"2026 Q1 为正 ({q1_26:+.1%})，亏损集中在 Q2 及以后。")

    if curve_analysis.get("worst_month"):
        lines.append(
            f"最弱月份 {curve_analysis['worst_month']} ({curve_analysis.get('worst_month_return', 0):+.1%})。"
        )
    if curve_analysis.get("worst_day"):
        wd = curve_analysis["worst_day"]
        lines.append(f"最大单日亏损 {wd.get('date')} ({wd.get('daily_return', 0):+.1%})。")

    q2_env = env_stats.get("2026_Q2", {})
    if q2_env.get("grade_red_rebalances", 0) > q2_env.get("grade_yellow_rebalances", 0):
        lines.append("2026 Q2 再平衡日 M1 红灯次数多于黄灯，环境过滤导致长时间空仓。")
    elif q2_env.get("zero_holdings_rebalance", 0) >= 3:
        lines.append("2026 Q2 多次再平衡日零持仓，策略在弱环境下未能建立有效仓位。")

    if log_stats.get("reduce_only_events", 0) > 5:
        lines.append(
            f"全年 reversal+yellow「只减不加」触发约 {log_stats['reduce_only_events']} 次，抑制新开仓。"
        )
    if curve_analysis.get("zero_return_days_pct", 0) > 0.35:
        lines.append(
            f"约 {curve_analysis['zero_return_days_pct']:.0%} 交易日日收益为 0（空仓/无波动），现金拖累明显。"
        )
    if not lines:
        lines.append("需结合各季 trades 与 factor regime 进一步人工复核。")
    return lines


def _write_report(
    rows: List[Dict[str, Any]],
    attribution: Dict[str, Any],
    out_path: Path,
) -> None:
    df, pivot_ret, pivot_sharpe, pivot_mdd = _pivot_table(rows)

    lines = [
        "季度回测对比报告（v1.16.0 定版，precomputed，fee=0.15%，capital=1M）",
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "═══ 一、季度累计收益（%）═══",
        pivot_ret.map(lambda x: f"{x*100:+.2f}" if pd.notna(x) else "—").to_string(),
        "",
        "═══ 二、季度 Sharpe ═══",
        pivot_sharpe.map(lambda x: f"{x:.2f}" if pd.notna(x) else "—").to_string(),
        "",
        "═══ 三、季度最大回撤（%）═══",
        pivot_mdd.map(lambda x: f"{x*100:.2f}" if pd.notna(x) else "—").to_string(),
        "",
        "═══ 四、明细 ═══",
    ]
    for r in rows:
        lines.append(
            f"  {r['label']:8s}  {r['start']}~{r['end']}  "
            f"ret={r['total_return']:+.2%}  sharpe={r['sharpe']:.2f}  "
            f"mdd={r['max_drawdown']:.2%}  reb={r['rebalance_count']}  days={r['trading_days']}"
        )

    lines.extend([
        "",
        "═══ 五、2026 亏损归因（诊断性，不改参）═══",
    ])
    for d in attribution.get("diagnosis", []):
        lines.append(f"  - {d}")

    lines.extend([
        "",
        "同季度横向对比（累计收益）:",
    ])
    for q, vals in attribution.get("same_quarter_comparison", {}).items():
        parts = "  ".join(f"{y}={v:+.1%}" for y, v in sorted(vals.items()))
        lines.append(f"  {q}: {parts}")

    if attribution.get("env_stats_by_quarter"):
        lines.extend(["", "2026 各季环境/持仓统计（再平衡日）:"])
        for k, v in attribution["env_stats_by_quarter"].items():
            lines.append(f"  {k}: {json.dumps(v, ensure_ascii=False)}")

    if attribution.get("curve_analysis"):
        ca = attribution["curve_analysis"]
        lines.extend(["", "2026 YTD 月度收益:"])
        for m, v in ca.get("monthly_returns", {}).items():
            lines.append(f"  {m}: {v:+.2%}")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("报告已写入 %s", out_path)


async def async_main(skip_run: bool) -> None:
    json_path = OUTPUT_DIR / "quarterly_results.json"
    rows: List[Dict[str, Any]] = []

    if skip_run and json_path.exists():
        rows = json.loads(json_path.read_text(encoding="utf-8"))
        logger.info("跳过回测，读取已有 %s", json_path)
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        rows = await run_all_quarters()
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    attribution = _analyze_2026_attribution(rows)
    (OUTPUT_DIR / "attribution_2026.json").write_text(
        json.dumps(attribution, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report_path = OUTPUT_DIR / "quarterly_compare_report.txt"
    _write_report(rows, attribution, report_path)
    logger.info("报告: %s", report_path)


def main() -> None:
    p = argparse.ArgumentParser(description="季度回测对比 + 2026 归因")
    p.add_argument("--skip-run", action="store_true", help="仅解析已有 quarterly_results.json")
    args = p.parse_args()
    asyncio.run(async_main(args.skip_run))


if __name__ == "__main__":
    main()
