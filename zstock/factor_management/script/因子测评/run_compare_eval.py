"""
多口径因子测评批量运行（对齐历史 factor_ab_history 对照）

模式：
  cond_p5   — 条件宇宙 period=5（默认生产口径）
  cond_p10  — 条件宇宙 period=10
  cond_p20  — 条件宇宙 period=20
  full_p5   — 全市场 period=5
  full_p10  — 全市场 period=10
  full_p20  — 全市场 period=20

用法：
  python -m zstock.factor_management.script.因子测评.run_compare_eval
  python -m zstock.factor_management.script.因子测评.run_compare_eval --job-parallel 8 --workers 8
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

BASE = Path(__file__).resolve().parent
YEARS = [2024, 2025, 2026]

MODES: List[Tuple[str, int, bool]] = [
    ("cond_p5", 5, True),
    ("cond_p10", 10, True),
    ("cond_p20", 20, True),
    ("full_p5", 5, False),
    ("full_p10", 10, False),
    ("full_p20", 20, False),
]

# 历史 factor_ab_history 中标记为 A 的因子（标准字段名）
HISTORICAL_A_FACTORS = [
    "dragon.f33_consecutive_boards",
    "dragon.f36_identity_premium",
    "dragon.f34_resonance_pct",
    "dragon.f34_resonance_pct_3d",
    "dragon.f34_resonance_pct_5d",
    "dragon.f34_resonance_pct_10d",
    "dragon.f35_bollinger_trend",
    "force.f_mean_reversion_signal",
]


def _year_window(year: int) -> Tuple[str, str]:
    from zstock.factor_management.script.因子测评.factor_evaluation import _year_eval_window

    return _year_eval_window(year)


def grade_letter(g) -> str:
    if g is None or (isinstance(g, float) and pd.isna(g)):
        return "N/A"
    s = str(g)
    if " - " in s:
        return s.split(" - ")[0].strip()
    if s.startswith("N/A"):
        return "N/A"
    return s[:1] if s else "N/A"


async def run_all_jobs(
    *,
    modes: Sequence[Tuple[str, int, bool]] = MODES,
    years: Sequence[int] = YEARS,
    workers: Optional[int] = None,
    job_parallel: int = 8,
    output_root: Optional[Path] = None,
) -> Path:
    from zstock.factor_management.script.因子测评.factor_evaluation import (
        FactorEvaluationPipeline,
    )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = output_root or (BASE / "output" / f"compare_eval_{ts}")
    root.mkdir(parents=True, exist_ok=True)

    jobs: List[Tuple[str, int, str, str, bool, Path]] = []
    for mode, period, conditional in modes:
        for year in years:
            start, end = _year_window(year)
            ylabel = str(year) if year != datetime.now().year else f"{year}_YTD"
            out_dir = root / mode / ylabel
            jobs.append((mode, period, start, end, conditional, out_dir))

    sem = asyncio.Semaphore(max(1, int(job_parallel)))
    done: Dict[str, Path] = {}

    async def _one(job: Tuple[str, int, str, str, bool, Path]) -> None:
        mode, period, start, end, conditional, out_dir = job
        key = f"{mode}/{out_dir.name}"
        async with sem:
            logger.info(
                "\n%s\n[任务] %s | %s → %s | period=%d conditional=%s\n%s",
                "=" * 60,
                key,
                start,
                end,
                period,
                conditional,
                "=" * 60,
            )
            out_dir.mkdir(parents=True, exist_ok=True)
            pipeline = FactorEvaluationPipeline(
                start_date=start,
                end_date=end,
                period=period,
                conditional=conditional,
                invert_negative=True,
                plot=False,
                output_dir=str(out_dir),
                workers=workers,
            )
            await pipeline.run()
            done[key] = out_dir / "summary.csv"

    await asyncio.gather(*[_one(j) for j in jobs])

    report_path = generate_compare_report(root, done)
    logger.info("对照测评完成: %s", root)
    logger.info("报告: %s", report_path)
    return report_path


def generate_compare_report(root: Path, done: Dict[str, Path]) -> Path:
    """生成历史 A 因子 × 多口径 × 三年对照表。"""
    rows: List[dict] = []
    for key, csv_path in sorted(done.items()):
        if not csv_path.exists():
            continue
        mode, ylabel = key.split("/", 1)
        df = pd.read_csv(csv_path)
        df["factor_key"] = df["layer"] + "." + df["field"]
        for fk in HISTORICAL_A_FACTORS:
            m = df[df["factor_key"] == fk]
            if m.empty:
                continue
            r = m.iloc[0]
            rows.append(
                {
                    "factor": fk,
                    "mode": mode,
                    "year": ylabel,
                    "score": r.get("Total_Score"),
                    "grade": grade_letter(r.get("Grade")),
                    "rank_icir": r.get("Rank_ICIR"),
                    "rank_ic": r.get("Rank_IC_Mean"),
                    "n": r.get("N_Periods"),
                }
            )

    detail = pd.DataFrame(rows)
    report_path = BASE / f"factor_compare_report_{datetime.now().strftime('%Y%m%d')}.md"

    lines: List[str] = []
    lines.append("# 因子多口径对照测评报告（对齐 factor_ab_history）")
    lines.append("")
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"数据目录: `{root}`")
    lines.append("")
    lines.append("## 测评模式")
    lines.append("")
    lines.append("| 模式 | period | 宇宙 |")
    lines.append("|------|--------|------|")
    lines.append("| cond_p5/p10/p20 | 5/10/20 | Top3板块∩主板（条件宇宙） |")
    lines.append("| full_p5/p10/p20 | 5/10/20 | 全市场（--no-conditional） |")
    lines.append("")
    lines.append("## 历史 A 因子：各口径最高评级")
    lines.append("")

    if not detail.empty:
        best = (
            detail.sort_values("score", ascending=False)
            .groupby("factor")
            .first()
            .reset_index()
        )
        lines.append("| 因子 | 最优模式 | 年份 | 得分 | 评级 | Rank ICIR |")
        lines.append("|------|----------|------|------|------|-----------|")
        for _, r in best.iterrows():
            icir = r["rank_icir"]
            icir_s = f"{icir:.3f}" if pd.notna(icir) else "-"
            sc = int(r["score"]) if pd.notna(r["score"]) else "-"
            lines.append(
                f"| {r['factor']} | {r['mode']} | {r['year']} | {sc} | {r['grade']} | {icir_s} |"
            )
        lines.append("")

        lines.append("## 历史 A 因子完整矩阵（得分/评级）")
        lines.append("")
        for fk in HISTORICAL_A_FACTORS:
            sub = detail[detail["factor"] == fk]
            if sub.empty:
                continue
            lines.append(f"### {fk}")
            lines.append("")
            lines.append("| 模式 | 2024 | 2025 | 2026/2026_YTD |")
            lines.append("|------|------|------|----------------|")
            for mode in [m[0] for m in MODES]:
                cells = []
                for yl in ["2024", "2025", "2026_YTD"]:
                    cell_df = sub[(sub["mode"] == mode) & (sub["year"] == yl)]
                    if cell_df.empty:
                        cells.append("-")
                    else:
                        r = cell_df.iloc[0]
                        sc = r["score"]
                        g = r["grade"]
                        cells.append(
                            f"{int(sc)}/{g}" if pd.notna(sc) else g
                        )
                lines.append(f"| {mode} | {cells[0]} | {cells[1]} | {cells[2]} |")
            lines.append("")

        # 全因子各模式 A/B 计数
        lines.append("## 各模式 A/B 因子数量（50 字段）")
        lines.append("")
        lines.append("| 模式 | 年份 | A | B | C | D |")
        lines.append("|------|------|---|---|---|---|")
        for key, csv_path in sorted(done.items()):
            if not csv_path.exists():
                continue
            mode, ylabel = key.split("/", 1)
            df = pd.read_csv(csv_path)
            grades = [grade_letter(g) for g in df["Grade"]]
            cnt = {g: grades.count(g) for g in ["A", "B", "C", "D", "N/A"]}
            lines.append(
                f"| {mode} | {ylabel} | {cnt['A']} | {cnt['B']} | {cnt['C']} | {cnt['D'] + cnt['N/A']} |"
            )
        lines.append("")

    lines.append("## 说明")
    lines.append("")
    lines.append("- 历史 `factor_ab_history.md` 的 A 多来自 **period=10/20** 或 **全市场** 口径，与 cond_p5 不可直接比。")
    lines.append("- 标准 A 线：Total_Score ≥ 80；66~73 分为 B。")
    lines.append("- 2026 区间截止 2026-07-27（因子/L2 数据交集）。")
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    detail.to_csv(root / "historical_a_factors_detail.csv", index=False, encoding="utf-8-sig")
    return report_path


async def async_main(args: argparse.Namespace) -> int:
    try:
        from app.core import database as db_module

        await db_module.db_manager.init_mongodb()
    except Exception as e:
        logger.error("数据库初始化失败: %s", e)
        return 1

    modes = MODES
    if args.modes:
        name_set = {x.strip() for x in args.modes.split(",") if x.strip()}
        modes = [m for m in MODES if m[0] in name_set]
        if not modes:
            logger.error("无有效模式: %s", args.modes)
            return 1

    years = [int(y.strip()) for y in args.years.split(",") if y.strip()]

    report = await run_all_jobs(
        modes=modes,
        years=years,
        workers=args.workers,
        job_parallel=args.job_parallel,
        output_root=Path(args.output) if args.output else None,
    )
    print(report)
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )
    p = argparse.ArgumentParser(description="多口径因子测评批量对照")
    p.add_argument("--years", default="2024,2025,2026", help="年份，逗号分隔")
    p.add_argument(
        "--modes",
        default=None,
        help="模式子集，如 cond_p10,full_p5（默认全部 6 种）",
    )
    p.add_argument("--workers", type=int, default=None, help="单任务因子并发数")
    p.add_argument("--job-parallel", type=int, default=8, help="任务级并发数，默认 8")
    p.add_argument("--output", default=None, help="输出根目录")
    args = p.parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    sys.exit(main())
