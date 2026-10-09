"""
zstock 策略管理模块（截面因子方案）

新版策略层基于 zstock.factor_management.pipeline.CrossSectionStrategyPipeline
产出的截面信号做组合优化、风控、换手控制和回测。
"""

from .pipeline import StrategyPipeline
from .portfolio_optimizer import PortfolioOptimizer
from .risk_manager import RiskManager
from .turnover_controller import TurnoverController

__all__ = [
    'PortfolioOptimizer',
    'RiskManager',
    'TurnoverController',
    'StrategyPipeline',
]
