"""
v1.20.1 归因：2024 季度 prod vs v119 + 2025 ablation + 信号漏斗。

    .venv\\Scripts\\python.exe -m zstock.strategy_management.script.run_attribution_followup
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import sys
import time
import traceback
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.strategy_management.script.run_history_2020_2026 import (  # noqa: E402
    _run_one,
    _setup_logging,
)

logger = logging.getLogger(__name__)

CONFIG_DIR = PROJECT_ROOT / "zstock" / "common" / "config"
PARAMS_PROD = CONFIG_DIR / "strategy_params.json"
PARAMS_V119 = CONFIG_DIR / "strategy_params_v119_frozen.json"

QUARTERS_2024: List[Tuple[str, str, str]] = [
    ("2024_Q1", "2024-01-01", "2024-03-31"),
    ("2024_Q2", "2024-04-01", "2024-06-30"),
    ("2024_Q3", "2024-07-01", "2024-09-30"),
    ("2024_Q4", "2024-10-01", "2024-12-31"),
]

HS300 = {
    "2024": 0.1620,
    "2025": 0.21190086365415106,
}


def _clear_caches() -> None:
    from zstock.common.config import strategy_config
    from zstock.strategy_management.pipeline import StrategyPipeline
    from zstock.strategy_management.script import backtester as bt_mod

    StrategyPipeline._config_cache = None
    strategy_config._clear_cache()
    bt_mod._CONFIG_CACHE = None


def _load_params(path: Path) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_temp_params(name: str, params: Dict[str, Any], out_dir: Path) -> Path:
    p = out_dir / name
    p.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _ablation_variants(out_dir: Path) -> List[Tuple[str, Path]]:
    prod = _load_params(PARAMS_PROD)
    v119 = _load_params(PARAMS_V119)

    no_te = copy.deepcopy(prod)
    te = dict(no_te.get("target_equity") or {})
    te["enabled"] = False
    no_te["target_equity"] = te

    overlay_v119 = copy.deepcopy(prod)
    overlay_v119["market_overlay"] = copy.deepcopy(v119.get("market_overlay") or {})
    te2 = dict(overlay_v119.get("target_equity") or {})
    te2["enabled"] = False
    overlay_v119["target_equity"] = te2

    v119_te_prod = copy.deepcopy(v119)
    if prod.get("target_equity"):
        v119_te_prod["target_equity"] = copy.deepcopy(prod["target_equity"])

    return [
        ("prod_full", PARAMS_PROD),
        ("v119_frozen", PARAMS_V119),
        ("prod_no_target_equity", _write_temp_params("tmp_prod_no_te.json", no_te, out_dir)),
        ("prod_v119_overlay_no_te", _write_temp_params("tmp_prod_v119_ov.json", overlay_v119, out_dir)),
        ("v119_plus_target_equity", _write_temp_params("tmp_v119_te.json", v119_te_prod, out_dir)),
    ]


async def _funnel_compare(
    dates: List[str],
    tags: List[Tuple[str, Path]],
) -> List[Dict[str, Any]]:
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline

    rows: List[Dict[str, Any]] = []
    for tag, path in tags:
        _clear_caches()
        pipe = CrossSectionStrategyPipeline(config_path=str(path))
        await pipe.preload_precomputed_factors(dates[0], dates[-1])
        logger.info("漏斗 %s (%d 日)", tag, len(dates))
        for i, td in enumerate(dates, start=1):
            try:
                df = await pipe.score_signals(td)
            except Exception as e:
                rows.append(
                    {
                        "tag": tag,
                        "trade_date": td,
                        "grade": "error",
                        "regime": "error",
                        "n_m3": 0,
                        "n_m4": 0,
                        "n_buy": 0,
                        "stop": f"error:{type(e).__name__}",
                    }
                )
                continue
            rows.append(
                {
                    "tag": tag,
                    "trade_date": td,
                    "grade": str(df.attrs.get("market_grade", "?")),
                    "regime": str(df.attrs.get("regime", "?")),
                    "n_m3": int(df.attrs.get("funnel_n_m3", 0) or 0),
                    "n_m4": int(df.attrs.get("funnel_n_m4", 0) or 0),
                    "n_buy": int(df.attrs.get("funnel_n_buy", 0) or 0),
                    "stop": str(df.attrs.get("funnel_stop", "?")),
                }
            )
            if i % 50 == 0:
                logger.info("  [%s] %d/%d", tag, i, len(dates))
    return rows


def _funnel_summary(rows: List[Dict[str, Any]], tag: str) -> str:
    ys = [r for r in rows if r.get("tag") == tag]
    if not ys:
        return f"{tag}: 无数据"
    grades = Counter(r["grade"] for r in ys)
    stops = Counter(r["stop"] for r in ys)
    non_red = [r for r in ys if r["grade"] != "red" and not str(r["stop"]).startswith("error")]
    m3 = [r["n_m3"] for r in non_red]
    m4 = [r["n_m4"] for r in non_red]
    buy = [r["n_buy"] for r in non_red]
    mom_g = [r for r in non_red if r["regime"] == "momentum" and r["grade"] == "green"]
    mg_buy = sum(r["n_buy"] for r in mom_g) / len(mom_g) if mom_g else 0.0
    pass_pct = (sum(m4) / sum(m3) * 100.0) if m3 and sum(m3) > 0 else 0.0
    return (
        f"{tag}: green/yellow/red={grades.get('green', 0)}/{grades.get('yellow', 0)}/"
        f"{grades.get('red', 0)} | ok={stops.get('ok', 0)} m4_empty={stops.get('m4_empty', 0)} | "
        f"非红 M3均={sum(m3)/len(m3):.1f} M4均={sum(m4)/len(m4):.1f} buy均={sum(buy)/len(buy):.1f} "
        f"M4通过率={pass_pct:.1f}% | momentum+green buy均={mg_buy:.1f} (n={len(mom_g)})"
    )


def _interpret(
    q_rows: List[Dict[str, Any]],
    y2025: List[Dict[str, Any]],
    funnel_lines: List[str],
) -> List[str]:
    lines = ["── 自动解读（规则型，需结合表内数字）──", ""]

    by_q: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for r in q_rows:
        if r.get("error"):
            continue
        by_q.setdefault(str(r.get("quarter", "")), {})[r["label"]] = r

    for qkey in sorted(by_q.keys()):
        pair = by_q.get(qkey, {})
        p = pair.get("prod")
        v = pair.get("v119")
        if not p or not v:
            continue
        dp = float(p["total_return"]) - float(v["total_return"])
        if abs(dp) >= 0.02:
            who = "prod" if dp > 0 else "v119"
            lines.append(
                f"  {qkey}: {who} 领先 {abs(dp)*100:.2f}pp "
                f"(prod {float(p['total_return'])*100:+.2f}% vs v119 {float(v['total_return'])*100:+.2f}%)"
            )

    prod25 = next((r for r in y2025 if r["label"] == "prod_full" and not r.get("error")), None)
    v11925 = next((r for r in y2025 if r["label"] == "v119_frozen" and not r.get("error")), None)
    no_te = next((r for r in y2025 if r["label"] == "prod_no_target_equity" and not r.get("error")), None)
    ov = next((r for r in y2025 if r["label"] == "prod_v119_overlay_no_te" and not r.get("error")), None)
    vte = next((r for r in y2025 if r["label"] == "v119_plus_target_equity" and not r.get("error")), None)

    if prod25 and v11925:
        lines.append(
            f"  2025 全年: prod {float(prod25['total_return'])*100:+.2f}% vs v119 "
            f"{float(v11925['total_return'])*100:+.2f}% (HS300 +{HS300['2025']*100:.2f}%)"
        )
    if prod25 and no_te and float(no_te["total_return"]) > float(prod25["total_return"]) + 0.005:
        lines.append(
            "  → prod 关闭 target_equity 后 2025 上升：v1.20 overlay 与 target_equity 叠加可能压仓位。"
        )
    if prod25 and ov and float(ov["total_return"]) > float(prod25["total_return"]) + 0.005:
        lines.append("  → prod 改用 v119 overlay 且关 target_equity 后 2025 更好。")
    if v11925 and vte and float(vte["total_return"]) > float(v11925["total_return"]) + 0.005:
        lines.append(
            "  → v119 叠加 prod 的 target_equity 后 2025 更好：与「prod 关 TE」并不矛盾，"
            "需看 overlay/profiles 组合。"
        )

    lines.extend(["", "── 漏斗摘要 ──", ""])
    lines.extend(funnel_lines)
    lines.append("")
    return lines


async def async_main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-funnel", action="store_true", help="跳过漏斗（省时）")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(__file__).resolve().parent / "output" / f"attribution_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    _setup_logging(out_dir / "run.log")

    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.data_management.query_service import COL_FACTOR_MARKET, get_data_query_service

    await init_zstock_database()
    try:
        await get_data_query_service().ensure_indexes()
    except Exception as e:
        logger.warning("索引: %s", e)

    t0 = time.time()
    quarter_rows: List[Dict[str, Any]] = []
    job_n = len(QUARTERS_2024) * 2 + 5
    job_i = 0

    for qlabel, start, end in QUARTERS_2024:
        for label, path in (("prod", PARAMS_PROD), ("v119", PARAMS_V119)):
            job_i += 1
            seg = out_dir / "quarters" / f"{label}_{qlabel}"
            seg.mkdir(parents=True, exist_ok=True)
            logger.info("[%d/%d] 季度 %s %s", job_i, job_n, label, qlabel)
            try:
                row = await _run_one(label, start, end, path, seg)
                row["quarter"] = qlabel
                quarter_rows.append(row)
            except Exception as e:
                logger.error("季度失败: %s", e)
                traceback.print_exc()
                quarter_rows.append(
                    {
                        "label": label,
                        "quarter": qlabel,
                        "start": start,
                        "end": end,
                        "error": str(e),
                    }
                )

    y2025_rows: List[Dict[str, Any]] = []
    start25, end25 = "2025-01-01", "2025-12-31"
    for label, path in _ablation_variants(out_dir):
        job_i += 1
        seg = out_dir / "ablation_2025" / label
        seg.mkdir(parents=True, exist_ok=True)
        logger.info("[%d/%d] 2025 ablation %s", job_i, job_n, label)
        try:
            row = await _run_one(label, start25, end25, path, seg)
            y2025_rows.append(row)
        except Exception as e:
            logger.error("2025 ablation 失败 %s: %s", label, e)
            y2025_rows.append(
                {"label": label, "start": start25, "end": end25, "error": str(e)}
            )

    funnel_lines: List[str] = []
    funnel_raw: List[Dict[str, Any]] = []
    if not args.skip_funnel:
        qs = get_data_query_service()
        db = qs.database_service.db
        for year, ystart, yend in (("2024", "2024-01-01", "2024-12-31"), ("2025", "2025-01-01", "2025-12-31")):
            dates = sorted(
                str(d)
                for d in await db[COL_FACTOR_MARKET].distinct(
                    "trade_date",
                    {"trade_date": {"$gte": ystart, "$lte": yend}},
                )
                if d
            )
            if not dates:
                continue
            logger.info("漏斗 %s %d 日", year, len(dates))
            part = await _funnel_compare(
                dates,
                [("prod", PARAMS_PROD), ("v119", PARAMS_V119)],
            )
            for r in part:
                r["year"] = year
            funnel_raw.extend(part)
            funnel_lines.append(f"[{year}] " + _funnel_summary(part, "prod"))
            funnel_lines.append(f"[{year}] " + _funnel_summary(part, "v119"))

    lines = [
        "归因 follow-up：2024 季度 + 2025 ablation",
        f"生成: {datetime.now():%Y-%m-%d %H:%M}",
        f"prod: {PARAMS_PROD.name} | v119: {PARAMS_V119.name}",
        "",
        "── 2024 分季度（prod vs v119）──",
        f"{'季度':<10} {'配置':<8} {'收益':>8} {'超额vsHS300':>12} {'MDD':>8}",
        "-" * 52,
    ]
    for r in sorted(quarter_rows, key=lambda x: (x.get("quarter", ""), x.get("label", ""))):
        if r.get("error"):
            lines.append(f"{r.get('quarter','?'):<10} {r['label']:<8} ERROR")
            continue
        ex = r.get("excess_vs_index")
        lines.append(
            f"{r['quarter']:<10} {r['label']:<8} {float(r['total_return'])*100:+7.2f}% "
            f"{(float(ex)*100 if ex is not None else float('nan')):+11.2f}% "
            f"{float(r['max_drawdown'])*100:+7.2f}%"
        )

    lines.extend(
        [
            "",
            "── 2025 ablation（全年）──",
            f"{'配置':<28} {'收益':>8} {'vs HS300':>10} {'MDD':>8} {'Sharpe':>7}",
            "-" * 65,
        ]
    )
    for r in y2025_rows:
        if r.get("error"):
            lines.append(f"{r['label']:<28} ERROR")
            continue
        ex = float(r["total_return"]) - HS300["2025"]
        sh = r.get("sharpe")
        shs = f"{float(sh):7.2f}" if sh is not None and sh == sh else "    n/a"
        lines.append(
            f"{r['label']:<28} {float(r['total_return'])*100:+7.2f}% {ex*100:+9.2f}% "
            f"{float(r['max_drawdown'])*100:+7.2f}% {shs}"
        )

    lines.extend(_interpret(quarter_rows, y2025_rows, funnel_lines))
    lines.append(f"\n用时 { (time.time()-t0)/60:.1f} 分钟")

    report = "\n".join(lines)
    (out_dir / "attribution_report.txt").write_text(report, encoding="utf-8")
    (out_dir / "attribution_results.json").write_text(
        json.dumps(
            {"quarters_2024": quarter_rows, "ablation_2025": y2025_rows, "funnel": funnel_raw},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("\n%s", report)
    logger.info("报告: %s", out_dir / "attribution_report.txt")
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
