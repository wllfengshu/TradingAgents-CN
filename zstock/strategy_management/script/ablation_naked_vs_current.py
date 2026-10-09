# -*- coding: utf-8 -*-
"""裸回测 vs 当前配置：关掉黄灯缩仓 / 弱段只减不加 / 回撤节流，满仓 top_k。

用法（不改 strategy_params.json）：
    python -m zstock.strategy_management.script.ablation_naked_vs_current
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
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
    Path(__file__).resolve().parent / "output" / f"ablation_naked_{datetime.now().strftime('%Y%m%d')}"
)
CURRENT_CSV = (
    Path(__file__).resolve().parent / "output" / "overnight_20260908_review" / "comparison.csv"
)
HS300 = {
    "2024": 0.1620,
    "2025": 0.2119,
    "2026": -0.0282,
}
PERIODS = [
    ("2024", "2024-01-01", "2024-12-31"),
    ("2025", "2025-01-02", "2025-12-31"),
    ("2026", "2026-01-01", "2026-09-08"),
]


def _avg_exposure(holdings_log: List[Dict[str, Any]]) -> float:
    ws: List[float] = []
    for snap in holdings_log or []:
        hs = snap.get("holdings") or []
        ws.append(sum(float(h.get("weight", 0)) for h in hs) if hs else 0.0)
    return float(sum(ws) / len(ws)) if ws else 0.0


def _clear_caches() -> None:
    from zstock.common.config import strategy_config
    from zstock.strategy_management.pipeline import StrategyPipeline
    from zstock.strategy_management.script import backtester as bt_mod

    StrategyPipeline._config_cache = None
    strategy_config._clear_cache()
    bt_mod._CONFIG_CACHE = None


def _naked_factor_override() -> Dict[str, Any]:
    return {
        "market_overlay": {
            "position_scale_green": 1.0,
            "position_scale_yellow": 1.0,
        },
        "final_score": {
            "top_k": 5,
            "by_regime": {"reversal": {"top_k": 5}},
        },
        "weak_regime_protection": {"enabled": False},
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


def _load_current_rows() -> Dict[str, Dict[str, float]]:
    import pandas as pd

    if not CURRENT_CSV.exists():
        return {}
    df = pd.read_csv(CURRENT_CSV)
    out: Dict[str, Dict[str, float]] = {}
    mapping = {
        "current_2024": "2024",
        "current_2025": "2025",
        "current_2026": "2026",
    }
    for _, row in df.iterrows():
        key = mapping.get(str(row.get("key", "")))
        if not key:
            continue
        out[key] = {
            "total_return": float(row["total_return"]),
            "sharpe": float(row["sharpe"]),
            "max_drawdown": float(row["max_drawdown"]),
            "avg_exposure": float(row["avg_exposure"]),
        }
    return out


async def _run_naked_year(provider, start: str, end: str, output_dir: Path, label: str) -> Dict[str, Any]:
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
    from zstock.strategy_management.script.backtester import Backtester

    _clear_caches()
    seg_dir = output_dir / "segments" / label
    seg_dir.mkdir(parents=True, exist_ok=True)
    pipe = CrossSectionStrategyPipeline(config_override=_naked_factor_override())
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
    result.plot(output_path=str(seg_dir / "equity.png"), title=f"naked {label} {start}~{end}")
    result.export_csv(str(seg_dir))
    (seg_dir / "summary.txt").write_text(result.summary(), encoding="utf-8")
    m = result.metrics
    row = {
        "status": "success",
        "label": label,
        "start": start,
        "end": end,
        "total_return": float(m.get("total_return", 0)),
        "total_return_gross": float(m.get("total_return_gross", 0)),
        "annualized_return": float(m.get("annualized_return", 0)),
        "sharpe": float(m.get("sharpe", 0)),
        "max_drawdown": float(m.get("max_drawdown", 0)),
        "avg_exposure": _avg_exposure(result.holdings_log),
        "total_cost": float(m.get("total_cost", 0)),
        "rebalance_count": int(m.get("rebalance_count", 0)),
        "final_equity": float(result.equity_curve.iloc[-1]) if len(result.equity_curve) else 1.0,
    }
    logger.info(
        "  naked %s ret=%.2f%% sharpe=%.3f mdd=%.2f%% exp=%.1f%%",
        label,
        row["total_return"] * 100,
        row["sharpe"],
        row["max_drawdown"] * 100,
        row["avg_exposure"] * 100,
    )
    return row


def _fmt_pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


def _report(naked: Dict[str, Dict[str, Any]], current: Dict[str, Dict[str, float]]) -> str:
    lines = [
        "裸回测 vs 当前配置（v1.16.0）",
        f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "裸回测关闭: 黄灯缩仓、reversal top_k=3、弱段只减不加、回撤节流",
        "仍保留: T+1 开盘、费率 0.15%、红灯空仓、top_k=5、单票上限 20%",
        "",
        f"{'年份':<8} {'当前收益':>10} {'裸回测':>10} {'当前仓位':>10} {'裸仓位':>10} {'沪深300':>10} {'裸-指数':>10}",
        "-" * 78,
    ]
    for year, _, _ in PERIODS:
        n = naked.get(year) or {}
        c = current.get(year) or {}
        hs = HS300[year]
        nr = float(n.get("total_return", 0))
        cr = float(c.get("total_return", 0))
        ne = float(n.get("avg_exposure", 0))
        ce = float(c.get("avg_exposure", 0))
        lines.append(
            f"{year:<8} {_fmt_pct(cr):>10} {_fmt_pct(nr):>10} "
            f"{ce * 100:>9.1f}% {ne * 100:>9.1f}% {_fmt_pct(hs):>10} {_fmt_pct(nr - hs):>10}"
        )
    lines.append("")
    lines.append("解读:")
    lines.append("  裸回测仍大幅跑输沪深300 → 选股边不够，调仓位意义不大。")
    lines.append("  裸回测能打平/小赢指数、当前配置却很差 → 边还在，是风控/缩仓在拖后腿。")
    return "\n".join(lines)


async def async_main() -> int:
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.factor_management.script.网格搜索.grid_search_real import load_ohlcv
    from zstock.strategy_management.script.backtester import make_ohlcv_provider_from_dict

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for name in ("zstock", "zstock.factor_management", "zstock.strategy_management", "app"):
        logging.getLogger(name).setLevel(logging.WARNING)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    await init_zstock_database()
    try:
        logger.info("加载 OHLCV 2024-01-01 → 2026-09-08")
        ohlcv = await load_ohlcv("2024-01-01", "2026-09-08")
        if not ohlcv:
            logger.error("无 OHLCV")
            return 1
        provider = make_ohlcv_provider_from_dict(ohlcv)
        logger.info("OHLCV %d 只", len(ohlcv))

        naked: Dict[str, Dict[str, Any]] = {}
        for year, start, end in PERIODS:
            logger.info("裸回测 %s %s → %s", year, start, end)
            naked[year] = await _run_naked_year(provider, start, end, OUTPUT_DIR, f"naked_{year}")

        current = _load_current_rows()
        report = _report(naked, current)
        (OUTPUT_DIR / "ablation_report.txt").write_text(report, encoding="utf-8")
        with open(OUTPUT_DIR / "naked_results.json", "w", encoding="utf-8") as f:
            json.dump(naked, f, ensure_ascii=False, indent=2)
        print(report)
        logger.info("报告: %s", OUTPUT_DIR / "ablation_report.txt")
        return 0
    finally:
        await close_zstock_database()


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
