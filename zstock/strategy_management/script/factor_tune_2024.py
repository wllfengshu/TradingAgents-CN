"""
2024 样本内因子调参：基于 IC 测评重配 active_factors，对比多方案。

仅使用 2024-01-01 ~ 2024-12-31 数据选型，避免过拟合。

用法：
    python -m zstock.strategy_management.script.factor_tune_2024
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

_PARAMS = PROJECT_ROOT / "zstock" / "common" / "config" / "strategy_params.json"
START, END = "2024-01-01", "2024-12-31"

# ── 2024 IC 测评驱动的因子集（A/B 级 + 极性修正）────────────────────────────

_SECTOR_IC = [
    {"field": "f30_sector_concentration", "weight": 0.50, "polarity": "negative"},
    {"field": "f28_consistency", "weight": 0.30, "polarity": "positive"},
    {"field": "f21_rps_20d", "weight": 0.20, "polarity": "negative"},
]

_DRAGON_IC = [
    {"field": "f33_consecutive_boards", "weight": 0.30, "polarity": "negative"},
    {"field": "f32_amount", "weight": 0.22, "polarity": "negative"},
    {"field": "f31_excess_return_10d", "weight": 0.20, "polarity": "negative"},
    {"field": "f37_relative_strength", "weight": 0.14, "polarity": "negative"},
    {"field": "f36_identity_premium", "weight": 0.14, "polarity": "negative"},
]

_DRAGON_IC_EXT = _DRAGON_IC + [
    {"field": "f35_bollinger_trend", "weight": 0.10, "polarity": "negative"},
]

_FORCE_MOMENTUM_IC = [
    {"field": "fcoop1_main_net_ratio", "weight": 0.55, "polarity": "negative"},
    {"field": "f_mean_reversion_signal", "weight": 0.45, "polarity": "negative"},
]

_FORCE_REVERSAL_IC = [
    {"field": "f_mean_reversion_signal", "weight": 0.70, "polarity": "negative"},
    {"field": "fcoop1_main_net_ratio", "weight": 0.30, "polarity": "negative"},
]


def _dup(lst: List[Dict]) -> List[Dict]:
    return copy.deepcopy(lst)


def _apply_factors(cfg: Dict[str, Any], sector=None, dragon=None, force_m=None, force_r=None) -> Dict:
    c = copy.deepcopy(cfg)
    af = c.setdefault("active_factors", {})
    if sector is not None:
        af["sector"] = {"momentum": _dup(sector), "reversal": _dup(sector)}
    if dragon is not None:
        af["dragon"] = {"momentum": _dup(dragon), "reversal": _dup(dragon)}
    if force_m is not None or force_r is not None:
        fc = af.setdefault("force", {})
        if force_m is not None:
            fc["momentum"] = _dup(force_m)
        if force_r is not None:
            fc["reversal"] = _dup(force_r)
    return c


def _variants(base: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    return [
        ("v141_current", base),
        (
            "ic_fix_sector",
            _apply_factors(base, sector=_SECTOR_IC),
        ),
        (
            "ic_dragon",
            _apply_factors(base, dragon=_DRAGON_IC),
        ),
        (
            "ic_sector_dragon",
            _apply_factors(base, sector=_SECTOR_IC, dragon=_DRAGON_IC),
        ),
        (
            "ic_full",
            _apply_factors(
                base,
                sector=_SECTOR_IC,
                dragon=_DRAGON_IC,
                force_m=_FORCE_MOMENTUM_IC,
                force_r=_FORCE_REVERSAL_IC,
            ),
        ),
        (
            "ic_full_ext",
            _apply_factors(
                base,
                sector=_SECTOR_IC,
                dragon=_DRAGON_IC_EXT,
                force_m=_FORCE_MOMENTUM_IC,
                force_r=_FORCE_REVERSAL_IC,
            ),
        ),
    ]


def _load_base() -> Dict[str, Any]:
    with open(_PARAMS, "r", encoding="utf-8") as f:
        return json.load(f)


async def _run_one(label: str, cfg: Dict[str, Any], ohlcv: dict) -> Dict[str, Any]:
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
    from zstock.strategy_management.pipeline import StrategyPipeline
    from zstock.strategy_management.script.backtester import Backtester, make_ohlcv_provider_from_dict

    StrategyPipeline._config_cache = None
    fp_override = {
        "active_factors": cfg.get("active_factors"),
        "sector_layer": cfg.get("sector_layer"),
        "dragon_layer": cfg.get("dragon_layer"),
        "cooperative_force": cfg.get("cooperative_force"),
        "final_score": cfg.get("final_score"),
        "factor_decay": cfg.get("factor_decay"),
    }
    fp = CrossSectionStrategyPipeline(config_override=fp_override)
    bt = Backtester(fee_rate=0.0015, factor_pipeline=fp)
    provider = make_ohlcv_provider_from_dict(ohlcv)
    reb = int(cfg.get("backtest", {}).get("rebalance_freq", 5))
    result = await bt.run(
        start_date=START,
        end_date=END,
        ohlcv_provider=provider,
        strategy_config=cfg,
        rebalance_freq=reb,
        use_precomputed_factors=True,
        verbose=False,
    )
    m = result.metrics
    return {
        "variant": label,
        "total_return": float(m.get("total_return", 0)),
        "annualized_return": float(m.get("annualized_return", 0)),
        "sharpe": float(m.get("sharpe", 0)),
        "max_drawdown": float(m.get("max_drawdown", 0)),
        "rebalance_count": int(m.get("rebalance_count", 0)),
    }


async def async_main() -> int:
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.factor_management.script.网格搜索.grid_search_real import load_ohlcv

    logging.basicConfig(level=logging.WARNING)
    logging.getLogger(__name__).setLevel(logging.INFO)

    try:
        await init_zstock_database()
    except Exception:
        from app.core.database import db_manager
        await db_manager.init_mongodb()

    base = _load_base()
    out_dir = Path(__file__).resolve().parent / "output" / "factor_tune_2024"
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("加载 OHLCV %s ~ %s", START, END)
    ohlcv = await load_ohlcv(START, END)

    rows: List[Dict[str, Any]] = []
    try:
        for label, cfg in _variants(base):
            logger.info("回测 %s", label)
            rows.append(await _run_one(label, cfg, ohlcv))

        df = pd.DataFrame(rows).sort_values("annualized_return", ascending=False)
        df.to_csv(out_dir / "factor_tune_2024.csv", index=False, encoding="utf-8-sig")

        lines = [
            "",
            "=" * 88,
            "2024 因子调参（仅样本内，IC 驱动）",
            "=" * 88,
            f"{'variant':<20} {'ann_ret':>8} {'total':>8} {'sharpe':>7} {'mdd':>8} {'reb':>4}",
        ]
        for _, r in df.iterrows():
            lines.append(
                f"{r['variant']:<20} {r['annualized_return']*100:>7.2f}% "
                f"{r['total_return']*100:>7.2f}% {r['sharpe']:>7.3f} "
                f"{r['max_drawdown']*100:>7.2f}% {int(r['rebalance_count']):>4}"
            )
        best = df.iloc[0]
        lines += [
            "",
            f"-> 最优: {best['variant']} (年化 {best['annualized_return']*100:.2f}%)",
            "=" * 88,
        ]
        report = "\n".join(lines)
        (out_dir / "factor_tune_2024_report.txt").write_text(report, encoding="utf-8")
        print(report)
        return 0
    finally:
        await close_zstock_database()


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
