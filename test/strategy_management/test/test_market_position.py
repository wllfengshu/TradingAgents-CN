"""market_position：策略层 M1 缩放。"""

from zstock.strategy_management.pipeline import StrategyPipeline


def test_yellow_scale_from_overlay(strategy_params):
    strategy_params = dict(strategy_params)
    strategy_params["target_equity"] = {"enabled": False}
    strategy_params["market_overlay"] = {"position_scale_yellow": 0.5}
    s = StrategyPipeline._resolve_market_position_scale("yellow", strategy_params)
    assert s == 0.5


def test_target_equity_by_context(strategy_params):
    params = dict(strategy_params)
    params["target_equity"] = {
        "enabled": True,
        "by_context": {"momentum_green": 0.95, "reversal_yellow": 0.45},
    }
    assert StrategyPipeline._resolve_market_position_scale("green", params, regime="momentum") == 0.95
    assert StrategyPipeline._resolve_market_position_scale("yellow", params, regime="reversal") == 0.45
    assert StrategyPipeline._resolve_market_position_scale("red", params, regime="momentum") == 0.0


def test_baseline_yellow_full(strategy_params):
    params = {
        "market_overlay": {"position_scale_yellow": 1.0},
        "market_factors": {"scale_yellow": 1.0},
    }
    assert StrategyPipeline._resolve_market_position_scale("yellow", params) == 1.0
