"""自适应再平衡：对照正式 strategy_params.json v1.16.0。"""

from zstock.strategy_management.adaptive_rebalance import resolve_rebalance_freq


def test_disabled_returns_default(strategy_params):
    cfg = dict(strategy_params["adaptive_rebalance"])
    cfg["enabled"] = False
    assert resolve_rebalance_freq(cfg, default_freq=5) == 5
    assert resolve_rebalance_freq(None, default_freq=5) == 5


def test_production_use_min_freq_reversal_yellow(strategy_params):
    """production: reversal=3, yellow=3, use_min_freq → 3，夹在 [3,8]。"""
    cfg = strategy_params["adaptive_rebalance"]
    assert cfg["enabled"] is True
    assert cfg["use_min_freq"] is True
    freq = resolve_rebalance_freq(cfg, regime="reversal", market_grade="yellow", default_freq=5)
    assert freq == 3


def test_production_neutral_green_uses_min_of_4_and_5(strategy_params):
    cfg = strategy_params["adaptive_rebalance"]
    freq = resolve_rebalance_freq(cfg, regime="neutral", market_grade="green", default_freq=5)
    assert freq == 4


def test_production_red_clamped_by_max_freq(strategy_params):
    """red=8, momentum=5, min → 5。"""
    cfg = strategy_params["adaptive_rebalance"]
    freq = resolve_rebalance_freq(cfg, regime="momentum", market_grade="red", default_freq=5)
    assert freq == 5


def test_use_max_freq_when_flag_off(strategy_params):
    cfg = dict(strategy_params["adaptive_rebalance"])
    cfg["use_min_freq"] = False
    freq = resolve_rebalance_freq(cfg, regime="reversal", market_grade="green", default_freq=5)
    # reversal=3, green=5 → max=5
    assert freq == 5


def test_clamp_to_min_max(strategy_params):
    cfg = dict(strategy_params["adaptive_rebalance"])
    cfg["by_regime"] = {"volatile": 1}
    cfg["by_market_grade"] = {"yellow": 1}
    freq = resolve_rebalance_freq(cfg, regime="volatile", market_grade="yellow", default_freq=5)
    assert freq == int(cfg["min_freq"])
