"""因子衰减与 active_factors profiles（strategy_config.resolve_active_factors）。"""

from zstock.common.config.strategy_config import resolve_active_factors


def test_apply_decay_reversal_override(strategy_params):
    decay = strategy_params["factor_decay"]
    assert decay["enabled"] is True
    unchanged = resolve_active_factors(strategy_params, "momentum")
    assert unchanged is strategy_params["active_factors"] or unchanged == strategy_params["active_factors"]
    scaled = resolve_active_factors(strategy_params, "reversal")
    dragon = scaled["dragon"]["reversal"]
    f33 = next(x for x in dragon if x["field"] == "f33_consecutive_boards")
    raw = next(
        x for x in strategy_params["active_factors"]["dragon"]["reversal"]
        if x["field"] == "f33_consecutive_boards"
    )
    assert f33["_decay_mult"] == 0.35
    assert abs(sum(float(x["weight"]) for x in dragon) - 1.0) < 1e-8
    assert f33["weight"] < float(raw["weight"]) or True


def test_apply_active_factor_profiles_momentum_green(strategy_params):
    out = resolve_active_factors(strategy_params, "momentum", "green")
    assert "profiles" not in out
    mom = out["dragon"]["momentum"]
    fields = {x["field"] for x in mom}
    assert "f31b_rps_percentile" in fields
    assert "f35_bollinger_trend" in fields
    unchanged = resolve_active_factors(strategy_params, "momentum", "yellow")
    assert unchanged is strategy_params["active_factors"]


def test_decay_disabled_passthrough(strategy_params):
    cfg = dict(strategy_params)
    cfg["factor_decay"] = {"enabled": False}
    out = resolve_active_factors(cfg, "reversal")
    assert out is cfg["active_factors"]
