"""FactorRegistry / catalog 自注册：覆盖旧手写目录字段。"""

from __future__ import annotations

from web.backend.factor_catalog import catalog_by_layer, list_factor_catalog
from zstock.common.entity.factor_spec import FactorRegistry
from zstock.factor_management.registry_bootstrap import ensure_factors_loaded

# 旧 factor_catalog._CATALOG 字段快照（方案 A 迁移基线）
_LEGACY_CATALOG_FIELDS = {
    "f21_rps_10d",
    "f21_rps_20d",
    "f21_rps_60d",
    "f22_main_flow",
    "f23_limit_up_density",
    "f24_max_consecutive",
    "f25_volume_slope_3d",
    "f25_volume_slope_5d",
    "f25_volume_slope_10d",
    "f26_volume_growth_5d",
    "f26_volume_growth_20d",
    "f27_new_high_ratio",
    "f28_consistency",
    "f29_sector_breadth",
    "f30_sector_concentration",
    "f31_excess_return",
    "f31_excess_return_5d",
    "f31_excess_return_10d",
    "f31_excess_return_15d",
    "f31_excess_return_20d",
    "f31b_rps_percentile",
    "f32_amount",
    "f33_consecutive_boards",
    "f34_resonance_pct",
    "f34_resonance_pct_3d",
    "f34_resonance_pct_5d",
    "f34_resonance_pct_10d",
    "f35_bollinger_trend",
    "f36_identity_premium",
    "f37_relative_strength",
    "f38_turnover_anomaly",
    "f39_pb",
    "f40_holder_change",
    "fcoop1_main_net_ratio",
    "fcoop2_main_retail_ratio",
    "fcoop3_sustained_days",
    "fcoop3_sustained_days_3d",
    "fcoop3_sustained_days_5d",
    "fcoop3_sustained_days_10d",
    "fcoop4_turnover_quality",
    "fcoop5_main_flow_acceleration",
    "fcoop6_main_force_aggression",
    "fcoop7_super_large_net_ratio",
    "fcoop8_main_flow_trend_5d",
    "f_mean_reversion_signal",
}


def test_registry_loads_and_covers_legacy_catalog():
    ensure_factors_loaded()
    assert FactorRegistry.all_specs()
    rows = list_factor_catalog(catalog_only=True)
    fields = {r["field"] for r in rows}
    missing = _LEGACY_CATALOG_FIELDS - fields
    assert not missing, f"目录缺失字段: {sorted(missing)}"
    for r in rows:
        assert r["layer"] in {"sector", "dragon", "force", "market"}
        assert "name_cn" in r and "name_en" in r
        assert "definition" in r
        assert "inputs" in r and "outputs" in r
        assert r["default_polarity"] in {"positive", "negative"}


def test_catalog_by_layer_keys():
    by_layer = catalog_by_layer(catalog_only=True)
    assert set(by_layer) >= {"sector", "dragon", "force"}
    assert by_layer["sector"]
    assert by_layer["dragon"]
    assert by_layer["force"]


def test_eval_defaults_from_registry():
    from zstock.factor_management.script.factor_evaluation import (
        DEFAULT_SECTOR_FACTORS,
        DEFAULT_STOCK_FACTORS,
        _refresh_eval_factor_defaults,
    )

    _refresh_eval_factor_defaults()
    assert "f21_rps_20d" in DEFAULT_SECTOR_FACTORS
    assert "f31_excess_return" in DEFAULT_STOCK_FACTORS["dragon"]
    assert "fcoop1_main_net_ratio" in DEFAULT_STOCK_FACTORS["force"]
    assert "f35_bollinger_pass" in DEFAULT_STOCK_FACTORS["dragon"]
    assert "dragon_consistency_5d" in DEFAULT_STOCK_FACTORS["force"]
