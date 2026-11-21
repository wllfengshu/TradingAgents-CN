"""Backtester：自适应再平衡频率与回撤降仓分支。"""

from __future__ import annotations

import pandas as pd
import pytest

from zstock.strategy_management.strategy_section import execute as exec_stage
from zstock.strategy_management.pipeline import StrategyPipeline
from zstock.strategy_management.script.backtester import Backtester


def test_grade_fallback_uses_execution_api_not_missing_pipeline_method():
    """回归：summary 缺 market_grade 时不得再调 StrategyPipeline._extract_market_grade。"""
    assert not hasattr(StrategyPipeline, "_extract_market_grade")
    sig = pd.DataFrame({"code": ["600000"], "market_grade": ["yellow"]})
    summary: dict = {"status": "success"}  # 故意无 market_grade
    grade = summary.get("market_grade") or exec_stage.extract_market_grade(sig)
    assert grade == "yellow"


def test_resolve_rebalance_freq_branches():
    assert Backtester._resolve_rebalance_freq(None, default_freq=5) == 5
    assert Backtester._resolve_rebalance_freq({"enabled": False}, default_freq=7) == 7

    cfg = {
        "enabled": True,
        "default_freq": 5,
        "by_regime": {"momentum": 3, "reversal": 8, "neutral": 5},
        "by_market_grade": {"green": 3, "yellow": 5, "red": 8},
        "use_min_freq": True,
        "min_freq": 3,
        "max_freq": 8,
    }
    assert Backtester._resolve_rebalance_freq(
        cfg, regime="momentum", market_grade="yellow", default_freq=5
    ) == 3
    cfg2 = dict(cfg, use_min_freq=False)
    assert Backtester._resolve_rebalance_freq(
        cfg2, regime="momentum", market_grade="yellow", default_freq=5
    ) == 5


def test_compute_drawdown_scale_branches():
    assert Backtester._compute_drawdown_scale([], None) == 1.0
    assert Backtester._compute_drawdown_scale([1.0, 1.1], {"enabled": False}) == 1.0
    assert (
        Backtester._compute_drawdown_scale(
            [1.0, 1.1], {"enabled": True, "drawdown_throttle": {"enabled": False}}
        )
        == 1.0
    )

    # 单档：回撤超过阈值
    equity = [1.0, 1.1, 1.2, 1.0]  # peak 1.2 → dd ~ -16.7%
    cfg = {
        "enabled": True,
        "drawdown_throttle": {
            "enabled": True,
            "lookback_days": 0,
            "drawdown_threshold": -0.10,
            "scale_factor": 0.5,
        },
    }
    scale = Backtester._compute_drawdown_scale(equity, cfg)
    assert scale == pytest.approx(0.5)

    # 多档
    cfg2 = {
        "enabled": True,
        "drawdown_throttle": {
            "enabled": True,
            "lookback_days": 10,
            "tiers": [
                {"drawdown_threshold": -0.05, "scale_factor": 0.8},
                {"drawdown_threshold": -0.15, "scale_factor": 0.4},
            ],
        },
    }
    scale2 = Backtester._compute_drawdown_scale(equity, cfg2)
    assert 0 < scale2 <= 0.8
