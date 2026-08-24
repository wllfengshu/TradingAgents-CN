"""v1.16 弱段保护 2024 样本内 A/B。"""

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
MIN_ANN = 0.10


def _load() -> Dict[str, Any]:
    with open(_PARAMS, "r", encoding="utf-8") as f:
        return json.load(f)


def _variants(base: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    v151 = copy.deepcopy(base)
    v151["weak_regime_protection"] = {"enabled": False}

    ro = copy.deepcopy(base)
    ro["weak_regime_protection"] = {
        "enabled": True,
        "reduce_only": {
            "enabled": True,
            "when_regime": ["reversal"],
            "when_market_grade": ["yellow"],
        },
        "drawdown_throttle": {"enabled": False},
    }

    dd = copy.deepcopy(base)
    dd["weak_regime_protection"] = {
        "enabled": True,
        "reduce_only": {"enabled": False},
        "drawdown_throttle": {
            "enabled": True,
            "lookback_days": 20,
            "drawdown_threshold": 0.10,
            "scale_factor": 0.7,
        },
    }

    return [
        ("v151_baseline", v151),
        ("reduce_only", ro),
        ("drawdown_throttle", dd),
        ("v116_both", base),
    ]


async def _run(cfg: Dict, ohlcv: dict, start: str, end: str) -> Dict[str, float]:
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
    from zstock.strategy_management.pipeline import StrategyPipeline
    from zstock.strategy_management.script.backtester import Backtester, make_ohlcv_provider_from_dict

    StrategyPipeline._config_cache = None
    fp = CrossSectionStrategyPipeline()
    bt = Backtester(fee_rate=0.0015, factor_pipeline=fp)
    r = await bt.run(
        start_date=start,
        end_date=end,
        ohlcv_provider=make_ohlcv_provider_from_dict(ohlcv),
        strategy_config=cfg,
        rebalance_freq=int(cfg["backtest"]["rebalance_freq"]),
        use_precomputed_factors=True,
        verbose=False,
    )
    m = r.metrics
    return {
        "total_return": float(m["total_return"]),
        "annualized_return": float(m["annualized_return"]),
        "max_drawdown": float(m["max_drawdown"]),
        "sharpe": float(m["sharpe"]),
    }


async def main() -> int:
    from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
    from zstock.factor_management.script.网格搜索.grid_search_real import load_ohlcv

    logging.basicConfig(level=logging.WARNING)
    try:
        await init_zstock_database()
    except Exception:
        from app.core.database import db_manager
        await db_manager.init_mongodb()

    base = _load()
    out = Path(__file__).resolve().parent / "output" / "v16_tune_2024"
    out.mkdir(parents=True, exist_ok=True)

    ohlcv_full = await load_ohlcv("2024-01-01", "2024-12-31")
    ohlcv_q2 = await load_ohlcv("2024-04-01", "2024-06-30")
    rows = []
    try:
        for label, cfg in _variants(base):
            logger.info("回测 %s", label)
            full = await _run(cfg, ohlcv_full, "2024-01-01", "2024-12-31")
            q2 = await _run(cfg, ohlcv_q2, "2024-04-01", "2024-06-30")
            rows.append({
                "variant": label,
                "full_ann": full["annualized_return"],
                "full_ret": full["total_return"],
                "full_mdd": full["max_drawdown"],
                "full_sharpe": full["sharpe"],
                "q2_ret": q2["total_return"],
                "q2_mdd": q2["max_drawdown"],
                "passes": full["annualized_return"] >= MIN_ANN,
            })
        df = pd.DataFrame(rows)
        ok = df[df["passes"]].sort_values("q2_ret", ascending=False)
        df.to_csv(out / "v16_tune_2024.csv", index=False, encoding="utf-8-sig")
        print(df.to_string(index=False))
        if not ok.empty:
            b = ok.iloc[0]
            print(f"\n-> 推荐: {b['variant']} Q2={b['q2_ret']*100:.2f}% 全年年化={b['full_ann']*100:.2f}%")
        return 0
    finally:
        await close_zstock_database()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
