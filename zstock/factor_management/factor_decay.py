"""
因子衰减：按 IC 测评等级 / 字段 override 动态缩放 active_factors 权重。

配置见 strategy_params.json → factor_decay
"""

from __future__ import annotations

import copy
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

_GRADE_RE = re.compile(r"^([A-D])")


def parse_grade(grade_str: str) -> str:
    if not grade_str:
        return "C"
    m = _GRADE_RE.match(str(grade_str).strip())
    return m.group(1) if m else "C"


def load_eval_field_multipliers(
    eval_paths: Dict[str, str],
    blend_weights: Optional[Dict[str, float]] = None,
    grade_multipliers: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """从多年 IC 测评 summary.csv 混合得到 field → multiplier。"""
    grade_multipliers = grade_multipliers or {
        "A": 1.25,
        "B": 1.0,
        "C": 0.55,
        "D": 0.15,
    }
    blend_weights = blend_weights or {}
    acc: Dict[str, List[float]] = {}
    weights_used: Dict[str, float] = {}

    for label, path in eval_paths.items():
        p = Path(path)
        if not p.exists():
            logger.warning("因子测评文件不存在: %s", p)
            continue
        try:
            df = pd.read_csv(p)
        except Exception as e:
            logger.warning("读取测评失败 %s: %s", p, e)
            continue
        w = float(blend_weights.get(label, 1.0))
        weights_used[label] = w
        for _, row in df.iterrows():
            field = str(row.get("field", ""))
            if not field:
                continue
            g = parse_grade(str(row.get("Grade", "C")))
            mult = float(grade_multipliers.get(g, 1.0))
            acc.setdefault(field, []).append(mult * w)

    if not acc:
        return {}

    total_w = sum(weights_used.values()) or 1.0
    out: Dict[str, float] = {}
    for field, vals in acc.items():
        out[field] = sum(vals) / total_w
    return out


def apply_factor_decay(
    active_factors: Dict[str, Any],
    decay_cfg: Dict[str, Any],
    regime: Optional[str] = None,
) -> Dict[str, Any]:
    """返回缩放后的 active_factors 深拷贝（各 regime 内权重再归一化）。

    若 decay_cfg.by_regime[regime] 存在，仅对该 regime 的因子列表应用 field_overrides。
    """
    if not decay_cfg or not decay_cfg.get("enabled"):
        return active_factors

    by_regime = decay_cfg.get("by_regime") or {}
    if by_regime:
        regime_cfg = by_regime.get(regime or "")
        if not regime_cfg:
            return active_factors
        sub = {
            "field_overrides": regime_cfg.get("field_overrides") or {},
            "eval_paths": decay_cfg.get("eval_paths"),
            "eval_blend_weights": decay_cfg.get("eval_blend_weights"),
            "eval_blend_ratio": decay_cfg.get("eval_blend_ratio"),
            "grade_multipliers": decay_cfg.get("grade_multipliers"),
        }
        return _apply_decay_to_regime(active_factors, sub, target_regime=regime)

    return _apply_decay_to_regime(active_factors, decay_cfg, target_regime=None)


def _apply_decay_to_regime(
    active_factors: Dict[str, Any],
    decay_cfg: Dict[str, Any],
    target_regime: Optional[str],
) -> Dict[str, Any]:
    out = copy.deepcopy(active_factors)
    field_mult = dict(decay_cfg.get("field_overrides") or {})

    eval_paths = decay_cfg.get("eval_paths") or {}
    if eval_paths:
        auto = load_eval_field_multipliers(
            eval_paths,
            blend_weights=decay_cfg.get("eval_blend_weights"),
            grade_multipliers=decay_cfg.get("grade_multipliers"),
        )
        blend = float(decay_cfg.get("eval_blend_ratio", 0.6))
        for f, m in auto.items():
            if f in field_mult:
                field_mult[f] = field_mult[f] * (1 - blend) + m * blend
            else:
                field_mult[f] = 1.0 * (1 - blend) + m * blend

    if not field_mult:
        return active_factors if target_regime else out

    for layer in ("sector", "dragon", "force"):
        layer_cfg = out.get(layer)
        if not isinstance(layer_cfg, dict):
            continue
        regimes = [target_regime] if target_regime else list(layer_cfg.keys())
        for regime in regimes:
            factors = layer_cfg.get(regime)
            if not isinstance(factors, list):
                continue
            scaled: List[Dict[str, Any]] = []
            for fc in factors:
                fc = dict(fc)
                field = fc.get("field", "")
                base_w = float(fc.get("weight", 0))
                mult = float(field_mult.get(field, 1.0))
                fc["weight"] = base_w * mult
                fc["_decay_mult"] = mult
                scaled.append(fc)
            total = sum(float(x.get("weight", 0)) for x in scaled)
            if total > 0:
                for fc in scaled:
                    fc["weight"] = float(fc["weight"]) / total
            layer_cfg[regime] = scaled

    logger.debug("因子衰减已应用: %d 个字段 multiplier", len(field_mult))
    return out
