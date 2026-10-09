"""
夜间研究批：α vs 暴露分解 + v123 候选 × 2020–2026 全历史。

    .venv\\Scripts\\python.exe -m zstock.strategy_management.script.run_overnight_research
    .venv\\Scripts\\python.exe -m zstock.strategy_management.script.run_overnight_research --resume output/overnight_research_YYYYMMDD_HHMMSS
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
from typing import Any, Dict, List, Optional, Tuple

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

# (label, path, 假设/对照)
RESEARCH_BATCH: List[Tuple[str, Path, str]] = [
    ("prod", CONFIG_DIR / "strategy_params.json", "生产基线 v1.20.1"),
    (
        "v123_naked",
        CONFIG_DIR / "strategy_params_v123_naked_select.json",
        "纯选股：无TE/弱段/黄灯缩仓 → α 上限",
    ),
    (
        "v123_no_te",
        CONFIG_DIR / "strategy_params_v123_no_target_equity.json",
        "关 target_equity → 暴露 ablation",
    ),
    (
        "v123_exposure",
        CONFIG_DIR / "strategy_params_v123_exposure.json",
        "动量黄/中性抬高 target",
    ),
    (
        "v123_exposure_m3",
        CONFIG_DIR / "strategy_params_v123_exposure_mom3.json",
        "暴露 + 动量 2–3 只集中",
    ),
    (
        "v123_alpha",
        CONFIG_DIR / "strategy_params_v123_alpha_trim.json",
        "动量 dragon/M4 减 D 级权重",
    ),
    (
        "v122b",
        CONFIG_DIR / "strategy_params_v122b_hybrid.json",
        "手工混合 v122b 对照",
    ),
]

KEY_YEARS = ("2020", "2022", "2024", "2025")


def _cagr(rows: List[Dict[str, Any]]) -> Tuple[float, float, float]:
    ok = [r for r in rows if not r.get("error") and r.get("total_return") is not None]
    if not ok:
        return float("nan"), float("nan"), float("nan")
    prod = 1.0
    worst_mdd = 0.0
    for r in sorted(ok, key=lambda x: x["start"]):
        prod *= 1.0 + float(r["total_return"])
        mdd = float(r.get("max_drawdown") or 0.0)
        worst_mdd = min(worst_mdd, mdd)
    n = len(ok)
    cagr = prod ** (1.0 / n) - 1.0 if n > 0 and prod > 0 else float("nan")
    return prod - 1.0, cagr, worst_mdd


def _year_ret(rows: List[Dict[str, Any]], year: str) -> Optional[float]:
    for r in rows:
        if r.get("start", "").startswith(year) and r.get("total_return") is not None:
            return float(r["total_return"])
    return None


def _interpret(by_label: Dict[str, List[Dict[str, Any]]]) -> List[str]:
    lines = ["", "=== 明日阅读：自动解读（规则型）===", ""]
    prod = by_label.get("prod", [])
    naked = by_label.get("v123_naked", [])
    no_te = by_label.get("v123_no_te", [])
    exp = by_label.get("v123_exposure", [])
    alpha = by_label.get("v123_alpha", [])

    _, cagr_p, mdd_p = _cagr(prod)
    _, cagr_n, _ = _cagr(naked)
    lines.append(f"prod 7y CAGR {cagr_p:+.2%}  worstMDD {mdd_p:+.2%}")

    if naked:
        _, cagr_n, mdd_n = _cagr(naked)
        lines.append(f"v123_naked 7y CAGR {cagr_n:+.2%}  worstMDD {mdd_n:+.2%}")
        for y in ("2024", "2025"):
            rp, rn = _year_ret(prod, y), _year_ret(naked, y)
            if rp is not None and rn is not None:
                lines.append(f"  {y} prod {rp:+.2%} vs naked {rn:+.2%} (Δ {rn-rp:+.2%})")
        if cagr_n <= cagr_p + 0.005:
            lines.append(
                "→ naked 未显著优于 prod：优先改 **暴露/仓位**，单靠放松风控难提 CAGR。"
            )
        else:
            lines.append(
                "→ naked 明显优于 prod：**风控/TE 在拖后腿**，暴露线值得推进。"
            )

    if no_te and prod:
        for y in ("2024", "2025"):
            rp, rt = _year_ret(prod, y), _year_ret(no_te, y)
            if rp is not None and rt is not None and rt - rp > 0.01:
                lines.append(f"→ {y} 关 TE 提升 {rt-rp:+.2%}：target_equity 压制牛年参与。")

    if exp and prod:
        _, c_e, m_e = _cagr(exp)
        lines.append(
            f"v123_exposure 7y CAGR {c_e:+.2%} vs prod {cagr_p:+.2%} (MDD {m_e:+.2%})"
        )
        if c_e > cagr_p + 0.003 and m_e >= -0.20:
            lines.append("→ v123_exposure 过关候选：可再跑分段 MDD 人工确认。")

    if alpha and prod:
        _, c_a, m_a = _cagr(alpha)
        lines.append(
            f"v123_alpha 7y CAGR {c_a:+.2%} vs prod {cagr_p:+.2%} (MDD {m_a:+.2%})"
        )
        if c_a > cagr_p + 0.003:
            lines.append("→ 减因子/对齐 profile 有效：排序线可继续。")
        elif c_a < cagr_p - 0.003:
            lines.append("→ alpha_trim  hurt：默认等权 dragon 勿轻易删。")

    lines.append("")
    lines.append("验收门槛: 7y CAGR > prod ~5% 且 worstMDD ≥ -20% 再考虑进 prod。")
    return lines


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="夜间研究批 v123 α/暴露验证")
    p.add_argument(
        "--resume",
        type=Path,
        default=None,
        help="续跑已有 overnight_research_* 目录",
    )
    return p.parse_args()


async def async_main() -> int:
    args = _parse_args()
    if args.resume is not None:
        out_dir = args.resume.resolve()
        if not out_dir.is_dir():
            logger.error("续跑目录不存在: %s", out_dir)
            return 1
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path(__file__).resolve().parent / "output" / f"overnight_research_{stamp}"
        out_dir.mkdir(parents=True, exist_ok=True)
    _setup_logging(out_dir / "run.log")

    manifest = [
        {"label": lb, "path": str(p), "hypothesis": h}
        for lb, p, h in RESEARCH_BATCH
        if p.is_file()
    ]
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

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
    by_label: Dict[str, List[Dict[str, Any]]] = {}

    summary_lines = [
        "夜间研究批 overnight_research",
        f"生成: {datetime.now():%Y-%m-%d %H:%M}",
        f"2026 截止: {end_2026}",
        "",
        "配置说明:",
    ]
    for lb, path, hyp in RESEARCH_BATCH:
        if path.is_file():
            summary_lines.append(f"  {lb}: {hyp}")
    summary_lines.extend(
        [
            "",
            f"{'配置':<16} {'7yCAGR':>8} {'累计':>10} {'worstMDD':>10} "
            + " ".join(f"{y:>7}" for y in KEY_YEARS),
            "-" * 72,
        ]
    )

    t_all = time.time()
    jobs = [(lb, p) for lb, p, _ in RESEARCH_BATCH if p.is_file()]
    n_jobs = len(jobs) * len(year_ranges)
    job_i = 0

    for label, path in jobs:
        cfg_rows: List[Dict[str, Any]] = []
        for year, start, end in year_ranges:
            job_i += 1
            seg = out_dir / f"{label}_{year}"
            seg.mkdir(parents=True, exist_ok=True)
            summary_path = seg / "summary.json"
            if summary_path.is_file():
                row = json.loads(summary_path.read_text(encoding="utf-8"))
                logger.info("[%d/%d] 跳过 %s %s", job_i, n_jobs, label, year)
            else:
                logger.info("[%d/%d] %s %s", job_i, n_jobs, label, year)
                try:
                    row = await _run_one(label, start, end, path, seg)
                except Exception as e:
                    logger.error("失败 %s %s: %s", label, year, e)
                    traceback.print_exc()
                    row = {"label": label, "start": start, "end": end, "error": str(e)}
            cfg_rows.append(row)
            all_rows.append(row)

        by_label[label] = cfg_rows
        label_dir = out_dir / label
        label_dir.mkdir(parents=True, exist_ok=True)
        write_backtest_report(
            label_dir,
            cfg_rows,
            title=f"研究批 {label}",
            subtitle=f"2026 截止 {end_2026}",
        )
        total, cagr, worst_mdd = _cagr(cfg_rows)
        yr_parts = []
        for y in KEY_YEARS:
            v = _year_ret(cfg_rows, y)
            yr_parts.append(f"{v:+.2%}" if v is not None else "   n/a")
        summary_lines.append(
            f"{label:<16} {cagr:>+7.2%} {total:>+9.2%} {worst_mdd:>+9.2%} "
            + " ".join(f"{x:>7}" for x in yr_parts)
        )

    summary_lines.extend(_interpret(by_label))
    summary_lines.append("")
    summary_lines.append(f"总用时 {(time.time() - t_all) / 60:.1f} 分钟")
    summary_lines.append(f"输出目录: {out_dir}")

    summary_text = "\n".join(summary_lines)
    (out_dir / "research_summary.txt").write_text(summary_text, encoding="utf-8")
    (out_dir / "research_all_results.json").write_text(
        json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("\n%s", summary_text)
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
