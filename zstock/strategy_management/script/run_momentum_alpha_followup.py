"""
主线 A：2024 H1 弱势 + momentum+green 漏斗 → M4 候选 → 2022/2024/2025 短验证。

    python -m zstock.strategy_management.script.run_momentum_alpha_followup
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import sys
import time
from collections import Counter
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
CONFIG = PROJECT_ROOT / "zstock" / "common" / "config"
PARAMS_PROD = CONFIG / "strategy_params.json"
ATTR_Q = (
    PROJECT_ROOT
    / "zstock/strategy_management/script/output/attribution_20260928_132651/attribution_results.json"
)

KEY_YEARS = {"2022", "2024", "2025"}
H1_2024 = ("2024-01-01", "2024-06-30")


def _write_candidates(out_dir: Path) -> List[Tuple[str, Path]]:
    prod = json.loads(PARAMS_PROD.read_text(encoding="utf-8"))

    a = copy.deepcopy(prod)
    coop = dict(a.get("cooperative_force") or {})
    br = dict(coop.get("by_regime") or {})
    mom = dict(br.get("momentum") or {})
    mom["threshold_pct"] = 0.004
    br["momentum"] = mom
    coop["by_regime"] = br
    a["cooperative_force"] = coop
    a["description"] = "v1.21a 动量 M4 门槛 0.004（略宽于 prod 0.005）"
    a["version"] = "1.21.1-a"
    pa = CONFIG / "strategy_params_v121a_mom_m4_004.json"
    pa.write_text(json.dumps(a, ensure_ascii=False, indent=2), encoding="utf-8")

    b = copy.deepcopy(prod)
    coop2 = dict(b.get("cooperative_force") or {})
    coop2["gate_mode_by_grade"] = {"green": "off"}
    b["cooperative_force"] = coop2
    b["description"] = "v1.21b 绿灯日 M4 合力过滤关闭（gate_mode_by_grade.green=off）"
    b["version"] = "1.21.1-b"
    pb = CONFIG / "strategy_params_v121b_m4_green_off.json"
    pb.write_text(json.dumps(b, ensure_ascii=False, indent=2), encoding="utf-8")

    (out_dir / "candidates_note.txt").write_text(
        f"v121a -> {pa.name}\nv121b -> {pb.name}\n", encoding="utf-8"
    )
    return [
        ("prod", PARAMS_PROD),
        ("v121a", pa),
        ("v121b", pb),
    ]


def _h1_report(quarter_rows: List[Dict[str, Any]]) -> List[str]:
    lines = ["── 2024 H1（归因批 Q1+Q2，prod vs v119）──", ""]
    for q in ("2024_Q1", "2024_Q2"):
        for label in ("prod", "v119"):
            r = next(
                (
                    x
                    for x in quarter_rows
                    if x.get("quarter") == q and x.get("label") == label
                ),
                None,
            )
            if not r or r.get("error"):
                continue
            lines.append(
                f"  {q} {label}: 收益 {float(r['total_return'])*100:+.2f}%  "
                f"MDD {float(r['max_drawdown'])*100:+.2f}%  "
                f"超额 {float(r.get('excess_vs_index', 0))*100:+.2f}%"
            )
    lines.append("")
    lines.append(
        "  解读: H1 两版均为小负收益，指数 Q1 为正；弱势来自仓位/参与而非漏斗差异（见 hybrid 漏斗一致）。"
    )
    lines.append("")
    return lines


async def _funnel_h1_mg(start: str, end: str) -> List[str]:
    from zstock.data_management.query_service import COL_FACTOR_MARKET, get_data_query_service
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline

    qs = get_data_query_service()
    db = qs.database_service.db
    dates = sorted(
        str(d)
        for d in await db[COL_FACTOR_MARKET].distinct(
            "trade_date", {"trade_date": {"$gte": start, "$lte": end}}
        )
        if d
    )
    lines = [f"── momentum+green 漏斗 {start}~{end} (prod) ──", ""]
    if not dates:
        lines.append("  无因子日")
        return lines

    pipe = CrossSectionStrategyPipeline(config_path=str(PARAMS_PROD))
    await pipe.preload_precomputed_factors(start, end)
    mg_days = 0
    m4_empty = 0
    buy_sum = 0
    for td in dates:
        df = await pipe.score_signals(td)
        g = str(df.attrs.get("market_grade", "")).lower()
        reg = str(df.attrs.get("regime", "")).lower()
        if g != "green" or reg != "momentum":
            continue
        mg_days += 1
        stop = str(df.attrs.get("funnel_stop", ""))
        if stop == "m4_empty":
            m4_empty += 1
        buy_sum += int(df.attrs.get("funnel_n_buy", 0) or 0)

    avg_buy = buy_sum / mg_days if mg_days else 0.0
    lines.append(f"  momentum+green 日数: {mg_days}")
    lines.append(f"  其中 m4_empty: {m4_empty} ({100*m4_empty/mg_days:.1f}%)" if mg_days else "")
    lines.append(f"  buy 均值: {avg_buy:.2f}")
    lines.append("")
    return lines


def _cagr(rows: List[Dict[str, Any]]) -> float:
    ok = [r for r in rows if not r.get("error") and r.get("total_return") is not None]
    if not ok:
        return float("nan")
    prod = 1.0
    for r in sorted(ok, key=lambda x: x["start"]):
        prod *= 1.0 + float(r["total_return"])
    return prod ** (1.0 / len(ok)) - 1.0


async def async_main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-backtest", action="store_true")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(__file__).resolve().parent / "output" / f"malpha_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    _setup_logging(out_dir / "run.log")

    report_parts: List[str] = [
        "主线 A：2024 H1 + M4 候选",
        f"生成: {datetime.now():%Y-%m-%d %H:%M}",
        "",
    ]

    if ATTR_Q.is_file():
        data = json.loads(ATTR_Q.read_text(encoding="utf-8"))
        report_parts.extend(_h1_report(data.get("quarters_2024") or []))
    else:
        report_parts.append("（未找到 attribution_results.json，跳过 H1 表）")
        report_parts.append("")

    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database

    await init_zstock_database()
    try:
        report_parts.extend(await _funnel_h1_mg(*H1_2024))
    except Exception as e:
        report_parts.append(f"漏斗失败: {e}")
        report_parts.append("")

    batch = _write_candidates(out_dir)
    report_parts.append("── 候选 ──")
    for lb, p in batch:
        report_parts.append(f"  {lb}: {p.name}")
    report_parts.append("")

    all_rows: List[Dict[str, Any]] = []
    by_label: Dict[str, List[Dict[str, Any]]] = {lb: [] for lb, _ in batch}

    if not args.skip_backtest:
        end_2026 = await _latest_trade_date_in_factors()
        ranges = [(y, s, e) for y, s, e in _year_ranges(end_2026) if str(y) in KEY_YEARS]
        n_jobs = len(batch) * len(ranges)
        t0 = time.time()
        job = 0
        for label, path in batch:
            for year, start, end in ranges:
                job += 1
                seg = out_dir / f"{label}_{year}"
                seg.mkdir(parents=True, exist_ok=True)
                logger.info("[%d/%d] %s %s", job, n_jobs, label, year)
                row = await _run_one(label, start, end, path, seg)
                all_rows.append(row)
                by_label[label].append(row)

        years = sorted(KEY_YEARS)
        hdr = f"{'配置':<8} {'三年CAGR':>10}" + "".join(f"{y:>8}" for y in years)
        report_parts.extend(["── 短验证 2022/2024/2025 ──", hdr, "-" * len(hdr)])
        best_lbl, best_cagr = "", float("-inf")
        for label, _ in batch:
            rows = by_label[label]
            by_y = {r["start"][:4]: r for r in rows if not r.get("error")}
            cagr = _cagr(rows)
            if cagr == cagr and cagr > best_cagr:
                best_cagr, best_lbl = cagr, label
            cols = []
            for y in years:
                tr = by_y.get(y, {}).get("total_return")
                cols.append(f"{float(tr):+7.2%}" if tr is not None else "     n/a")
            worst = min(
                (float(r["max_drawdown"]) for r in rows if r.get("max_drawdown") is not None),
                default=float("nan"),
            )
            report_parts.append(
                f"{label:<8} {cagr:>+9.2%}" + "".join(cols) + f"  MDD≥{worst:+.1%}"
            )
        report_parts.extend(
            [
                "",
                f"  三年最高: {best_lbl} ({best_cagr:+.2%})",
                "  晋级条件: 三年CAGR > prod 且 2024/2025 不双杀；再跑七年全历史。",
                f"  用时 {(time.time()-t0)/60:.1f} 分钟",
            ]
        )
        (out_dir / "malpha_results.json").write_text(
            json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        report_parts.append("（已 --skip-backtest）")

    await close_zstock_database()

    text = "\n".join(report_parts)
    (out_dir / "malpha_report.txt").write_text(text, encoding="utf-8")
    logger.info("\n%s", text)
    logger.info("报告: %s", out_dir / "malpha_report.txt")
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
