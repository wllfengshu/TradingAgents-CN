"""
zstock 策略管理

领域：
  sentiment / selection / market_book
编排支撑：
  strategy_section  截面股票池、打分、配权、合规检查、持仓和强制卖出
  market_book      灯和风格 → 防守 / 中性 / 进攻
  sentiment        市场情绪，返回红黄绿
编排入口：
  StrategyPipeline（情绪+选股 → 红黄绿账户 → 组合/风控/换手）
"""

from .pipeline import StrategyPipeline
from .strategy_section import PortfolioOptimizer, RiskManager, SectionStrategy
from .turnover_controller import TurnoverController
from .strategy_section.universe import build_universe_by_date, normalize_universe_mode

__all__ = [
    "StrategyPipeline",
    "PortfolioOptimizer",
    "SectionStrategy",
    "RiskManager",
    "TurnoverController",
    "build_universe_by_date",
    "normalize_universe_mode",
]
