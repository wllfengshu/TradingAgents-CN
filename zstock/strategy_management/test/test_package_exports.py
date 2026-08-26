"""包导出。"""

from zstock.strategy_management import (
    Backtester,
    BacktestResult,
    PortfolioOptimizer,
    RiskManager,
    SignalGenerator,
    StrategyPipeline,
    TurnoverController,
)


def test_public_exports():
    assert SignalGenerator is not None
    assert PortfolioOptimizer is not None
    assert RiskManager is not None
    assert TurnoverController is not None
    assert StrategyPipeline is not None
    assert Backtester is not None
    assert BacktestResult is not None
