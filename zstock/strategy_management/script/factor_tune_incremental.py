"""2024 增量诊断：逐项验证 adaptive / 因子修正的贡献。"""

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

_PARAMS = PROJECT_ROOT / "zstock" / "common" / "config" / "strategy_params.json"
START, END = "2024-01-01", "2024-12-31"


def _load() -> Dict[str, Any]:
    with open(_PARAMS, "r", encoding="utf-8") as f:
        return json.load(f)


def _variants(base: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    no_adapt = copy.deepcopy(base)
    no_adapt["adaptive_rebalance"] = {**no_adapt.get("adaptive_rebalance", {}), "enabled": False}

    fix_f28 = copy.deepcopy(base)
    for regime in ("momentum", "reversal"):
        for fc in fix_f28["active_factors"]["sector"][regime]:
            if fc["field"] == "f28_consistency":
                fc["polarity"] = "positive"

    trim_dragon = copy.deepcopy(base)
    keep = {"f33_consecutive_boards", "f34_resonance_pct_5d", "f36_identity_premium"}
    for regime in ("momentum", "reversal"):
        lst = trim_dragon["active_factors"]["dragon"][regime]
        trimmed = [x for x in lst if x["field"] in keep]
        w = 1.0 / len(trimmed)
        for x in trimmed:
            x["weight"] = w
        trim_dragon["active_factors"]["dragon"][regime] = trimmed

    add_b_dragon = copy.deepcopy(base)
    add = [
        {"field": "f33_consecutive_boards", "weight": 0.25, "polarity": "negative"},
        {"field": "f32_amount", "weight": 0.20, "polarity": "negative"},
        {"field": "f31_excess_return_10d", "weight": 0.20, "polarity": "negative"},
        {"field": "f36_identity_premium", "weight": 0.15, "polarity": "negative"},
        {"field": "f34_resonance_pct_5d", "weight": 0.20, "polarity": "negative"},
    ]
    for regime in ("momentum", "reversal"):
        add_b_dragon["active_factors"]["dragon"][regime] = copy.deepcopy(add)

    yellow_07 = copy.deepcopy(base)
    yellow_07.setdefault("market_overlay", {})["position_scale_yellow"] = 0.7

    return [
        ("no_adaptive", no_adapt),
        ("adaptive_v141", base),
        ("fix_f28_polarity", fix_f28),
        ("trim_weak_dragon", trim_dragon),
        ("add_b_grade_dragon", add_b_dragon),
        ("yellow_scale_0.7", yellow_07),
    ]


async def _run(label: str, cfg: Dict, ohlcv: dict) -> Dict:
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
    from zstock.strategy_management.pipeline import StrategyPipeline
    from zstock.strategy_management.script.backtester import Backtester, make_ohlcv_provider_from_dict

    StrategyPipeline._config_cache = None
    fp = CrossSectionStrategyPipeline(config_override={
        "active_factors": cfg.get("active_factors"),
        "sector_layer": cfg.get("sector_layer"),
        "dragon_layer": cfg.get("dragon_layer"),
        "cooperative_force": cfg.get("cooperative_force"),
        "final_score": cfg.get("final_score"),
    })
    bt = Backtester(fee_rate=0.0015, factor_pipeline=fp)
    r = await bt.run(
        start_date=START, end_date=END,
        ohlcv_provider=make_ohlcv_provider_from_dict(ohlcv),
        strategy_config=cfg,
        rebalance_freq=int(cfg["backtest"]["rebalance_freq"]),
        use_precomputed_factors=True, verbose=False,
    )
    m = r.metrics
    return {"variant": label, "annualized_return": m["annualized_return"],
            "total_return": m["total_return"], "sharpe": m["sharpe"],
            "max_drawdown": m["max_drawdown"], "rebalance_count": m["rebalance_count"]}


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
    ohlcv = await load_ohlcv(START, END)
    out = Path(__file__).resolve().parent / "output" / "factor_tune_2024"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    try:
        for label, cfg in _variants(base):
            print(f"Running {label}...", flush=True)
            rows.append(await _run(label, cfg, ohlcv))
        df = pd.DataFrame(rows).sort_values("annualized_return", ascending=False)
        df.to_csv(out / "incremental_2024.csv", index=False, encoding="utf-8-sig")
        print(df.to_string(index=False))
        return 0
    finally:
        await close_zstock_database()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
