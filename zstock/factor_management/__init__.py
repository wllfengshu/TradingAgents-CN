"""
zstock 因子管理模块 (研究层)

截面日频策略（MongoDB 预计算因子）与可选的 Qlib/LLM 研究路径并存。
默认导入不依赖 Qlib；llm_strategy 相关类在首次访问时才加载。
"""

from __future__ import annotations

import importlib
from typing import Any

from .prefilters import PreFilters
from .sector_factors import SectorFactors
from .dragon_factors import DragonFactors
from .force_factors import ForceFactors
from .market_factors import MarketFactors
from .pipeline import CrossSectionStrategyPipeline

_LAZY_IMPORTS = {
    "FactorCalculator": (
        "zstock.factor_management.llm_strategy.factor_calculator",
        "FactorCalculator",
    ),
    "FactorPreprocessor": (
        "zstock.factor_management.llm_strategy.factor_preprocessor",
        "FactorPreprocessor",
    ),
    "ModelTrainer": (
        "zstock.factor_management.llm_strategy.model_trainer",
        "ModelTrainer",
    ),
    "BacktestEngine": (
        "zstock.factor_management.llm_strategy.backtest_engine",
        "BacktestEngine",
    ),
}


def __getattr__(name: str) -> Any:
    if name not in _LAZY_IMPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_path, attr = _LAZY_IMPORTS[name]
    module = importlib.import_module(module_path)
    return getattr(module, attr)


__all__ = [
    "FactorCalculator",
    "FactorPreprocessor",
    "ModelTrainer",
    "BacktestEngine",
    "PreFilters",
    "SectorFactors",
    "DragonFactors",
    "ForceFactors",
    "MarketFactors",
    "CrossSectionStrategyPipeline",
]
