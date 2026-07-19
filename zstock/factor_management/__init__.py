"""
zstock 因子管理模块 (研究层)

这是系统的第2层（研究层），负责：
1. 因子计算：使用 Qlib Alpha158 + 自定义因子
2. 因子预处理：去极值、标准化、中性化
3. 因子存储：将计算结果保存到 MongoDB + Redis
4. 模型训练：LightGBM、Linear、MLP 等模型
5. 回测验证：使用 Qlib 回测框架进行性能评估

模块结构：
- factor_calculator: 因子计算核心
- factor_preprocessor: 因子预处理
- model_trainer: 模型训练
- backtest_engine: 回测验证
"""

# 核心类
from zstock.factor_management.llm_strategy.factor_calculator import FactorCalculator
from zstock.factor_management.llm_strategy.factor_preprocessor import FactorPreprocessor
from zstock.factor_management.llm_strategy.model_trainer import ModelTrainer
from zstock.factor_management.llm_strategy.backtest_engine import BacktestEngine

# 截面策略相关
from .prefilters import PreFilters
from .sector_factors import SectorFactors
from .dragon_factors import DragonFactors
from .force_factors import ForceFactors
from .market_factors import MarketFactors
from .pipeline import CrossSectionStrategyPipeline

__all__ = [
    'FactorCalculator',
    'FactorPreprocessor',
    'ModelTrainer',
    'BacktestEngine',
    # 截面策略
    'PreFilters',
    'SectorFactors',
    'DragonFactors',
    'ForceFactors',
    'MarketFactors',
    'CrossSectionStrategyPipeline',
]
