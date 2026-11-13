"""包导出。"""

import zstock.strategy_management as sm
from zstock.strategy_management import (
    PortfolioOptimizer,
    RiskManager,
    StrategyPipeline,
    TurnoverController,
)


def test_public_exports():
    assert PortfolioOptimizer is not None
    assert RiskManager is not None
    assert TurnoverController is not None
    assert StrategyPipeline is not None
    assert "Backtester" not in sm.__all__
    assert "BacktestResult" not in sm.__all__
