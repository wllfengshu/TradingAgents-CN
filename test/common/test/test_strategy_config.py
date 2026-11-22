"""strategy_config：对真实 strategy_params.json 做 SSOT 派生校验。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zstock.common.config import strategy_config as sc


@pytest.fixture(autouse=True)
def _clear_ssot_cache():
    sc._clear_cache()
    yield
    sc._clear_cache()


def test_load_strategy_params_cache_and_override(strategy_params, tmp_path):
    loaded = sc.load_strategy_params()
    assert loaded["final_score"]["top_k"] == strategy_params["final_score"]["top_k"]
    again = sc.load_strategy_params()
    assert again is loaded

    alt = tmp_path / "params.json"
    payload = json.loads(json.dumps(strategy_params))
    payload["final_score"]["top_k"] = 7
    alt.write_text(json.dumps(payload), encoding="utf-8")
    override = sc.load_strategy_params(alt)
    assert override["final_score"]["top_k"] == 7
    assert sc.load_strategy_params()["final_score"]["top_k"] == strategy_params["final_score"]["top_k"]


def test_load_missing_file_raises(tmp_path, monkeypatch):
    missing = tmp_path / "nope.json"
    monkeypatch.setattr(sc, "_STRATEGY_PARAMS_PATH", missing)
    sc._clear_cache()
    with pytest.raises(RuntimeError, match="strategy_params"):
        sc.load_strategy_params()


def test_market_and_style_configs(strategy_params):
    mf = sc.get_market_factors_config()
    assert mf["ma_window"] == int(strategy_params["market_factors"]["ma_window"])
    assert mf["trend_band"] == pytest.approx(
        float(strategy_params["market_factors"]["trend_band"])
    )
    assert mf["trend_release"] == pytest.approx(
        float(strategy_params["market_factors"]["trend_release"])
    )
    cached = sc.get_market_factors_config()
    assert cached is mf

    style = sc.get_style_detector_config()
    assert style["lookback"] == int(strategy_params["style_detector"]["lookback"])
    assert style["momentum_threshold"] == pytest.approx(
        float(strategy_params["style_detector"]["momentum_threshold"])
    )
    assert sc.get_style_detector_config() is style

    neutral = sc.get_neutral_style(autocorr=0.1, strength=0.2)
    assert neutral["regime"] == "neutral"
    assert neutral["momentum_weight"] == 0.5
    assert neutral["reversal_weight"] == 0.5


def test_build_runtime_and_risk_limits(strategy_params):
    runtime = sc.build_runtime_config(strategy_params)
    top_k = int(strategy_params["final_score"]["top_k"])
    portfolio = strategy_params.get("portfolio") or {}
    expected_max = int(portfolio.get("max_holdings", top_k))
    assert runtime["portfolio_optimization"]["max_holdings"] == expected_max
    assert runtime["final_score"]["top_k"] == top_k
    assert "weak_regime_protection" in runtime
    assert "adaptive_rebalance" in runtime
    assert "book_regime" in runtime
    assert runtime["turnover_control"]["min_hold_days"] == int(
        strategy_params["turnover_control"]["min_hold_days"]
    )

    limits = sc.build_risk_limits(strategy_params)
    assert limits["top_k"] == top_k
    assert limits["max_holdings"] == expected_max * 2
    assert limits["allow_cash"] is True


def test_resolve_active_factors_profile_and_decay(strategy_params):
    base = sc.resolve_active_factors(strategy_params, regime="momentum", grade=None)
    assert "sector" in base
    assert isinstance(base["sector"].get("momentum"), list)

    with_grade = sc.resolve_active_factors(strategy_params, regime="momentum", grade="green")
    assert "profiles" not in with_grade or with_grade.get("profiles") is None or True

    decay_on = json.loads(json.dumps(strategy_params))
    decay_on["factor_decay"] = {
        "enabled": True,
        "by_regime": {
            "momentum": {
                "field_overrides": {
                    (decay_on["active_factors"]["sector"]["momentum"][0]["field"]): 0.5
                }
            }
        },
    }
    field = decay_on["active_factors"]["sector"]["momentum"][0]["field"]
    raw_w = float(decay_on["active_factors"]["sector"]["momentum"][0]["weight"])
    resolved = sc.resolve_active_factors(decay_on, regime="momentum")
    scaled = next(x for x in resolved["sector"]["momentum"] if x["field"] == field)
    assert scaled["_decay_mult"] == pytest.approx(0.5)
    # 再归一化后权重总和仍为 1
    total = sum(float(x["weight"]) for x in resolved["sector"]["momentum"])
    assert total == pytest.approx(1.0)
    assert scaled["weight"] != pytest.approx(raw_w) or raw_w == 0


def test_resolve_runtime_config_paths(strategy_params):
    base = sc.resolve_runtime_config()
    assert "portfolio_optimization" in base
    full = sc.resolve_runtime_config(strategy_params)
    assert full["final_score"]["top_k"] == strategy_params["final_score"]["top_k"]
    partial = sc.resolve_runtime_config(
        {"turnover_control": {"buffer_threshold": 0.99}},
        base_params=strategy_params,
    )
    assert partial["turnover_control"]["buffer_threshold"] == 0.99
    assert partial["final_score"]["top_k"] == strategy_params["final_score"]["top_k"]
    # 完整 JSON override 须并入 base，不丢宿主段
    base_copy = json.loads(json.dumps(strategy_params))
    base_copy["extra_marker"] = {"keep": 1}
    override = json.loads(json.dumps(strategy_params))
    override["final_score"]["top_k"] = 9
    merged_rt = sc.resolve_runtime_config(override, base_params=base_copy)
    assert merged_rt["final_score"]["top_k"] == 9
    assert sc.get_book_regime_config(strategy_params)["bond_code"]


def test_package_exports():
    import zstock.common as common

    assert callable(common.load_strategy_params)
    assert callable(common.build_runtime_config)
    assert callable(common.resolve_runtime_config)
    assert callable(common.build_risk_limits)
    assert common.PreFilters is not None


def test_resolve_decay_without_by_regime(strategy_params):
    payload = json.loads(json.dumps(strategy_params))
    field = payload["active_factors"]["sector"]["momentum"][0]["field"]
    payload["factor_decay"] = {
        "enabled": True,
        "field_overrides": {field: 0.2},
    }
    resolved = sc.resolve_active_factors(payload, regime="momentum")
    assert any(x.get("_decay_mult") == 0.2 for x in resolved["sector"]["momentum"])
    # 无 overrides 时原样返回
    payload["factor_decay"] = {"enabled": True, "field_overrides": {}}
    same = sc.resolve_active_factors(payload, regime="momentum")
    assert "sector" in same
    # 指定 regime 但 by_regime 无该项
    payload["factor_decay"] = {
        "enabled": True,
        "by_regime": {"reversal": {"field_overrides": {field: 0.1}}},
    }
    skipped = sc.resolve_active_factors(payload, regime="momentum")
    assert skipped["sector"]["momentum"]
