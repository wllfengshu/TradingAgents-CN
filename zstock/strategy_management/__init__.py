"""
zstock 策略管理模块（截面因子方案）

新版策略层基于 zstock.factor_management.pipeline.CrossSectionStrategyPipeline
产出的截面信号做组合优化、风控、换手控制和回测。
"""

from .signal_generator import SignalGenerator
from .portfolio_optimizer import PortfolioOptimizer
from .risk_manager import RiskManager
from .turnover_controller import TurnoverController
from .pipeline import StrategyPipeline
from zstock.strategy_management.script.backtester import Backtester, BacktestResult

__all__ = [
    'SignalGenerator',
    'PortfolioOptimizer',
    'RiskManager',
    'TurnoverController',
    'StrategyPipeline',
    'Backtester',
    'BacktestResult',
]
