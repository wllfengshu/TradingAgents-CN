"""
策略配置单一访问层（Single Source of Truth）。

所有策略参数都从 zstock/common/config/strategy_params.json 读取，各层禁止自行写死。

对外提供：
- load_strategy_params()         读并缓存原始配置 JSON；可传入备用文件路径
- get_market_factors_config()    派生 M1 情绪权重/红绿灯/仓位缩放
- get_style_detector_config()    派生 M1 风格检测阈值
- get_neutral_style()            中性风格（动量/反转对半，定义而非交易参数）
- resolve_active_factors()       当日因子画像（profiles + factor_decay）
- build_runtime_config()         派生各阶段运行态配置（含弱段保护/自适应/仓位 overlay）
- build_risk_limits()            派生风控 limits
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_STRATEGY_PARAMS_PATH = Path(__file__).resolve().parent / "strategy_params.json"
_params_cache: Optional[Dict[str, Any]] = None
_market_factors_cfg_cache: Optional[Dict[str, Any]] = None
_style_detector_cfg_cache: Optional[Dict[str, Any]] = None


def _clear_cache() -> None:
    """测试夹具用：清空缓存，强制下次重新读盘。"""
    global _params_cache, _market_factors_cfg_cache, _style_detector_cfg_cache
    _params_cache = None
    _market_factors_cfg_cache = None
    _style_detector_cfg_cache = None


def load_strategy_params(path: Optional[str | Path] = None) -> Dict[str, Any]:
    """读策略参数 JSON。

    path 为 None：读生产 SSOT（进程内缓存）。
    path 有值：读指定文件，不写入缓存（网格搜索 / 消融实验）。
    """
    if path is not None:
        p = Path(path)
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

    global _params_cache
    if _params_cache is not None:
        return _params_cache

    try:
        with open(_STRATEGY_PARAMS_PATH, "r", encoding="utf-8") as f:
            _params_cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("❌ 加载策略参数失败: %s, %s", _STRATEGY_PARAMS_PATH, e)
        _params_cache = {}
    return _params_cache


def get_market_factors_config(params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """派生 M1 情绪权重、红绿灯阈值与仓位缩放（进程内缓存）。"""
    global _market_factors_cfg_cache
    if params is None and _market_factors_cfg_cache is not None:
        return _market_factors_cfg_cache

    src = params if params is not None else load_strategy_params()
    cfg = (src or {}).get("market_factors") or {}
    weights = cfg.get("weights") or {}
    out = {
        "w_trend": float(weights.get("trend_strength", 0.30)),
        "w_bollinger": float(weights.get("bollinger_position", 0.25)),
        "w_volume": float(weights.get("volume_state", 0.20)),
        "w_momentum": float(weights.get("momentum_5day", 0.15)),
        "w_volatility": float(weights.get("volatility_suppression", 0.10)),
        "grade_green": float(cfg.get("grade_green", 70.0)),
        "grade_yellow": float(cfg.get("grade_yellow", 40.0)),
        "scale_green": float(cfg.get("scale_green", 1.0)),
        "scale_yellow": float(cfg.get("scale_yellow", 0.4)),
        "scale_red": float(cfg.get("scale_red", 0.0)),
    }
    if params is None:
        _market_factors_cfg_cache = out
    return out


def get_style_detector_config(params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """派生 M1 风格检测阈值（进程内缓存）。"""
    global _style_detector_cfg_cache
    if params is None and _style_detector_cfg_cache is not None:
        return _style_detector_cfg_cache

    src = params if params is not None else load_strategy_params()
    cfg = (src or {}).get("style_detector") or {}
    out = {
        "momentum_threshold": float(cfg.get("momentum_threshold", 0.05)),
        "reversal_threshold": float(cfg.get("reversal_threshold", -0.05)),
        "min_observations": int(cfg.get("min_observations", 15)),
        "lookback": int(cfg.get("lookback", 20)),
    }
    if params is None:
        _style_detector_cfg_cache = out
    return out


def get_neutral_style(
    *,
    autocorr: float = float("nan"),
    strength: float = 0.0,
) -> Dict[str, object]:
    """中性风格：动量/反转权重对半。

    这是「无风格观点」的定义（与 style_detector 区间内分类一致），
    不是可调交易参数，因此不进 strategy_params.json。
    """
    return {
        "regime": "neutral",
        "autocorr": autocorr,
        "strength": strength,
        "momentum_weight": 0.5,
        "reversal_weight": 0.5,
    }


def resolve_active_factors(
    params: Optional[Dict[str, Any]] = None,
    regime: str = "neutral",
    grade: Optional[str] = None,
) -> Dict[str, Any]:
    """当日 active_factors：先套 profiles（regime_grade），再按 factor_decay 缩放权重。"""
    src = params if params is not None else load_strategy_params()
    resolved = src.get("active_factors") or {}
    if grade and resolved:
        profiles = resolved.get("profiles")
        patch = profiles.get(f"{regime}_{grade}") if isinstance(profiles, dict) else None
        if isinstance(patch, dict):
            resolved = copy.deepcopy(resolved)
            resolved.pop("profiles", None)
            for layer, spec in patch.items():
                if layer == "profiles" or not isinstance(spec, (dict, list)):
                    continue
                if layer not in resolved or not isinstance(resolved.get(layer), dict):
                    resolved[layer] = {}
                layer_cfg = resolved[layer]
                if isinstance(spec, list):
                    layer_cfg[regime] = copy.deepcopy(spec)
                else:
                    for reg, entries in spec.items():
                        if isinstance(entries, list):
                            layer_cfg[str(reg)] = copy.deepcopy(entries)

    decay_cfg = src.get("factor_decay") or {}
    if not decay_cfg.get("enabled"):
        return resolved
    by_regime = decay_cfg.get("by_regime") or {}
    if by_regime:
        regime_cfg = by_regime.get(regime or "")
        if not regime_cfg:
            return resolved
        field_mult = dict(regime_cfg.get("field_overrides") or {})
        target_regime = regime
    else:
        field_mult = dict(decay_cfg.get("field_overrides") or {})
        target_regime = None
    if not field_mult:
        return resolved

    out = copy.deepcopy(resolved)
    for layer in ("sector", "dragon", "force"):
        layer_cfg = out.get(layer)
        if not isinstance(layer_cfg, dict):
            continue
        regimes = [target_regime] if target_regime else list(layer_cfg.keys())
        for reg in regimes:
            factors = layer_cfg.get(reg)
            if not isinstance(factors, list):
                continue
            scaled: list = []
            for fc in factors:
                fc = dict(fc)
                field = fc.get("field", "")
                mult = float(field_mult.get(field, 1.0))
                fc["weight"] = float(fc.get("weight", 0)) * mult
                fc["_decay_mult"] = mult
                scaled.append(fc)
            total = sum(float(x.get("weight", 0)) for x in scaled)
            if total > 0:
                for fc in scaled:
                    fc["weight"] = float(fc["weight"]) / total
            layer_cfg[reg] = scaled
    return out


def _copy_section(params: Dict[str, Any], key: str) -> Dict[str, Any]:
    raw = params.get(key) or {}
    return copy.deepcopy(raw) if isinstance(raw, dict) else {}


def build_runtime_config(params: Optional[Dict[str, Any]] = None) -> Dict[str, Dict]:
    """由原始 JSON 派生 pipeline / 回测 / API 共用的运行态配置。

    派生段（portfolio_optimization 等）与透传段（weak_regime_protection 等）
    必须同包返回，避免「带完整 JSON 的回测」和「只拿 runtime 的隔夜/API」行为分叉。
    """
    params = params if params is not None else load_strategy_params()

    portfolio = params.get("portfolio", {}) or {}
    po_cfg = params.get("portfolio_optimization", {}) or {}
    tov = params.get("turnover_control", {})
    exit_rules = params.get("exit_rules", {})
    backtest = params.get("backtest", {})
    top_k = int(params.get("final_score", {}).get("top_k", 5))

    return {
        "portfolio_optimization": {
            "min_holdings": int(portfolio.get("min_holdings", max(1, top_k - 2))),
            "max_holdings": int(portfolio.get("max_holdings", top_k)),
            "max_weight_per_stock": float(portfolio.get("max_weight_per_stock", 0.2)),
            "weighting": str(po_cfg.get("weighting", "score")),
            "fill_weights_to_position_scale": bool(po_cfg.get("fill_weights_to_position_scale", True)),
            "max_single_stock_cap": float(po_cfg.get("max_single_stock_cap", 0.40)),
            "single_stock_cap_by_grade": dict(po_cfg.get("single_stock_cap_by_grade") or {}),
            "single_stock_cap_by_regime": dict(po_cfg.get("single_stock_cap_by_regime") or {}),
        },
        "risk_management": {
            "hard_stop_loss_pct": float(exit_rules.get("hard_stop_loss_pct", -0.08)),
            "max_sector_exposure": float(portfolio.get("max_sector_exposure", 0.4)),
        },
        "turnover_control": {
            "buffer_threshold": float(tov.get("buffer_threshold", 0.25)),
            "min_hold_days": int(tov.get("min_hold_days", 3)),
            "fee_rate": float(backtest.get("fee_rate", 0.0015)),
            "fill_open_slots": bool(tov.get("fill_open_slots", True)),
        },
        "exit_rules": {
            "hard_stop_loss_pct": float(exit_rules.get("hard_stop_loss_pct", -0.08)),
            "rank_percentile_threshold": float(exit_rules.get("rank_percentile_threshold", 0.85)),
            "consecutive_days_out_of_candidates": int(exit_rules.get("consecutive_days_out_of_candidates", 3)),
            "flat_after_bad_days": int(exit_rules.get("flat_after_bad_days", 5)),
            "no_signal_action": str(exit_rules.get("no_signal_action", "hold")),
            "no_signal_reduce_scale": float(exit_rules.get("no_signal_reduce_scale", 0.5)),
            "trend_exit": dict(exit_rules.get("trend_exit", {})),
        },
        "final_score": _copy_section(params, "final_score"),
        "portfolio": _copy_section(params, "portfolio"),
        "market_selection": _copy_section(params, "market_selection"),
        "target_equity": _copy_section(params, "target_equity"),
        "market_overlay": _copy_section(params, "market_overlay"),
        "market_factors": _copy_section(params, "market_factors"),
        "weak_regime_protection": _copy_section(params, "weak_regime_protection"),
        "adaptive_rebalance": _copy_section(params, "adaptive_rebalance"),
    }


def build_risk_limits(params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """由原始 JSON 派生风控 limits（与 RiskManager 期望结构一致）。"""
    params = params if params is not None else load_strategy_params()

    portfolio = params.get("portfolio", {})
    top_k = int(params.get("final_score", {}).get("top_k", 5))
    max_holdings = int(portfolio.get("max_holdings", top_k))

    return {
        "top_k": top_k,
        "min_holdings": int(portfolio.get("min_holdings", max(1, top_k - 2))),
        "max_holdings": max_holdings * 2,  # 允许调仓过渡期持仓上限
        "max_weight_per_stock": float(portfolio.get("max_weight_per_stock", 0.2)),
        "max_top5_concentration": 1.0,  # top_k=5 时不做 top5 集中度额外惩罚
        "max_sector_exposure": float(portfolio.get("max_sector_exposure", 0.4)),
        "weight_sum_tolerance": 1e-3,
        "allow_cash": True,
    }
