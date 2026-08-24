"""从 summary.csv 生成 2024/2025/2026 三年因子测评对比报告（含多口径对照）。"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from zstock.factor_management.script.因子测评.run_compare_eval import (
    HISTORICAL_A_FACTORS,
    MODES,
)

BASE = Path(__file__).resolve().parent
DEFAULT_COMPARE_ROOT = BASE / "output/compare_eval_20260823_174008"
SOURCES = {
    "2024": BASE / "output/eval_batch_20260823_160640/2024/summary.csv",
    "2025": BASE / "output/eval_batch_20260823_160640/2025/summary.csv",
    "2026": BASE / "output/eval_2026_YTD_fixed/summary.csv",
}
WINDOWS = {
    "2024": "2024-01-01 ~ 2024-12-31",
    "2025": "2025-01-02 ~ 2025-12-31",
    "2026": "2026-01-01 ~ 2026-07-27",
}
SOURCE_NOTES = {
    "2024": "output/eval_batch_20260823_160640/2024",
    "2025": "output/eval_batch_20260823_160640/2025",
    "2026": "output/eval_2026_YTD_fixed（turnover_rate 修复后重算）",
}
YEARS = ["2024", "2025", "2026"]
YEAR_LABELS = ["2024", "2025", "2026_YTD"]


def grade_letter(g) -> str:
    if g is None or (isinstance(g, float) and pd.isna(g)):
        return "N/A"
    s = str(g)
    if " - " in s:
        return s.split(" - ")[0].strip()
    if s.startswith("N/A"):
        return "N/A"
    return s[:1] if s else "N/A"


def load_data() -> dict[str, pd.DataFrame]:
    dfs: dict[str, pd.DataFrame] = {}
    for y, path in SOURCES.items():
        if not path.exists():
            raise FileNotFoundError(f"缺少 {y} 测评结果: {path}")
        df = pd.read_csv(path)
        df["factor_key"] = df["layer"] + "." + df["field"]
        dfs[y] = df
    return dfs


def build_merged(dfs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    keys = sorted(set().union(*(set(d["factor_key"]) for d in dfs.values())))
    rows = []
    for k in keys:
        layer, field = k.split(".", 1)
        row: dict = {"layer": layer, "field": field, "factor": k}
        for y in YEARS:
            m = dfs[y][dfs[y]["factor_key"] == k]
            if m.empty:
                row[f"{y}_score"] = None
                row[f"{y}_grade"] = "缺失"
                row[f"{y}_ric"] = None
                row[f"{y}_n"] = None
            else:
                r = m.iloc[0]
                row[f"{y}_score"] = r.get("Total_Score")
                row[f"{y}_grade"] = grade_letter(r.get("Grade"))
                ric = r.get("Rank_IC_Mean")
                row[f"{y}_ric"] = ric if pd.notna(ric) else None
                n = r.get("N_Periods")
                row[f"{y}_n"] = int(n) if pd.notna(n) else 0
        rows.append(row)
    return pd.DataFrame(rows)


def fmt_cell(score, grade: str) -> str:
    if score is None or (isinstance(score, float) and pd.isna(score)):
        return grade
    return f"{int(score)}/{grade}"


def fmt_ric(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "-"
    return f"{float(v):.4f}"


def load_compare_summaries(compare_root: Path) -> Dict[str, Path]:
    done: Dict[str, Path] = {}
    if not compare_root.exists():
        return done
    for mode, _, _ in MODES:
        mode_dir = compare_root / mode
        if not mode_dir.is_dir():
            continue
        for ylabel in YEAR_LABELS:
            csv_path = mode_dir / ylabel / "summary.csv"
            if csv_path.exists():
                done[f"{mode}/{ylabel}"] = csv_path
    return done


def build_compare_detail(done: Dict[str, Path]) -> pd.DataFrame:
    rows: List[dict] = []
    for key, csv_path in sorted(done.items()):
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
                }
            )
    return pd.DataFrame(rows)


def append_compare_sections(
    lines: List[str],
    compare_root: Optional[Path],
    done: Dict[str, Path],
    detail: pd.DataFrame,
) -> None:
    if not compare_root or not done:
        lines.append("## 多口径对照（对齐 factor_ab_history）")
        lines.append("")
        lines.append("未找到对照测评数据，请先运行 `run_compare_eval`。")
        lines.append("")
        return

    lines.append("## 多口径对照（对齐 factor_ab_history）")
    lines.append("")
    lines.append(f"数据目录: `{compare_root}`")
    lines.append("")
    lines.append("### 测评模式")
    lines.append("")
    lines.append("| 模式 | period | 宇宙 |")
    lines.append("|------|--------|------|")
    lines.append("| cond_p5/p10/p20 | 5/10/20 | Top3板块∩主板（条件宇宙） |")
    lines.append("| full_p5/p10/p20 | 5/10/20 | 全市场（--no-conditional） |")
    lines.append("")
    lines.append("> 上文各节为 **cond_p5**（标准生产口径）；本节补充 period=10/20 与全市场对照。")
    lines.append("")

    if detail.empty:
        lines.append("对照明细为空。")
        lines.append("")
        return

    lines.append("### 历史 A 因子：各口径最高评级")
    lines.append("")
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

    lines.append("### 历史 A 因子完整矩阵（得分/评级）")
    lines.append("")
    for fk in HISTORICAL_A_FACTORS:
        sub = detail[detail["factor"] == fk]
        if sub.empty:
            continue
        lines.append(f"#### {fk}")
        lines.append("")
        lines.append("| 模式 | 2024 | 2025 | 2026/2026_YTD |")
        lines.append("|------|------|------|----------------|")
        for mode, _, _ in MODES:
            cells = []
            for yl in YEAR_LABELS:
                cell_df = sub[(sub["mode"] == mode) & (sub["year"] == yl)]
                if cell_df.empty:
                    cells.append("-")
                else:
                    r = cell_df.iloc[0]
                    sc = r["score"]
                    g = r["grade"]
                    cells.append(f"{int(sc)}/{g}" if pd.notna(sc) else g)
            lines.append(f"| {mode} | {cells[0]} | {cells[1]} | {cells[2]} |")
        lines.append("")

    lines.append("### 各模式 A/B 因子数量（50 字段）")
    lines.append("")
    lines.append("| 模式 | 年份 | A | B | C | D |")
    lines.append("|------|------|---|---|---|---|")
    for key, csv_path in sorted(done.items()):
        mode, ylabel = key.split("/", 1)
        df = pd.read_csv(csv_path)
        grades = [grade_letter(g) for g in df["Grade"]]
        cnt = {g: grades.count(g) for g in ["A", "B", "C", "D", "N/A"]}
        lines.append(
            f"| {mode} | {ylabel} | {cnt['A']} | {cnt['B']} | {cnt['C']} | {cnt['D'] + cnt['N/A']} |"
        )
    lines.append("")
    lines.append("### 对照说明")
    lines.append("")
    lines.append("- 历史 `factor_ab_history.md` 的 A 多来自 **period=10/20** 或 **全市场** 口径，与 cond_p5 不可直接比。")
    lines.append("- 标准 A 线：Total_Score ≥ 80；66~73 分为 B。")
    lines.append("- 2026 区间截止 2026-07-27（因子/L2 数据交集）。")
    lines.append("")


def generate_report(compare_root: Optional[Path] = None) -> Path:
    dfs = load_data()
    merged = build_merged(dfs)
    out_path = BASE / f"factor_evaluation_report_2024_2025_2026_{datetime.now().strftime('%Y%m%d')}.md"

    lines: list[str] = []
    lines.append("# 因子有效性测评报告（2024 / 2025 / 2026）")
    lines.append("")
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append("## 测评配置")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append("| 主报告口径 | cond_p5（预测周期 5 日，条件宇宙） |")
    lines.append("| 条件宇宙 | Top3 板块 ∩ 主板非 ST（sector 层全市场） |")
    lines.append("| 极性取反 | f34 多窗口 / f30 / f36 |")
    lines.append("| 分层数 | 5 |")
    lines.append("| 因子字段数 | 50（sector 15 + dragon 20 + force 15） |")
    lines.append("")
    lines.append("## 测评区间")
    lines.append("")
    for y in YEARS:
        lines.append(f"- **{y}**: {WINDOWS[y]}（`{SOURCE_NOTES[y]}`）")
    lines.append("")
    lines.append("## 各年评级统计")
    lines.append("")
    lines.append("| 年份 | A | B | C | D | N/A | 合计 |")
    lines.append("|------|---|---|---|---|---|------|")
    for y in YEARS:
        grades = [grade_letter(dfs[y].loc[i, "Grade"]) for i in dfs[y].index]
        cnt = {g: grades.count(g) for g in ["A", "B", "C", "D", "N/A"]}
        lines.append(
            f"| {y} | {cnt['A']} | {cnt['B']} | {cnt['C']} | {cnt['D']} | {cnt['N/A']} | {len(grades)} |"
        )
    lines.append("")

    lines.append("## 各年 A/B 级因子")
    lines.append("")
    for y in YEARS:
        df = dfs[y].copy()
        df["g"] = df["Grade"].apply(grade_letter)
        ab = df[df["g"].isin(["A", "B"])].sort_values("Total_Score", ascending=False)
        lines.append(f"### {y}（{len(ab)} 个）")
        lines.append("")
        if ab.empty:
            lines.append("无 A/B 级因子。")
        else:
            lines.append("| 因子 | 得分 | 评级 | Rank IC Mean | Rank ICIR | N |")
            lines.append("|------|------|------|--------------|-----------|---|")
            for _, r in ab.iterrows():
                ric = r.get("Rank_IC_Mean")
                rir = r.get("Rank_ICIR")
                ric_s = f"{ric:.4f}" if pd.notna(ric) else "-"
                rir_s = f"{rir:.3f}" if pd.notna(rir) else "-"
                n = int(r["N_Periods"]) if pd.notna(r["N_Periods"]) else 0
                lines.append(
                    f"| {r['layer']}.{r['field']} | {int(r['Total_Score'])} | {r['g']} | "
                    f"{ric_s} | {rir_s} | {n} |"
                )
        lines.append("")

    lines.append("## 三年均为 B 级及以上")
    lines.append("")
    stable = [r for _, r in merged.iterrows() if all(r[f"{y}_grade"] in ("A", "B") for y in YEARS)]
    if not stable:
        lines.append("无因子在 2024/2025/2026 三年同时达到 B+。")
    else:
        lines.append("| 因子 | 2024 | 2025 | 2026 |")
        lines.append("|------|------|------|------|")
        for r in stable:
            lines.append(
                f"| {r['factor']} | {fmt_cell(r['2024_score'], r['2024_grade'])} | "
                f"{fmt_cell(r['2025_score'], r['2025_grade'])} | "
                f"{fmt_cell(r['2026_score'], r['2026_grade'])} |"
            )
    lines.append("")

    lines.append("## 2024 或 2025 为 B+ 但 2026 未达 B（风格切换）")
    lines.append("")
    dropped = []
    for _, r in merged.iterrows():
        hist_ab = r["2024_grade"] in ("A", "B") or r["2025_grade"] in ("A", "B")
        if hist_ab and r["2026_grade"] not in ("A", "B"):
            dropped.append(r)
    dropped.sort(
        key=lambda x: (x.get("2024_score") or 0) + (x.get("2025_score") or 0),
        reverse=True,
    )
    if not dropped:
        lines.append("无。")
    else:
        lines.append("| 因子 | 2024 | 2025 | 2026 |")
        lines.append("|------|------|------|------|")
        for r in dropped:
            lines.append(
                f"| {r['factor']} | {fmt_cell(r['2024_score'], r['2024_grade'])} | "
                f"{fmt_cell(r['2025_score'], r['2025_grade'])} | "
                f"{fmt_cell(r['2026_score'], r['2026_grade'])} |"
            )
    lines.append("")

    lines.append("## 全因子三年对比（50 字段）")
    lines.append("")
    for layer in ["sector", "dragon", "force"]:
        sub = merged[merged["layer"] == layer].sort_values("field")
        lines.append(f"### {layer.upper()} 层")
        lines.append("")
        lines.append(
            "| 因子 | 2024 得分/评级 | 2025 得分/评级 | 2026 得分/评级 | "
            "2024 RankIC | 2025 RankIC | 2026 RankIC |"
        )
        lines.append("|------|----------------|----------------|----------------|-------------|-------------|-------------|")
        for _, r in sub.iterrows():
            lines.append(
                f"| {r['field']} | {fmt_cell(r['2024_score'], r['2024_grade'])} | "
                f"{fmt_cell(r['2025_score'], r['2025_grade'])} | "
                f"{fmt_cell(r['2026_score'], r['2026_grade'])} | "
                f"{fmt_ric(r['2024_ric'])} | {fmt_ric(r['2025_ric'])} | {fmt_ric(r['2026_ric'])} |"
            )
        lines.append("")

    lines.append("## 数据质量说明")
    lines.append("")
    lines.append("| 因子 | 2024 | 2025 | 2026 | 说明 |")
    lines.append("|------|------|------|------|------|")
    lines.append("| dragon.f38_turnover_anomaly | N/A | B(66) | D(23) | 2024 缺 turnover_rate；2026 已修复 |")
    lines.append("| force.fcoop4_turnover_quality | D(N=0) | D(31) | D(31) | 2024 常数 IC；2026 已修复 |")
    lines.append("| force.longhu_board_bonus | D(N=0) | D(N=0) | D(N=0) | 需 QMT 环境 sync_lhb |")
    lines.append("")

    root = compare_root or DEFAULT_COMPARE_ROOT
    done = load_compare_summaries(root)
    detail = build_compare_detail(done)
    append_compare_sections(lines, root if done else None, done, detail)

    lines.append("## 结论摘要")
    lines.append("")
    lines.append("1. **2024（cond_p5）**：`dragon.f33_consecutive_boards` 唯一 A(83)；f31 系列、f32、f36、f37、f_mean_reversion 等多因子 B 级。")
    lines.append("2. **2025（cond_p5）**：`f33` B(73)、`f38` B(66)、`f_mean_reversion_signal` B(66)；龙头因子仍有效。")
    lines.append("3. **2026 YTD（cond_p5）**：仅 `force.dragon_consistency_5d` B(65)；多数历史强势龙头因子降至 C/D。")
    lines.append("4. **板块层** `f30_sector_concentration` 三年 C~B 边缘，是相对稳定的 sector 因子。")
    if not detail.empty:
        lines.append(
            "5. **多口径对照**：2024 年 `f33` 在 cond/full × p5/p10/p20 均可达 A；"
            "`f35_bollinger_trend` 仅在 **cond_p20** 达 A(83)，cond_p5 仅 B(65)——"
            "与 `factor_ab_history` 混用 period 口径一致。"
        )
        lines.append(
            "6. **历史 A 复现**：`f34` 系列、`f_mean_reversion` 在 p20/全市场最高 B(73)；"
            "2026 年仅 `f36_identity_premium` 在 cond_p20/full_p20 仍 B(73)。"
        )
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="生成三年因子测评报告（含多口径对照）")
    parser.add_argument(
        "--compare-dir",
        default=str(DEFAULT_COMPARE_ROOT),
        help="对照测评输出目录（compare_eval_*）",
    )
    args = parser.parse_args()
    compare_path = Path(args.compare_dir) if args.compare_dir else None
    path = generate_report(compare_path)
    print(path)
