import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.common.utils.db_utils import init_zstock_database, close_zstock_database
from zstock.strategy_management.script.backtester import Backtester
from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
from zstock.strategy_management.pipeline import StrategyPipeline
from zstock.common.config.strategy_config import load_strategy_params

async def test_buffer_effects():
    await init_zstock_database()
    base_cfg = load_strategy_params()
    # v129_cap50 settings
    base_cfg.setdefault("portfolio_optimization", {})
    base_cfg["portfolio_optimization"]["fill_weights_to_position_scale"] = True
    base_cfg["portfolio_optimization"]["max_single_stock_cap"] = 0.50
    
    fp = CrossSectionStrategyPipeline()
    sp = StrategyPipeline(factor_pipeline=fp)
    bt = Backtester(strategy_pipeline=sp, factor_pipeline=fp)
    
    # 1. Base with buffer_threshold = 0.25
    cfg_base = json.loads(json.dumps(base_cfg))
    cfg_base["turnover_control"]["buffer_threshold"] = 0.25
    bt_base = Backtester(factor_pipeline=fp)
    res_base = await bt_base.run_real_data(
        start_date="2020-01-01",
        end_date="2020-12-31",
        use_precomputed_factors=True,
        strategy_config=cfg_base,
        save_outputs=False,
        verbose=False,
    )
    m_base = res_base.metrics
    print(f"2020 buffer 0.25: ret={m_base['total_return']:.2%}, mdd={m_base['max_drawdown']:.2%}, sharpe={m_base.get('sharpe_ratio', 0):.2f}")

    # 2. With buffer_threshold = 0.05
    cfg_b05 = json.loads(json.dumps(base_cfg))
    cfg_b05["turnover_control"]["buffer_threshold"] = 0.05
    bt_b05 = Backtester(factor_pipeline=fp)
    res_b05 = await bt_b05.run_real_data(
        start_date="2020-01-01",
        end_date="2020-12-31",
        use_precomputed_factors=True,
        strategy_config=cfg_b05,
        save_outputs=False,
        verbose=False,
    )
    m_b05 = res_b05.metrics
    print(f"2020 buffer 0.05: ret={m_b05['total_return']:.2%}, mdd={m_b05['max_drawdown']:.2%}, sharpe={m_b05.get('sharpe_ratio', 0):.2f}")

    # 3. With buffer_threshold = 0.0 (no buffer barrier for additions)
    cfg_b00 = json.loads(json.dumps(base_cfg))
    cfg_b00["turnover_control"]["buffer_threshold"] = 0.0
    bt_b00 = Backtester(factor_pipeline=fp)
    res_b00 = await bt_b00.run_real_data(
        start_date="2020-01-01",
        end_date="2020-12-31",
        use_precomputed_factors=True,
        strategy_config=cfg_b00,
        save_outputs=False,
        verbose=False,
    )
    m_b00 = res_b00.metrics
    print(f"2020 buffer 0.00: ret={m_b00['total_return']:.2%}, mdd={m_b00['max_drawdown']:.2%}, sharpe={m_b00.get('sharpe_ratio', 0):.2f}")

    await close_zstock_database()

if __name__ == "__main__":
    asyncio.run(test_buffer_effects())
