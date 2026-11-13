"""因子目录（Web / API 勾选用）。字段与预计算落库、active_factors 对齐。"""
from __future__ import annotations

from typing import Any, Dict, List


def list_factor_catalog() -> List[Dict[str, Any]]:
    """返回全量可选因子元数据。"""
    return [dict(item) for item in _CATALOG]


def catalog_by_layer() -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {"sector": [], "dragon": [], "force": []}
    for item in _CATALOG:
        layer = item["layer"]
        if layer in out:
            out[layer].append(dict(item))
    return out


_CATALOG: List[Dict[str, Any]] = [
    # ── sector ──
    {"layer": "sector", "field": "f21_rps_10d", "name_cn": "板块RPS(10日)", "default_polarity": "negative", "regimes": ["momentum", "reversal"]},
    {"layer": "sector", "field": "f21_rps_20d", "name_cn": "板块RPS(20日)", "default_polarity": "negative", "regimes": ["momentum", "reversal"]},
    {"layer": "sector", "field": "f21_rps_60d", "name_cn": "板块RPS(60日)", "default_polarity": "negative", "regimes": ["momentum", "reversal"]},
    {"layer": "sector", "field": "f22_main_flow", "name_cn": "板块主力净流入", "default_polarity": "positive", "regimes": ["momentum", "reversal"]},
    {"layer": "sector", "field": "f23_limit_up_density", "name_cn": "涨停密度", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "sector", "field": "f24_max_consecutive", "name_cn": "最大连板高度", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "sector", "field": "f25_volume_slope_3d", "name_cn": "量能斜率(3日)", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "sector", "field": "f25_volume_slope_5d", "name_cn": "量能斜率(5日)", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "sector", "field": "f25_volume_slope_10d", "name_cn": "量能斜率(10日)", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "sector", "field": "f26_volume_growth_5d", "name_cn": "量能增长(5日)", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "sector", "field": "f26_volume_growth_20d", "name_cn": "量能增长(20日)", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "sector", "field": "f27_new_high_ratio", "name_cn": "新高占比", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "sector", "field": "f28_consistency", "name_cn": "板块一致性", "default_polarity": "negative", "regimes": ["momentum", "reversal"]},
    {"layer": "sector", "field": "f29_sector_breadth", "name_cn": "板块宽度", "default_polarity": "positive", "regimes": ["momentum", "reversal"]},
    {"layer": "sector", "field": "f30_sector_concentration", "name_cn": "板块集中度", "default_polarity": "negative", "regimes": ["momentum", "reversal"]},
    # ── dragon ──
    {"layer": "dragon", "field": "f31_excess_return", "name_cn": "超额收益", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "dragon", "field": "f31_excess_return_5d", "name_cn": "超额收益(5日)", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "dragon", "field": "f31_excess_return_10d", "name_cn": "超额收益(10日)", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "dragon", "field": "f31_excess_return_15d", "name_cn": "超额收益(15日)", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "dragon", "field": "f31_excess_return_20d", "name_cn": "超额收益(20日)", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "dragon", "field": "f31b_rps_percentile", "name_cn": "个股RPS分位", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "dragon", "field": "f32_amount", "name_cn": "成交额", "default_polarity": "positive", "regimes": ["momentum", "reversal"]},
    {"layer": "dragon", "field": "f33_consecutive_boards", "name_cn": "连板天数", "default_polarity": "negative", "regimes": ["momentum", "reversal"]},
    {"layer": "dragon", "field": "f34_resonance_pct", "name_cn": "共振涨幅", "default_polarity": "negative", "regimes": ["momentum", "reversal"]},
    {"layer": "dragon", "field": "f34_resonance_pct_3d", "name_cn": "共振涨幅(3日)", "default_polarity": "negative", "regimes": ["momentum", "reversal"]},
    {"layer": "dragon", "field": "f34_resonance_pct_5d", "name_cn": "共振涨幅(5日)", "default_polarity": "negative", "regimes": ["momentum", "reversal"]},
    {"layer": "dragon", "field": "f34_resonance_pct_10d", "name_cn": "共振涨幅(10日)", "default_polarity": "negative", "regimes": ["momentum", "reversal"]},
    {"layer": "dragon", "field": "f35_bollinger_trend", "name_cn": "布林趋势", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "dragon", "field": "f36_identity_premium", "name_cn": "身份溢价", "default_polarity": "negative", "regimes": ["momentum", "reversal"]},
    {"layer": "dragon", "field": "f37_relative_strength", "name_cn": "相对强度", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "dragon", "field": "f38_turnover_anomaly", "name_cn": "换手异动", "default_polarity": "negative", "regimes": ["momentum", "reversal"]},
    {"layer": "dragon", "field": "f39_pb", "name_cn": "市净率PB", "default_polarity": "negative", "regimes": ["momentum", "reversal"]},
    {"layer": "dragon", "field": "f40_holder_change", "name_cn": "股东户数变化", "default_polarity": "negative", "regimes": ["momentum", "reversal"]},
    # ── force ──
    {"layer": "force", "field": "fcoop1_main_net_ratio", "name_cn": "主力净流入占比", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "force", "field": "fcoop2_main_retail_ratio", "name_cn": "主力/散户比", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "force", "field": "fcoop3_sustained_days", "name_cn": "主力持续天数", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "force", "field": "fcoop3_sustained_days_3d", "name_cn": "主力持续(3日窗)", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "force", "field": "fcoop3_sustained_days_5d", "name_cn": "主力持续(5日窗)", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "force", "field": "fcoop3_sustained_days_10d", "name_cn": "主力持续(10日窗)", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "force", "field": "fcoop4_turnover_quality", "name_cn": "换手质量", "default_polarity": "negative", "regimes": ["reversal"]},
    {"layer": "force", "field": "fcoop5_main_flow_acceleration", "name_cn": "主力加速", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "force", "field": "fcoop6_main_force_aggression", "name_cn": "主力进攻性", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "force", "field": "fcoop7_super_large_net_ratio", "name_cn": "超大单净占比", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "force", "field": "fcoop8_main_flow_trend_5d", "name_cn": "主力趋势(5日)", "default_polarity": "positive", "regimes": ["momentum"]},
    {"layer": "force", "field": "f_mean_reversion_signal", "name_cn": "均值回归信号", "default_polarity": "negative", "regimes": ["reversal"]},
]
