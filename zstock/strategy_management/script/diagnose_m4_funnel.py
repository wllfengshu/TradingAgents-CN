# -*- coding: utf-8 -*-
"""M4 漏斗诊断 + 关闭合力门槛的对照回测。

    python -m zstock.strategy_management.script.diagnose_m4_funnel
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

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
    / f"diagnose_m4_{datetime.now().strftime('%Y%m%d')}"
)
HS300 = {"2024": 0.1620, "2025": 0.2119, "2026": -0.0282}
NAKED = {"2024": -0.0467, "2025": 0.0592, "2026": 0.0548}
CURRENT = {"2024": 0.0278, "2025": 0.0223, "2026": 0.0287}
PERIODS = [
    ("2024", "2024-01-01", "2024-12-31"),
    ("2025", "2025-01-02", "2025-12-31"),
    ("2026", "2026-01-01", "2026-09-08"),
]


def _year_of(td: str) -> str:
    y = td[:4]
    return y if y in ("2024", "2025", "2026") else "other"


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


def _no_gate_factor_override() -> Dict[str, Any]:
    return {
        "market_overlay": {
            "position_scale_green": 1.0,
            "position_scale_yellow": 1.0,
        },
        "final_score": {"top_k": 5, "by_regime": {"reversal": {"top_k": 5}}},
        "weak_regime_protection": {"enabled": False},
        "cooperative_force": {"skip_filter": True, "threshold_pct": 0.01},
    }


def _naked_runtime() -> Dict[str, Any]:
    from zstock.common.config.strategy_config import build_runtime_config

    cfg = build_runtime_config()
    cfg["weak_regime_protection"] = {"enabled": False}
    cfg["adaptive_rebalance"] = {"enabled": False}
    cfg["final_score"] = {"top_k": 5, "by_regime": {}}
    cfg["portfolio_optimization"]["max_holdings"] = 5
    cfg["portfolio_optimization"]["min_holdings"] = 3
    return cfg


async def _funnel_census(pipe, dates: List[str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for i, td in enumerate(dates, start=1):
        try:
            df = await pipe.score_signals(td)
        except Exception as e:
            rows.append(
                {
                    "trade_date": td,
                    "year": _year_of(td),
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
                "trade_date": td,
                "year": _year_of(td),
                "grade": str(df.attrs.get("market_grade", "?")),
                "regime": str(df.attrs.get("regime", "?")),
                "n_m3": int(df.attrs.get("funnel_n_m3", 0) or 0),
                "n_m4": int(df.attrs.get("funnel_n_m4", 0) or 0),
                "n_buy": int(df.attrs.get("funnel_n_buy", 0) or 0),
                "stop": str(df.attrs.get("funnel_stop", "?")),
            }
        )
        if i % 50 == 0:
            logger.info("漏斗进度 %d/%d", i, len(dates))
    return rows


def _summarize_funnel(rows: List[Dict[str, Any]]) -> str:
    lines = ["M4 信号漏斗（当前门槛 fcoop1>=1%）", ""]
    for year in ("2024", "2025", "2026"):
        ys = [r for r in rows if r["year"] == year]
        if not ys:
            continue
        n = len(ys)
        stops = Counter(r["stop"] for r in ys)
        grades = Counter(r["grade"] for r in ys)
        non_red = [r for r in ys if r["stop"] != "red" and r["grade"] != "red"]
        m3s = [r["n_m3"] for r in non_red]
        m4s = [r["n_m4"] for r in non_red]
        buys = [r["n_buy"] for r in non_red]
        m4_empty = stops.get("m4_empty", 0)
        thin = sum(1 for r in non_red if 0 < r["n_buy"] < 3)
        ge5 = sum(1 for r in non_red if r["n_buy"] >= 5)
        pass_pct = (
            (sum(m4s) / sum(m3s) * 100.0) if m3s and sum(m3s) > 0 else 0.0
        )
        lines.append(f"=== {year}  n={n} ===")
        lines.append(
            f"  灯号: green={grades.get('green', 0)} yellow={grades.get('yellow', 0)} "
            f"red={grades.get('red', 0)}"
        )
        lines.append(
            f"  终止: ok={stops.get('ok', 0)} red={stops.get('red', 0)} "
            f"m4_empty={m4_empty} no_m3={stops.get('no_m3', 0)} err={stops.get('error', 0)}"
        )
        if non_red:
            lines.append(
                f"  非红灯日: M3均={sum(m3s)/len(m3s):.1f}  M4均={sum(m4s)/len(m4s):.1f}  "
                f"buy均={sum(buys)/len(buys):.1f}  M4通过率={pass_pct:.1f}%"
            )
            lines.append(
                f"  buy<3 天数={thin}/{len(non_red)}  buy>=5 天数={ge5}/{len(non_red)}"
            )
        lines.append("")
    return "\n".join(lines)


async def _run_no_gate_year(provider, start: str, end: str, output_dir: Path, label: str) -> Dict[str, Any]:
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
    from zstock.strategy_management.script.backtester import Backtester

    _clear_caches()
    seg_dir = output_dir / "segments" / label
    seg_dir.mkdir(parents=True, exist_ok=True)
    pipe = CrossSectionStrategyPipeline(config_override=_no_gate_factor_override())
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
    result.plot(output_path=str(seg_dir / "equity.png"), title=f"noM4 {label} {start}~{end}")
    (seg_dir / "summary.txt").write_text(result.summary(), encoding="utf-8")
    m = result.metrics
    row = {
        "status": "success",
        "label": label,
        "total_return": float(m.get("total_return", 0)),
        "sharpe": float(m.get("sharpe", 0)),
        "max_drawdown": float(m.get("max_drawdown", 0)),
        "avg_exposure": _avg_exposure(result.holdings_log),
        "total_cost": float(m.get("total_cost", 0)),
    }
    logger.info(
        "  noM4 %s ret=%.2f%% sharpe=%.3f exp=%.1f%%",
        label,
        row["total_return"] * 100,
        row["sharpe"],
        row["avg_exposure"] * 100,
    )
    return row


def _final_report(funnel_txt: str, nogate: Dict[str, Dict[str, Any]]) -> str:
    lines = [
        "M4 诊断 + 关闭合力门槛对照",
        f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        funnel_txt,
        "回测对照（T+1 开盘，fee 0.15%）",
        f"{'年份':<8} {'当前':>10} {'裸回测':>10} {'关M4门槛':>10} {'沪深300':>10}",
        "-" * 52,
    ]
    for year, _, _ in PERIODS:
        ng = float((nogate.get(year) or {}).get("total_return", 0))
        lines.append(
            f"{year:<8} {CURRENT[year]*100:+9.2f}% {NAKED[year]*100:+9.2f}% "
            f"{ng*100:+9.2f}% {HS300[year]*100:+9.2f}%"
        )
    lines.append("")
    lines.append("关M4 仓位:")
    for year, _, _ in PERIODS:
        exp = float((nogate.get(year) or {}).get("avg_exposure", 0))
        lines.append(f"  {year}: {exp*100:.1f}%")
    lines.append("")
    lines.append("因子侧（2026-08-23 IC 测评，cond_p5）:")
    lines.append("  2024/2025 有效且方向为负（淡化热门）: f33 连板、超额收益、相对强度")
    lines.append("  2026 仅 B 级: force.dragon_consistency_5d（IC 为正，与负向龙头逻辑相反）")
    lines.append("  无因子三年同时 B+")
    return "\n".join(lines)


async def async_main() -> int:
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
    from zstock.factor_management.script.网格搜索.grid_search_real import load_ohlcv
    from zstock.strategy_management.script.backtester import make_ohlcv_provider_from_dict

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for name in ("zstock", "zstock.factor_management", "zstock.strategy_management", "app"):
        logging.getLogger(name).setLevel(logging.WARNING)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    await init_zstock_database()
    try:
        from zstock.data_management.query_service import COL_FACTOR_MARKET, get_data_query_service

        qs = get_data_query_service()
        db = qs.database_service.db
        dates = sorted(
            await db[COL_FACTOR_MARKET].distinct(
                "trade_date",
                {"trade_date": {"$gte": "2024-01-01", "$lte": "2026-09-08"}},
            )
        )
        logger.info("因子交易日 %d 个，开始漏斗", len(dates))

        pipe = CrossSectionStrategyPipeline()
        await pipe.preload_precomputed_factors("2024-01-01", "2026-09-08")
        funnel_rows = await _funnel_census(pipe, dates)
        funnel_txt = _summarize_funnel(funnel_rows)
        (OUTPUT_DIR / "funnel.csv").write_text(
            "trade_date,year,grade,regime,n_m3,n_m4,n_buy,stop\n"
            + "\n".join(
                f"{r['trade_date']},{r['year']},{r['grade']},{r['regime']},"
                f"{r['n_m3']},{r['n_m4']},{r['n_buy']},{r['stop']}"
                for r in funnel_rows
            ),
            encoding="utf-8",
        )
        print(funnel_txt)

        logger.info("加载 OHLCV")
        ohlcv = await load_ohlcv("2024-01-01", "2026-09-08")
        provider = make_ohlcv_provider_from_dict(ohlcv)
        nogate: Dict[str, Dict[str, Any]] = {}
        for year, start, end in PERIODS:
            logger.info("关M4门槛回测 %s", year)
            nogate[year] = await _run_no_gate_year(
                provider, start, end, OUTPUT_DIR, f"nogate_{year}"
            )

        report = _final_report(funnel_txt, nogate)
        (OUTPUT_DIR / "diagnose_m4_report.txt").write_text(report, encoding="utf-8")
        with open(OUTPUT_DIR / "nogate_results.json", "w", encoding="utf-8") as f:
            json.dump(nogate, f, ensure_ascii=False, indent=2)
        print(report)
        return 0
    finally:
        await close_zstock_database()


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
