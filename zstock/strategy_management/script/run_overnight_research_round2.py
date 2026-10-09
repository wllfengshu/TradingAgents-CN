"""
夜间研究 round2：在 v123_exposure / v123_naked 之间找可进 prod 的折中。

    python -m zstock.strategy_management.script.run_overnight_research_round2
    python -m zstock.strategy_management.script.run_overnight_research_round2 --resume output/overnight_round2_YYYYMMDD_HHMMSS
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

ROUND2_BATCH: List[Tuple[str, Path, str]] = [
    ("prod", CONFIG_DIR / "strategy_params.json", "生产基线"),
    (
        "v123_exposure",
        CONFIG_DIR / "strategy_params_v123_exposure.json",
        "round1 最优安全：+5.21% / MDD -7.5%",
    ),
    (
        "v123_naked",
        CONFIG_DIR / "strategy_params_v123_naked_select.json",
        "round1 α 上限：+5.47% / MDD -16%",
    ),
    (
        "v123f",
        CONFIG_DIR / "strategy_params_v123f_momentum_full_te.json",
        "动量 TE 绿黄中性 1/1/0.95",
    ),
    (
        "v123h",
        CONFIG_DIR / "strategy_params_v123h_exposure_no_reduce.json",
        "v123_exposure + 关只减不加",
    ),
    (
        "v123g",
        CONFIG_DIR / "strategy_params_v123g_naked_weak90.json",
        "关 TE + 弱段开 + 黄灯 0.9",
    ),
    (
        "v123k",
        CONFIG_DIR / "strategy_params_v123k_teoff_throttle.json",
        "关 TE + 仅回撤节流 + 黄 0.95",
    ),
    (
        "v123d",
        CONFIG_DIR / "strategy_params_v123d_compromise.json",
        "round1 v123d 对照（未过关）",
    ),
]

KEY_YEARS = ("2020", "2022", "2024", "2025")
# round1 参考（便于 morning 对照，非本批重算）
ROUND1_REF = {
    "prod": (+0.0496, -0.0752),
    "v123_exposure": (+0.0521, -0.0747),
    "v123_naked": (+0.0547, -0.1598),
}


def _cagr(rows: List[Dict[str, Any]]) -> Tuple[float, float, float]:
    ok = [r for r in rows if not r.get("error") and r.get("total_return") is not None]
    if not ok:
        return float("nan"), float("nan"), float("nan")
    prod = 1.0
    worst_mdd = 0.0
    for r in sorted(ok, key=lambda x: x["start"]):
        prod *= 1.0 + float(r["total_return"])
        worst_mdd = min(worst_mdd, float(r.get("max_drawdown") or 0.0))
    n = len(ok)
    cagr = prod ** (1.0 / n) - 1.0 if n > 0 and prod > 0 else float("nan")
    return prod - 1.0, cagr, worst_mdd


def _year_ret(rows: List[Dict[str, Any]], year: str) -> float | None:
    for r in rows:
        if r.get("start", "").startswith(year) and r.get("total_return") is not None:
            return float(r["total_return"])
    return None


def _promote_candidates(by_label: Dict[str, List[Dict[str, Any]]]) -> List[str]:
    lines: List[str] = []
    prod_rows = by_label.get("prod", [])
    _, cagr_p, mdd_p = _cagr(prod_rows)
    best_name = "prod"
    best_cagr = cagr_p
    best_mdd = mdd_p
    for label, rows in by_label.items():
        if label == "prod" or not rows:
            continue
        _, c, m = _cagr(rows)
        if c != c or m != m:
            continue
        if c > best_cagr + 0.002 and m >= -0.20:
            best_name, best_cagr, best_mdd = label, c, m
    lines.append(
        f"相对 prod(CAGR {cagr_p:+.2%} MDD {mdd_p:+.2%})，"
        f"本批最优且 MDD≥-20%: {best_name} → CAGR {best_cagr:+.2%} MDD {best_mdd:+.2%}"
    )
    exp = by_label.get("v123_exposure", [])
    if exp:
        _, c_e, m_e = _cagr(exp)
        if c_e > cagr_p and m_e >= mdd_p - 0.01:
            lines.append("→ v123_exposure 仍安全领先时，优先微调 f/h，勿回 naked 全局满仓。")
    naked = by_label.get("v123_naked", [])
    for label in ("v123g", "v123k", "v123f"):
        rows = by_label.get(label, [])
        if not rows or not naked:
            continue
        _, c_n, m_n = _cagr(naked)
        _, c_x, m_x = _cagr(rows)
        if c_x >= c_n - 0.003 and m_x > m_n + 0.02:
            lines.append(
                f"→ {label} 接近 naked 收益且 MDD 更浅：折中候选（CAGR {c_x:+.2%} MDD {m_x:+.2%}）。"
            )
    lines.append("进 prod 门槛: 7y CAGR > prod +0.3% 且 worstMDD ≥ -20%，2020/2025 不可崩。")
    return lines


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="夜间研究 round2")
    p.add_argument("--resume", type=Path, default=None)
    return p.parse_args()


async def async_main() -> int:
    args = _parse_args()
    if args.resume is not None:
        out_dir = args.resume.resolve()
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path(__file__).resolve().parent / "output" / f"overnight_round2_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    _setup_logging(out_dir / "run.log")

    manifest = [
        {"label": lb, "path": str(p), "hypothesis": h}
        for lb, p, h in ROUND2_BATCH
        if p.is_file()
    ]
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.strategy_management.script.backtest_report import write_backtest_report

    await init_zstock_database()
    end_2026 = await _latest_trade_date_in_factors()
    year_ranges = _year_ranges(end_2026)

    all_rows: List[Dict[str, Any]] = []
    by_label: Dict[str, List[Dict[str, Any]]] = {}

    lines = [
        "overnight_research_round2",
        f"生成: {datetime.now():%Y-%m-%d %H:%M}",
        f"2026 截止: {end_2026}",
        "",
        "round1 参考 (CAGR, worstMDD):",
    ]
    for k, (c, m) in ROUND1_REF.items():
        lines.append(f"  {k}: {c:+.2%}, {m:+.2%}")
    lines.extend(
        [
            "",
            f"{'配置':<14} {'7yCAGR':>8} {'累计':>10} {'worstMDD':>10} "
            + " ".join(f"{y:>7}" for y in KEY_YEARS),
            "-" * 70,
        ]
    )

    t0 = time.time()
    jobs = [(lb, p) for lb, p, _ in ROUND2_BATCH if p.is_file()]
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
                logger.info("[%d/%d] skip %s %s", job_i, n_jobs, label, year)
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
            title=f"round2 {label}",
            subtitle=f"截止 {end_2026}",
        )
        total, cagr, worst_mdd = _cagr(cfg_rows)
        yr = []
        for y in KEY_YEARS:
            v = _year_ret(cfg_rows, y)
            yr.append(f"{v:+.2%}" if v is not None else "n/a")
        lines.append(
            f"{label:<14} {cagr:>+7.2%} {total:>+9.2%} {worst_mdd:>+9.2%} "
            + " ".join(f"{x:>7}" for x in yr)
        )

    lines.extend(["", "=== 晋级解读 ==="])
    lines.extend(_promote_candidates(by_label))
    lines.append("")
    lines.append(f"总用时 {(time.time() - t0) / 60:.1f} 分钟")
    lines.append(f"目录: {out_dir}")

    text = "\n".join(lines)
    (out_dir / "round2_summary.txt").write_text(text, encoding="utf-8")
    (out_dir / "round2_all_results.json").write_text(
        json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("\n%s", text)
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
