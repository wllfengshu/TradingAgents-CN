# -*- coding: utf-8 -*-
"""
2025 专项：v1.17 vs baseline 漏斗 + overlay/因子 ablation。

    python -m zstock.strategy_management.script.diagnose_2025_ablation

输出：output/diagnose_2025_YYYYMMDD/
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

CONFIG_DIR = PROJECT_ROOT / "zstock" / "common" / "config"
PARAMS_V117 = CONFIG_DIR / "strategy_params.json"
PARAMS_BASELINE = CONFIG_DIR / "strategy_params_baseline_archive.json"

YEAR = "2025"
START = "2025-01-01"
END = "2025-12-31"
CAPITAL = 1_000_000.0
FEE = 0.0015
REBALANCE = 5
HS300_2025 = 0.21190086365415106

OUTPUT_DIR = (
    Path(__file__).resolve().parent
    / "output"
    / f"diagnose_2025_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
)


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


def _avg_exposure(holdings_log: List[Dict[str, Any]]) -> float:
    ws: List[float] = []
    for snap in holdings_log or []:
        hs = snap.get("holdings") or []
        ws.append(sum(float(h.get("weight", 0)) for h in hs) if hs else 0.0)
    return float(sum(ws) / len(ws)) if ws else 0.0


def _overlay_off_patch(params: Dict[str, Any]) -> Dict[str, Any]:
    p = copy.deepcopy(params)
    p["weak_regime_protection"] = {"enabled": False}
    p["factor_decay"] = {**(p.get("factor_decay") or {}), "enabled": False}
    mo = dict(p.get("market_overlay") or {})
    mo["position_scale_green"] = 1.0
    mo["position_scale_yellow"] = 1.0
    mo["position_scale_red"] = 0.0
    p["market_overlay"] = mo
    fs = dict(p.get("final_score") or {})
    fs["top_k"] = 5
    fs["by_regime"] = {}
    p["final_score"] = fs
    return p


def _variants() -> List[Tuple[str, Path, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]]:
    """(label, params_path, factor_override, params_patch_fn_name via None + merged params)"""
    base = _load_params(PARAMS_V117)
    bl = _load_params(PARAMS_BASELINE)
    v117_no_ov = _overlay_off_patch(base)
    bl_factors = {"active_factors": copy.deepcopy(bl.get("active_factors"))}
    v117_bl_alpha = copy.deepcopy(base)
    v117_bl_alpha["active_factors"] = copy.deepcopy(bl.get("active_factors"))
    return [
        ("v117_full", PARAMS_V117, None, None),
        ("baseline_full", PARAMS_BASELINE, None, None),
        ("v117_no_overlay", PARAMS_V117, None, v117_no_ov),
        ("v117_baseline_factors", PARAMS_V117, bl_factors, v117_bl_alpha),
    ]


async def _funnel_for_pipe(pipe, dates: List[str], tag: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
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
                    "stop": f"error:{type(e).__name__}: {e}",
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
                "allow_new": bool(df.attrs.get("m1_allow_new_open", True)),
            }
        )
        if i % 40 == 0:
            logger.info("[%s] 漏斗 %d/%d", tag, i, len(dates))
    return rows


def _summarize_funnel_tag(rows: List[Dict[str, Any]], tag: str) -> List[str]:
    ys = [r for r in rows if r.get("tag") == tag]
    if not ys:
        return [f"=== {tag} 无数据 ===", ""]
    n = len(ys)
    stops = Counter(r["stop"] for r in ys)
    grades = Counter(r["grade"] for r in ys)
    non_red = [r for r in ys if r["grade"] != "red" and r["stop"] not in ("red", "error")]
    m3s = [r["n_m3"] for r in non_red]
    m4s = [r["n_m4"] for r in non_red]
    buys = [r["n_buy"] for r in non_red]
    pass_pct = (sum(m4s) / sum(m3s) * 100.0) if m3s and sum(m3s) > 0 else 0.0
    thin = sum(1 for r in non_red if 0 < r["n_buy"] < 3)
    lines = [
        f"=== {tag}  n={n} ===",
        f"  灯号: green={grades.get('green', 0)} yellow={grades.get('yellow', 0)} "
        f"red={grades.get('red', 0)}",
        f"  终止: ok={stops.get('ok', 0)} red={stops.get('red', 0)} "
        f"m4_empty={stops.get('m4_empty', 0)} no_m3={stops.get('no_m3', 0)}",
    ]
    if non_red:
        lines.append(
            f"  非红灯: M3均={sum(m3s)/len(m3s):.1f} M4均={sum(m4s)/len(m4s):.1f} "
            f"buy均={sum(buys)/len(buys):.1f} M4通过率={pass_pct:.1f}%"
        )
        lines.append(f"  buy<3: {thin}/{len(non_red)}")
    lines.append("")
    return lines


async def _run_backtest(
    provider,
    label: str,
    params_path: Path,
    params_merged: Dict[str, Any],
    factor_override: Optional[Dict[str, Any]],
    out_dir: Path,
) -> Dict[str, Any]:
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
    from zstock.strategy_management.script.backtester import Backtester

    _clear_caches()
    seg = out_dir / "segments" / label
    seg.mkdir(parents=True, exist_ok=True)
    fp = CrossSectionStrategyPipeline(
        config_path=str(params_path),
        config_override=factor_override,
    )
    bt = Backtester(fee_rate=FEE, initial_capital=CAPITAL, factor_pipeline=fp)
    result = await bt.run(
        start_date=START,
        end_date=END,
        ohlcv_provider=provider,
        rebalance_freq=REBALANCE,
        strategy_config=params_merged,
        use_precomputed_factors=True,
        verbose=False,
    )
    result.plot(output_path=str(seg / "equity.png"), title=f"{label} {START}~{END}")
    (seg / "summary.txt").write_text(result.summary(), encoding="utf-8")
    m = result.metrics or {}
    row = {
        "label": label,
        "total_return": float(m.get("total_return", 0)),
        "total_return_gross": float(m.get("total_return_gross", m.get("total_return", 0))),
        "sharpe": float(m.get("sharpe", 0)),
        "max_drawdown": float(m.get("max_drawdown", 0)),
        "avg_exposure": _avg_exposure(result.holdings_log),
        "total_cost": float(m.get("total_cost", 0)),
        "rebalance_count": int(m.get("rebalance_count", 0)),
        "excess_vs_hs300": float(m.get("total_return", 0)) - HS300_2025,
    }
    logger.info(
        "  BT %s ret=%+.2f%% exp=%.1f%% excess=%+.2f%% sharpe=%.2f",
        label,
        row["total_return"] * 100,
        row["avg_exposure"] * 100,
        row["excess_vs_hs300"] * 100,
        row["sharpe"],
    )
    return row


def _build_report(
    funnel_lines: List[str],
    bt_rows: List[Dict[str, Any]],
) -> str:
    lines = [
        "2025 专项诊断：漏斗 + ablation",
        f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"区间: {START} ~ {END}  |  沪深300 ≈ +21.19%",
        "",
        "── 信号漏斗（预计算 score_signals）──",
        "",
        *funnel_lines,
        "── 回测 ablation（T+1，fee 0.15%，reb=5）──",
        "",
        f"{'配置':<22} {'收益':>8} {'超额':>8} {'均仓位':>8} {'MDD':>8} {'Sharpe':>7}",
        "-" * 65,
    ]
    for r in bt_rows:
        lines.append(
            f"{r['label']:<22} {r['total_return']*100:+7.2f}% "
            f"{r['excess_vs_hs300']*100:+7.2f}% {r['avg_exposure']*100:7.1f}% "
            f"{r['max_drawdown']*100:+7.2f}% {r['sharpe']:7.2f}"
        )
    lines.extend(
        [
            "",
            "解读指引:",
            "  v117_no_overlay 若接近/超过 baseline_full → 2025 主要矛盾在策略层缩仓/弱段/decay。",
            "  v117_baseline_factors 若接近 baseline_full → 矛盾在 active_factors 而非 overlay。",
            "  漏斗 buy 均 v117 << baseline → 检查 M4 门槛与 signal_type/红灯逻辑。",
        ]
    )
    return "\n".join(lines)


async def async_main() -> int:
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.data_management.query_service import COL_FACTOR_MARKET, get_data_query_service
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
    from zstock.factor_management.script.网格搜索.grid_search_real import load_ohlcv
    from zstock.strategy_management.script.backtester import make_ohlcv_provider_from_dict

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for name in ("zstock", "zstock.factor_management", "zstock.strategy_management", "app"):
        logging.getLogger(name).setLevel(logging.WARNING)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = OUTPUT_DIR / "run.log"
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    logging.getLogger().addHandler(fh)

    await init_zstock_database()
    try:
        qs = get_data_query_service()
        db = qs.database_service.db
        dates = sorted(
            str(d)
            for d in await db[COL_FACTOR_MARKET].distinct(
                "trade_date",
                {"trade_date": {"$gte": START, "$lte": END}},
            )
            if d
        )
        logger.info("2025 因子日 %d 个", len(dates))

        funnel_all: List[Dict[str, Any]] = []
        for tag, path, _, patch in [
            ("v117_full", PARAMS_V117, None, None),
            ("baseline_full", PARAMS_BASELINE, None, None),
        ]:
            _clear_caches()
            pipe = CrossSectionStrategyPipeline(config_path=str(path))
            await pipe.preload_precomputed_factors(START, END)
            logger.info("漏斗 %s ...", tag)
            funnel_all.extend(await _funnel_for_pipe(pipe, dates, tag))

        funnel_csv = OUTPUT_DIR / "funnel_2025.csv"
        funnel_csv.write_text(
            "tag,trade_date,grade,regime,n_m3,n_m4,n_buy,stop,allow_new\n"
            + "\n".join(
                f"{r['tag']},{r['trade_date']},{r['grade']},{r['regime']},"
                f"{r['n_m3']},{r['n_m4']},{r['n_buy']},{r['stop']},{r.get('allow_new', '')}"
                for r in funnel_all
            ),
            encoding="utf-8",
        )
        funnel_lines: List[str] = []
        for tag in ("v117_full", "baseline_full"):
            funnel_lines.extend(_summarize_funnel_tag(funnel_all, tag))

        logger.info("加载 OHLCV %s → %s", START, END)
        ohlcv = await load_ohlcv(START, END)
        if not ohlcv:
            logger.error("无 OHLCV")
            return 1
        provider = make_ohlcv_provider_from_dict(ohlcv)
        logger.info("OHLCV %d 只", len(ohlcv))

        bt_rows: List[Dict[str, Any]] = []
        for label, path, factor_ov, params_patch in _variants():
            params_merged = params_patch if params_patch is not None else _load_params(path)
            logger.info("回测 %s", label)
            bt_rows.append(
                await _run_backtest(
                    provider, label, path, params_merged, factor_ov, OUTPUT_DIR
                )
            )

        report = _build_report(funnel_lines, bt_rows)
        (OUTPUT_DIR / "diagnose_2025_report.txt").write_text(report, encoding="utf-8")
        with open(OUTPUT_DIR / "diagnose_2025_results.json", "w", encoding="utf-8") as f:
            json.dump({"funnel": funnel_all, "backtests": bt_rows}, f, ensure_ascii=False, indent=2)
        print(report)
        logger.info("报告: %s", OUTPUT_DIR / "diagnose_2025_report.txt")
        return 0
    finally:
        await close_zstock_database()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
