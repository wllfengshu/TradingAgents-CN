import asyncio
import json
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
import pandas as pd

from zstock.common.utils.db_utils import init_zstock_database, close_zstock_database
from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
from zstock.strategy_management.pipeline import StrategyPipeline
from zstock.common.config.strategy_config import load_strategy_params

async def test_rebalance():
    await init_zstock_database()
    cfg = load_strategy_params()
    cfg.setdefault("turnover_control", {})["fill_open_slots"] = True
    cfg.setdefault("portfolio_optimization", {})["fill_weights_to_position_scale"] = True
    cfg["portfolio_optimization"]["max_single_stock_cap"] = 0.50
    fp = CrossSectionStrategyPipeline()
    sp = StrategyPipeline(factor_pipeline=fp)
    
    # 2020-02-17
    td = "2020-02-17"
    signals = await fp.score_signals(td)
    print(f"Signals count: {len(signals)}")
    print(signals[["code", "strategy_signal_score", "final_score", "rank"]].head(6))
    
    # Check with current_positions from previous holding
    # Suppose previous holding was 1 stock
    cur_pos = pd.DataFrame([{"code": "601101", "weight": 0.45, "score": 87.72}])
    
    res = await sp.execute_full_pipeline(
        trade_date=td,
        current_positions=cur_pos,
        precomputed_signals=signals,
        config=cfg,
    )
    print("\nResult status:", res["status"])
    print("Optimization holdings:\n", res["results"]["optimization"]["holdings_df"])
    print("Final holdings:\n", res["results"]["final_holdings"])
    
    await close_zstock_database()

if __name__ == "__main__":
    asyncio.run(test_rebalance())
