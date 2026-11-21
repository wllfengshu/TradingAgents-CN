"""
zstock 因子管理模块（研究 / 定义层）

职责：因子公式、因子目录与预计算脚本。
宇宙准入过滤见 zstock.common.prefilters.PreFilters。
选股漏斗见 strategy_management.StrategyPipeline。
注意：因子层，只负责单个因子，不要做策略层的逻辑！！！
"""

from __future__ import annotations

from .factors_dragon import DragonFactors
from .factors_force import ForceFactors
from .factors_market import MarketFactors
from .factors_sector import SectorFactors
from zstock.common.entity.factor_spec import FactorRegistry, FactorSpec, factor
from zstock.factor_management.registry_bootstrap import ensure_factors_loaded

__all__ = [
    "SectorFactors",
    "DragonFactors",
    "ForceFactors",
    "MarketFactors",
    "FactorSpec",
    "FactorRegistry",
    "factor",
    "ensure_factors_loaded",
]
