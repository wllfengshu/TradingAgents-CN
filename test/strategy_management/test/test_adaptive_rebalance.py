"""自适应再平衡：对照正式 strategy_params.json v1.16.0。"""

from zstock.strategy_management.script.backtester import Backtester


def test_disabled_returns_default(strategy_params):
    cfg = dict(strategy_params["adaptive_rebalance"])
    cfg["enabled"] = False
    assert Backtester._resolve_rebalance_freq(cfg, default_freq=5) == 5
    assert Backtester._resolve_rebalance_freq(None, default_freq=5) == 5


def test_production_rebalance_is_biweekly(strategy_params):
    """固定大约两周调一次，不再按黄灯或反转缩到 3 天。"""
    cfg = strategy_params["adaptive_rebalance"]
    assert cfg["enabled"] is True
    assert cfg["use_min_freq"] is True
    assert int(cfg["min_freq"]) == 10
    for regime, grade in (
        ("reversal", "yellow"),
        ("neutral", "green"),
        ("momentum", "red"),
        ("reversal", "green"),
    ):
        freq = Backtester._resolve_rebalance_freq(
            cfg, regime=regime, market_grade=grade, default_freq=10
        )
        assert freq == 10


def test_clamp_to_min_max(strategy_params):
    cfg = dict(strategy_params["adaptive_rebalance"])
    cfg["by_regime"] = {"volatile": 1}
    cfg["by_market_grade"] = {"yellow": 1}
    freq = Backtester._resolve_rebalance_freq(
        cfg, regime="volatile", market_grade="yellow", default_freq=5
    )
    assert freq == int(cfg["min_freq"])
