# -*- coding: utf-8 -*-
"""v2 选股边探索：IC 极性对齐 / 去 M4 / 反向门槛 / 单因子。无边则自动试下一组。

    python -m zstock.strategy_management.script.explore_v2_edge

不改 strategy_params.json。全部 T+1 开盘、fee 0.15%、黄灯满仓 top_k=5（隔离选股）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

CAPITAL = 1_000_000.0
FEE = 0.0015
REBALANCE = 5
OUTPUT_DIR = (
    Path(__file__).resolve().parent
    / "output"
    / f"explore_v2_{datetime.now().strftime('%Y%m%d')}"
)
HS300 = {"2024": 0.1620, "2025": 0.2119, "2026": -0.0282}
CURRENT = {
    "2024": {"ret": 0.0278, "exp": 0.238},
    "2025": {"ret": 0.0223, "exp": 0.284},
    "2026": {"ret": 0.0287, "exp": 0.224},
}
NOM4 = {
    "2024": {"ret": -0.3085, "exp": 0.708},
    "2025": {"ret": 0.2039, "exp": 0.661},
    "2026": {"ret": -0.1149, "exp": 0.667},
}
PERIODS = [
    ("2024", "2024-01-01", "2024-12-31"),
    ("2025", "2025-01-02", "2025-12-31"),
    ("2026", "2026-01-01", "2026-09-08"),
]


def _f(field: str, weight: float, polarity: str) -> Dict[str, Any]:
    return {"field": field, "weight": weight, "polarity": polarity}


def _both(entries: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    return {"momentum": entries, "reversal": entries}


def _naked_runtime() -> Dict[str, Any]:
    from zstock.common.config.strategy_config import build_runtime_config

    cfg = build_runtime_config()
    cfg["weak_regime_protection"] = {"enabled": False}
    cfg["adaptive_rebalance"] = {"enabled": False}
    cfg["final_score"] = {"top_k": 5, "by_regime": {}}
    cfg["portfolio_optimization"]["max_holdings"] = 5
    cfg["portfolio_optimization"]["min_holdings"] = 3
    return cfg


def _naked_overlay() -> Dict[str, Any]:
    return {
        "market_overlay": {
            "position_scale_green": 1.0,
            "position_scale_yellow": 1.0,
        },
        "final_score": {"top_k": 5, "by_regime": {"reversal": {"top_k": 5}}},
        "weak_regime_protection": {"enabled": False},
        "factor_decay": {"enabled": False},
    }


def _ic_align_factors() -> Dict[str, Any]:
    sector = [
        _f("f28_consistency", 0.35, "negative"),
        _f("f21_rps_20d", 0.30, "negative"),
        _f("f30_sector_concentration", 0.35, "negative"),
    ]
    dragon = [
        _f("f33_consecutive_boards", 0.20, "negative"),
        _f("f34_resonance_pct_5d", 0.15, "positive"),
        _f("f36_identity_premium", 0.15, "positive"),
        _f("f31_excess_return", 0.15, "negative"),
        _f("f37_relative_strength", 0.15, "negative"),
        _f("f38_turnover_anomaly", 0.10, "negative"),
        _f("f39_pb", 0.10, "negative"),
    ]
    force_m = [
        _f("dragon_consistency_5d", 0.35, "positive"),
        _f("fcoop1_main_net_ratio", 0.25, "negative"),
        _f("f_mean_reversion_signal", 0.25, "negative"),
        _f("fcoop7_super_large_net_ratio", 0.15, "negative"),
    ]
    force_r = [
        _f("fcoop4_turnover_quality", 0.40, "negative"),
        _f("f_mean_reversion_signal", 0.40, "negative"),
        _f("fcoop1_main_net_ratio", 0.20, "negative"),
    ]
    return {
        "sector": _both(sector),
        "dragon": _both(dragon),
        "force": {"momentum": force_m, "reversal": force_r},
    }


def _fade_core_factors() -> Dict[str, Any]:
    sector = [
        _f("f21_rps_20d", 0.5, "negative"),
        _f("f23_limit_up_density", 0.5, "negative"),
    ]
    dragon = [
        _f("f33_consecutive_boards", 0.40, "negative"),
        _f("f31_excess_return", 0.30, "negative"),
        _f("f37_relative_strength", 0.30, "negative"),
    ]
    force = [
        _f("f_mean_reversion_signal", 0.60, "negative"),
        _f("fcoop1_main_net_ratio", 0.40, "negative"),
    ]
    return {
        "sector": _both(sector),
        "dragon": _both(dragon),
        "force": _both(force),
    }


def _f33_only_factors() -> Dict[str, Any]:
    return {
        "sector": _both([_f("f23_limit_up_density", 1.0, "negative")]),
        "dragon": _both([_f("f33_consecutive_boards", 1.0, "negative")]),
        "force": _both([_f("f_mean_reversion_signal", 1.0, "negative")]),
    }


def _consistency_factors() -> Dict[str, Any]:
    return {
        "sector": _both([_f("f30_sector_concentration", 1.0, "positive")]),
        "dragon": _both([_f("f36_identity_premium", 1.0, "positive")]),
        "force": _both([_f("dragon_consistency_5d", 1.0, "positive")]),
    }


def _flip_only_factors() -> Dict[str, Any]:
    from zstock.common.config.strategy_config import load_strategy_params

    af = json.loads(json.dumps(load_strategy_params()["active_factors"]))
    for regime in ("momentum", "reversal"):
        for fc in af["dragon"][regime]:
            if fc["field"] in ("f36_identity_premium", "f34_resonance_pct_5d"):
                fc["polarity"] = "positive"
    return af


def _variants() -> List[Dict[str, Any]]:
    overlay = _naked_overlay()
    align = _ic_align_factors()
    return [
        {
            "id": "ic_align_m4off",
            "title": "v2: IC极性对齐 + 关M4",
            "factor": {
                **overlay,
                "active_factors": align,
                "cooperative_force": {"skip_filter": True, "gate_mode": "off"},
            },
        },
        {
            "id": "ic_align_m4on",
            "title": "IC极性对齐 + 保留M4流入门槛",
            "factor": {
                **overlay,
                "active_factors": align,
                "cooperative_force": {"skip_filter": False, "gate_mode": "inflow", "threshold_pct": 0.01},
            },
        },
        {
            "id": "coop0_m4off",
            "title": "当前龙头/板块 + 合力权重0 + 关M4",
            "factor": {
                **overlay,
                "cooperative_force": {"skip_filter": True, "gate_mode": "off"},
                "final_score": {
                    "top_k": 5,
                    "by_regime": {"reversal": {"top_k": 5}},
                    "weights": {"sector": 0.45, "dragon": 0.55, "cooperative": 0.0},
                },
            },
        },
        {
            "id": "flip_only_m4on",
            "title": "只把 f36/f34 改正向，M4仍开",
            "factor": {
                **overlay,
                "active_factors": _flip_only_factors(),
                "cooperative_force": {"skip_filter": False, "gate_mode": "inflow", "threshold_pct": 0.01},
            },
        },
        {
            "id": "fade_core_m4off",
            "title": "浓缩淡化(f33/f31/f37) + 关M4 + 合力负向",
            "factor": {
                **overlay,
                "active_factors": _fade_core_factors(),
                "cooperative_force": {"skip_filter": True, "gate_mode": "off"},
            },
        },
        {
            "id": "m4_outflow",
            "title": "M4改为净流出门槛，其它当前极性",
            "factor": {
                **overlay,
                "cooperative_force": {"skip_filter": False, "gate_mode": "outflow"},
            },
        },
        {
            "id": "f33_only_m4off",
            "title": "单因子淡化连板 + 关M4",
            "factor": {
                **overlay,
                "active_factors": _f33_only_factors(),
                "cooperative_force": {"skip_filter": True, "gate_mode": "off"},
                "final_score": {
                    "top_k": 5,
                    "by_regime": {"reversal": {"top_k": 5}},
                    "weights": {"sector": 0.2, "dragon": 0.8, "cooperative": 0.0},
                },
            },
        },
        {
            "id": "consistency_m4off",
            "title": "2026方向：身份溢价+龙头一致性 + 关M4",
            "factor": {
                **overlay,
                "active_factors": _consistency_factors(),
                "cooperative_force": {"skip_filter": True, "gate_mode": "off"},
            },
        },
    ]


def _clear_caches() -> None:
    from zstock.common.config import strategy_config
    from zstock.strategy_management.pipeline import StrategyPipeline
    from zstock.strategy_management.script import backtester as bt_mod

    StrategyPipeline._config_cache = None
    strategy_config._clear_cache()
    bt_mod._CONFIG_CACHE = None


def _avg_exposure(holdings_log: List[Dict[str, Any]]) -> float:
    ws: List[float] = []
    for snap in holdings_log or []:
        hs = snap.get("holdings") or []
        ws.append(sum(float(h.get("weight", 0)) for h in hs) if hs else 0.0)
    return float(sum(ws) / len(ws)) if ws else 0.0


def _matched_hs(year: str, exp: float) -> float:
    return float(exp) * HS300[year]


def _eval_variant(years: Dict[str, Dict[str, Any]]) -> Tuple[str, str]:
    """返回 (等级, 说明)。strong / tradable / better / fail。"""
    r = {y: float(years[y]["total_return"]) for y in ("2024", "2025", "2026")}
    e = {y: float(years[y]["avg_exposure"]) for y in ("2024", "2025", "2026")}
    mdd24 = float(years["2024"]["max_drawdown"])
    beats_full = sum(1 for y in ("2024", "2025") if r[y] >= HS300[y])
    beats_matched = sum(
        1 for y in ("2024", "2025", "2026") if r[y] >= _matched_hs(y, e[y]) + 0.005
    )
    vs_nom4 = sum(
        1
        for y in ("2024", "2026")
        if r[y] > NOM4[y]["ret"] + 0.05
    )
    vs_cur = sum(1 for y in ("2024", "2025", "2026") if r[y] > CURRENT[y]["ret"] + 0.01)
    if beats_full == 2 and r["2026"] >= HS300["2026"] - 0.02 and mdd24 > -0.20:
        return "strong", "2024+2025 均超过满仓沪深300，且 2026 不显著差于指数"
    if (
        r["2024"] > -0.08
        and beats_matched >= 2
        and r["2025"] > 0.08
        and mdd24 > -0.18
    ):
        return "tradable", "2024 未爆、至少两年赢同仓位指数、2025 有绝对收益"
    if vs_nom4 == 2 and r["2024"] > -0.18 and vs_cur >= 1:
        return "better", "相对关M4基线明显改善，可作下一轮起点"
    if vs_cur >= 2 and r["2024"] > -0.12:
        return "better", "两年以上好于当前配置且 2024 未崩"
    return "fail", (
        f"满仓赢{beats_full}/2 同仓位赢{beats_matched}/3 "
        f"vs关M4改善{vs_nom4}/2 vs当前{vs_cur}/3"
    )


async def _run_year(pipe, provider, start: str, end: str, seg_dir: Path, label: str) -> Dict[str, Any]:
    from zstock.strategy_management.script.backtester import Backtester

    _clear_caches()
    seg_dir.mkdir(parents=True, exist_ok=True)
    bt = Backtester(fee_rate=FEE, initial_capital=CAPITAL, factor_pipeline=pipe)
    result = await bt.run(
        start_date=start,
        end_date=end,
        ohlcv_provider=provider,
        rebalance_freq=REBALANCE,
        strategy_config=_naked_runtime(),
        use_precomputed_factors=True,
        verbose=False,
    )
    (seg_dir / "summary.txt").write_text(result.summary(), encoding="utf-8")
    m = result.metrics
    return {
        "status": "success",
        "label": label,
        "total_return": float(m.get("total_return", 0)),
        "sharpe": float(m.get("sharpe", 0)),
        "max_drawdown": float(m.get("max_drawdown", 0)),
        "avg_exposure": _avg_exposure(result.holdings_log),
        "total_cost": float(m.get("total_cost", 0)),
    }


def _fmt(x: float) -> str:
    return f"{x * 100:+.2f}%"


def _write_report(all_rows: Dict[str, Any], verdicts: Dict[str, Tuple[str, str]]) -> str:
    lines = [
        "v2 选股边探索",
        f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "约束: T+1 开盘, fee 0.15%, 黄灯满仓, top_k=5, 不改 strategy_params.json",
        "",
        f"{'方案':<22} {'2024':>9} {'2025':>9} {'2026':>9} {'仓24':>6} {'评级':<10} 说明",
        "-" * 100,
        f"{'当前v1.16':<22} {_fmt(CURRENT['2024']['ret']):>9} {_fmt(CURRENT['2025']['ret']):>9} "
        f"{_fmt(CURRENT['2026']['ret']):>9} {CURRENT['2024']['exp']*100:5.0f}% {'base':<10} 生产配置",
        f"{'关M4基线':<22} {_fmt(NOM4['2024']['ret']):>9} {_fmt(NOM4['2025']['ret']):>9} "
        f"{_fmt(NOM4['2026']['ret']):>9} {NOM4['2024']['exp']*100:5.0f}% {'base':<10} 极性未改",
        f"{'沪深300':<22} {_fmt(HS300['2024']):>9} {_fmt(HS300['2025']):>9} "
        f"{_fmt(HS300['2026']):>9} {'100':>5}% {'bench':<10}",
    ]
    for vid, years in all_rows.items():
        grade, why = verdicts.get(vid, ("?", ""))
        r24 = float(years["2024"]["total_return"])
        r25 = float(years["2025"]["total_return"])
        r26 = float(years["2026"]["total_return"])
        e24 = float(years["2024"]["avg_exposure"])
        lines.append(
            f"{vid:<22} {_fmt(r24):>9} {_fmt(r25):>9} {_fmt(r26):>9} "
            f"{e24*100:5.0f}% {grade:<10} {why}"
        )
    lines.append("")
    strong = [k for k, (g, _) in verdicts.items() if g == "strong"]
    tradable = [k for k, (g, _) in verdicts.items() if g == "tradable"]
    better = [k for k, (g, _) in verdicts.items() if g == "better"]
    if strong:
        lines.append(f"结论: 找到强边 {strong}")
    elif tradable:
        lines.append(f"结论: 找到可交易边 {tradable}")
    elif better:
        lines.append(f"结论: 未达可交易边，但 {better} 好于基线，作为下一轮起点")
    else:
        lines.append("结论: 本组假设均未证明稳定选股边，需要继续换因子/换宇宙/换持有期")
    return "\n".join(lines)


async def async_main() -> int:
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
    from zstock.factor_management.script.网格搜索.grid_search_real import load_ohlcv
    from zstock.strategy_management.script.backtester import make_ohlcv_provider_from_dict

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for name in ("zstock", "zstock.factor_management", "zstock.strategy_management", "app"):
        logging.getLogger(name).setLevel(logging.ERROR)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    variants = _variants()
    all_rows: Dict[str, Any] = {}
    verdicts: Dict[str, Tuple[str, str]] = {}

    await init_zstock_database()
    try:
        print("加载 OHLCV", flush=True)
        ohlcv = await load_ohlcv("2024-01-01", "2026-09-08")
        provider = make_ohlcv_provider_from_dict(ohlcv)
        found: Optional[str] = None

        for spec in variants:
            vid = spec["id"]
            print(f"\n=== {vid} · {spec['title']} ===", flush=True)
            _clear_caches()
            pipe = CrossSectionStrategyPipeline(config_override=spec["factor"])
            years: Dict[str, Dict[str, Any]] = {}
            for year, start, end in PERIODS:
                row = await _run_year(
                    pipe, provider, start, end, OUTPUT_DIR / "segments" / f"{vid}_{year}", f"{vid}_{year}"
                )
                years[year] = row
                print(
                    f"  {year} ret={row['total_return']*100:+.2f}% sharpe={row['sharpe']:.2f} "
                    f"mdd={row['max_drawdown']*100:.1f}% exp={row['avg_exposure']*100:.1f}%",
                    flush=True,
                )
            grade, why = _eval_variant(years)
            all_rows[vid] = years
            verdicts[vid] = (grade, why)
            print(f"  评级 {grade}: {why}", flush=True)
            with open(OUTPUT_DIR / "explore_results.json", "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "results": all_rows,
                        "verdicts": {k: {"grade": v[0], "why": v[1]} for k, v in verdicts.items()},
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            report = _write_report(all_rows, verdicts)
            (OUTPUT_DIR / "explore_report.txt").write_text(report, encoding="utf-8")
            if grade in ("strong",):
                found = vid
                print(f"已证明强边，停止: {vid}", flush=True)
                break

        report = _write_report(all_rows, verdicts)
        (OUTPUT_DIR / "explore_report.txt").write_text(report, encoding="utf-8")
        print("\n" + report, flush=True)
        return 0 if found else 0
    finally:
        await close_zstock_database()


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
