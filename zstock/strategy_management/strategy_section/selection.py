"""截面选股：板块 / 龙头 / 合力门禁与最终合成。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from zstock.common.utils.common_utils import to_float_or_none
from zstock.common.utils.factor_scoring import (
    filter_by_gates_columnar,
    filter_by_gates_rowwise,
    weighted_minmax_combine,
)

logger = logging.getLogger(__name__)

# 配置 field 名 ↔ 预计算落库名
_SECTOR_FIELD_ALIASES = {
    "f21_rps_20d": "f21_rps",
    "f25_volume_slope_5d": "f25_volume_slope",
    "f26_volume_growth_5d": "f26_volume_growth",
}

# Top N 并列时的默认 tie-break 字段（可后续改成配置）
_DEFAULT_TIEBREAK_FIELDS = ("f26_volume_growth_5d", "f26_volume_growth")

# ----- 板块 -----


def select_top_sectors(
    sector_scores: Dict[str, float],
    tiebreaker: Optional[Dict[str, float]] = None,
    top_n: int = 3,
) -> Dict[str, float]:
    """按分数取 Top N；并列时用 tiebreaker 降序。"""
    if not sector_scores:
        return {}
    tb = tiebreaker or {}
    sorted_sectors = sorted(
        sector_scores.items(),
        key=lambda x: (-x[1], -tb.get(x[0], 0.0)),
    )
    return {code: score for code, score in sorted_sectors[:top_n]}


def score_sectors_from_raw(
    raw: Dict[str, Dict],
    regime: str = "neutral",
    active_factors: dict = None,
    top_n: int = 3,
    gates: Optional[list] = None,
) -> Dict[str, float]:
    """板块 raw → Top N 板块分。"""
    if not raw:
        return {}

    tiebreak: Dict[str, float] = {}
    for field in _DEFAULT_TIEBREAK_FIELDS:
        if raw.get(field):
            tiebreak = raw[field]  # type: ignore[assignment]
            break

    def _default_f23(raw_map: Dict[str, Dict[str, Any]], universe: Set[str]) -> Set[str]:
        f23 = raw_map.get("f23_limit_up_density") or {}
        out: Set[str] = set()
        for sc in universe:
            if f23.get(sc, 0.0) > 0:
                out.add(sc)
        if not out:
            for sc, val in f23.items():
                if val > 0:
                    out.add(sc)
        return out

    universe: Set[str] = set()
    for field_map in (raw or {}).values():
        if isinstance(field_map, dict):
            universe.update(field_map.keys())
    primary = raw.get("f21_rps_20d") or raw.get("f21_rps") or {}
    if primary:
        universe.update(primary.keys())

    candidate_sectors = filter_by_gates_columnar(
        raw,
        gates,
        universe=universe,
        when_gates_none=_default_f23,
    )
    if not candidate_sectors:
        logger.warning("板块门禁：无符合条件板块")
        return {}

    if not active_factors:
        logger.warning("板块打分未收到 active_factors 配置，返回空")
        return {}
    sector_cfg = active_factors.get("sector", {})
    factor_configs = sector_cfg.get(regime, sector_cfg.get("momentum", []))
    if not factor_configs:
        return {}

    values_by_field: Dict[str, Dict[str, float]] = {}
    for fc in factor_configs:
        field = fc.get("field")
        if not field:
            continue
        values = raw.get(field) or {}
        if not values:
            alias = _SECTOR_FIELD_ALIASES.get(field)
            if alias:
                values = raw.get(alias) or {}
        filtered = {
            k: v
            for k, v in values.items()
            if k in candidate_sectors and v is not None
        }
        if filtered:
            values_by_field[field] = filtered

    scores = weighted_minmax_combine(values_by_field, factor_configs)
    if not scores:
        logger.warning("板块合成：无法生成评分")
        return {}

    return select_top_sectors(scores, tiebreak, top_n=top_n)


# ----- 龙头 -----


def score_dragons_from_raw(
    raw: Dict[str, Dict[str, Any]],
    regime: str = "neutral",
    active_factors: dict = None,
    gates: Optional[list] = None,
) -> Dict[str, float]:
    """个股龙头 raw（按 code 行）→ 龙头综合分。"""
    if not raw:
        return {}

    if not active_factors:
        logger.warning("龙头打分未收到 active_factors 配置，返回空")
        return {}

    dragon_cfg = active_factors.get("dragon", {})
    factor_configs = dragon_cfg.get(regime, dragon_cfg.get("momentum", []))
    if not factor_configs:
        return {}

    def _default_f35(rows: Dict[str, Dict[str, Any]]) -> Set[str]:
        out: Set[str] = set()
        for code, row in rows.items():
            f35 = to_float_or_none((row or {}).get("f35_bollinger_trend"))
            if f35 is not None:
                out.add(code)
        return out

    allowed_codes = filter_by_gates_rowwise(
        raw, gates, when_gates_none=_default_f35
    )

    config_fields = [fc["field"] for fc in factor_configs]
    field_values: Dict[str, Dict[str, float]] = {f: {} for f in config_fields}
    valid_stocks: Set[str] = set()

    for c, r in raw.items():
        if c not in allowed_codes:
            continue
        has_any = False
        for field in config_fields:
            val = r.get(field)
            if val is None and field == "f34_resonance_pct_5d":
                val = r.get("f34_resonance_pct")
            if val is not None:
                fval = float(val)
                if fval == fval:  # not NaN
                    field_values[field][c] = fval
                    has_any = True
        if has_any:
            valid_stocks.add(c)

    if not valid_stocks:
        return {}

    return weighted_minmax_combine(field_values, factor_configs)


# ----- 合力门禁 + 最终合成 -----


def resolve_force_gate_settings(
    coop_cfg: Dict[str, Any],
    regime: str,
    grade: str,
) -> Tuple[float, str, int]:
    """cooperative_force 全局 + by_regime + gate_mode_by_grade/context。

    Returns:
        (threshold_pct, gate_mode, min_sustained_days)
    """
    cfg = coop_cfg or {}
    threshold = float(cfg.get("threshold_pct", 0.01))
    gate_mode = str(cfg.get("gate_mode") or "inflow").strip().lower()
    min_sustained = int(cfg.get("min_sustained_days", 1) or 0)
    by_regime = cfg.get("by_regime") or {}
    sub = by_regime.get(regime) or by_regime.get(str(regime))
    if isinstance(sub, dict):
        if sub.get("threshold_pct") is not None:
            threshold = float(sub["threshold_pct"])
        if sub.get("gate_mode"):
            gate_mode = str(sub["gate_mode"]).strip().lower()
        if sub.get("min_sustained_days") is not None:
            min_sustained = int(sub["min_sustained_days"])
    if bool(cfg.get("skip_filter", False)):
        gate_mode = "off"
    # grade 粗粒度覆盖 → context(regime_grade) 更细，后者优先
    by_grade = cfg.get("gate_mode_by_grade") or {}
    if isinstance(by_grade, dict) and by_grade.get(grade):
        gate_mode = str(by_grade.get(grade)).strip().lower()
    by_ctx = cfg.get("gate_mode_by_context") or {}
    ctx_key = f"{regime}_{grade}"
    if isinstance(by_ctx, dict) and by_ctx.get(ctx_key):
        gate_mode = str(by_ctx.get(ctx_key)).strip().lower()
    return threshold, gate_mode, max(0, min_sustained)


def _passes_m4_inflow_gate(
    doc: Dict[str, Any],
    threshold_pct: float,
    min_sustained_days: int = 1,
) -> bool:
    try:
        fcoop1 = float(doc.get("fcoop1_main_net_ratio", 0.0) or 0.0)
    except (TypeError, ValueError):
        return False
    if fcoop1 < float(threshold_pct):
        return False
    raw = doc.get("fcoop3_sustained_days")
    try:
        sustained = float(raw) if raw is not None else float("nan")
    except (TypeError, ValueError):
        return False
    return bool(np.isfinite(sustained) and sustained >= float(min_sustained_days))


def _passes_m4_outflow_gate(
    doc: Dict[str, Any],
    max_ratio: float = 0.0,
    min_sustained_days: int = 1,
) -> bool:
    try:
        fcoop1 = float(doc.get("fcoop1_main_net_ratio", 0.0) or 0.0)
    except (TypeError, ValueError):
        return False
    if fcoop1 > float(max_ratio):
        return False
    raw = doc.get("fcoop3_sustained_days")
    try:
        sustained = float(raw) if raw is not None else float("nan")
    except (TypeError, ValueError):
        return False
    return bool(np.isfinite(sustained) and sustained >= float(min_sustained_days))


def _passes_m4_gate(
    doc: Dict[str, Any],
    threshold_pct: float,
    mode: str = "inflow",
    min_sustained_days: int = 1,
) -> bool:
    m = (mode or "inflow").strip().lower()
    if m in ("off", "skip", "none"):
        return True
    if m in ("outflow", "invert"):
        # 与 inflow 对称：要求净流出至少 threshold_pct（fcoop1 <= -threshold）
        return _passes_m4_outflow_gate(
            doc, -float(threshold_pct), min_sustained_days=min_sustained_days
        )
    return _passes_m4_inflow_gate(
        doc, threshold_pct, min_sustained_days=min_sustained_days
    )


def _force_factor_entries(
    style_info: Optional[Dict[str, object]] = None,
    active_factors: dict = None,
) -> List[Dict]:
    if active_factors and "force" in active_factors:
        force_cfg = active_factors["force"]
        regime = (style_info or {}).get("regime", "neutral")
        config_list = force_cfg.get(regime) or force_cfg.get("momentum", [])
        return [
            {
                "field": fc["field"],
                "weight": float(fc["weight"]),
                "polarity": fc.get("polarity", "positive"),
            }
            for fc in config_list
        ]
    logger.warning(
        "合力评分未收到 active_factors.force 配置，返回空（合力分退化为中性 50）"
    )
    return []


def _normalize_sector_ranks(top_sectors: List[Tuple[str, float]]) -> Dict[str, float]:
    n = len(top_sectors)
    if n <= 1:
        return {code: 100.0 for code, _ in top_sectors}
    return {
        code: 100.0 * (1.0 - idx / (n - 1))
        for idx, (code, _) in enumerate(top_sectors)
    }


def score_and_compose_final(
    candidates: List[Dict],
    top_sectors: List[Tuple[str, float]],
    m5_weights: Optional[Dict[str, float]] = None,
    style_info: Optional[Dict[str, object]] = None,
    active_factors: Optional[Dict] = None,
    force_docs_map: Optional[Dict[str, Dict]] = None,
    force_raw: Optional[List[Dict]] = None,
    m4_threshold: float = 0.03,
    gate_mode: str = "inflow",
    min_sustained_days: int = 1,
) -> List[Dict]:
    """合力门禁过滤 + 最终合成，按 final_score 降序。"""
    if force_docs_map is None and force_raw:
        force_docs_map = {r["code"]: r for r in force_raw if r.get("code")}

    filtered: List[Dict] = []
    missing_force = 0
    for c in candidates:
        code = c.get("code")
        if not code:
            continue
        # 预计算 map 缺票：保留候选并跳过门禁（避免静默系统性少票）；有文档才过 M4。
        if force_docs_map is not None:
            if code not in force_docs_map:
                missing_force += 1
                filtered.append(dict(c))
                continue
            fd = force_docs_map[code]
        else:
            fd = c
        if not _passes_m4_gate(
            fd,
            m4_threshold,
            gate_mode,
            min_sustained_days=min_sustained_days,
        ):
            continue
        filtered.append({**c, **fd} if fd is not c else dict(c))
    if missing_force:
        logger.warning(
            "M4 预计算缺文档 %d/%d 只候选（已保留并跳过门禁；请补齐 force 预计算）",
            missing_force,
            len(candidates),
        )

    if not filtered:
        return []

    factor_entries = _force_factor_entries(style_info, active_factors=active_factors)
    values_by_field: Dict[str, Dict[str, float]] = {}
    for fc in factor_entries:
        field = fc["field"]
        raw_vals: Dict[str, float] = {}
        for c in filtered:
            if field not in c or c[field] is None:
                continue
            try:
                val = float(c[field])
            except (TypeError, ValueError):
                continue
            if np.isfinite(val):
                raw_vals[c["code"]] = val
        if raw_vals:
            values_by_field[field] = raw_vals
    composite_scores = weighted_minmax_combine(values_by_field, factor_entries)

    weights = m5_weights or {}
    w_sector = float(weights.get("sector", 0.40))
    w_dragon = float(weights.get("dragon", 0.35))
    w_coop = float(weights.get("cooperative", 0.25))
    total_w = max(w_sector + w_dragon + w_coop, 1e-8)
    sector_rank_map = _normalize_sector_ranks(top_sectors)

    for c in filtered:
        coop_score = composite_scores.get(c["code"], 50.0)
        lhb_bonus = float(c.get("longhu_board_bonus", 0.0) or 0.0)
        coop_score = min(coop_score + lhb_bonus, 100.0)
        c["force_composite_score"] = coop_score
        c["longhu_board_bonus"] = lhb_bonus
        c["final_score"] = (
            w_sector * sector_rank_map.get(c.get("sector_code", ""), 0.0)
            + w_dragon * float(c.get("dragon_score", 0.0) or 0.0)
            + w_coop * coop_score
        ) / total_w

    return sorted(filtered, key=lambda x: x.get("final_score", 0), reverse=True)
