"""因子打分通用件：门禁过滤 + min-max 极性加权合成（与具体层公式无关）。"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Set

from zstock.common.utils.common_utils import minmax_normalize, passes_gate_value


def weighted_minmax_combine(
    values_by_field: Dict[str, Dict[str, float]],
    factor_configs: List[Dict[str, Any]],
) -> Dict[str, float]:
    """配置驱动合成：逐因子 min-max → 极性反转 → 按可用因子动态再归一化加权。

    values_by_field: {field: {entity_id: raw_value}}
    factor_configs: [{"field", "weight", "polarity": "positive"|"negative"}, ...]
    """
    normalized: Dict[str, tuple] = {}
    for fc in factor_configs or []:
        field = fc.get("field")
        if not field:
            continue
        values = values_by_field.get(field) or {}
        if not values:
            continue
        norm = minmax_normalize(values)
        if fc.get("polarity") == "negative":
            norm = {k: 100.0 - v for k, v in norm.items()}
        normalized[field] = (norm, float(fc.get("weight", 0.0)))

    if not normalized:
        return {}

    entities: Set[str] = set()
    for norm, _ in normalized.values():
        entities.update(norm.keys())

    result: Dict[str, float] = {}
    for entity in entities:
        available = [
            (norm[entity], w) for norm, w in normalized.values() if entity in norm
        ]
        if not available:
            continue
        total_w = sum(w for _, w in available)
        result[entity] = (
            sum(v * (w / total_w) for v, w in available) if total_w > 0 else 50.0
        )
    return result


def filter_by_gates_columnar(
    raw: Dict[str, Dict[str, Any]],
    gates: Optional[List[Dict[str, Any]]],
    *,
    universe: Optional[Set[str]] = None,
    when_gates_none: Optional[Callable[[Dict[str, Dict[str, Any]], Set[str]], Set[str]]] = None,
) -> Set[str]:
    """门禁过滤（列式 raw：field → {entity: value}）。

    gates=None：调用 when_gates_none，若未提供则返回全集。
    gates=[]：不过滤，返回全集。
    """
    if universe is None:
        universe = set()
        for field_map in (raw or {}).values():
            if isinstance(field_map, dict):
                universe.update(field_map.keys())
    if not universe:
        return set()

    if gates is None:
        if when_gates_none is not None:
            return when_gates_none(raw, set(universe))
        return set(universe)

    if len(gates) == 0:
        return set(universe)

    passed: Set[str] = set()
    for entity in universe:
        ok = True
        for gate in gates:
            field = gate.get("field")
            if not field:
                continue
            field_map = raw.get(field) or {}
            if not passes_gate_value(field_map.get(entity), gate):
                ok = False
                break
        if ok:
            passed.add(entity)
    return passed


def filter_by_gates_rowwise(
    rows: Dict[str, Dict[str, Any]],
    gates: Optional[List[Dict[str, Any]]],
    *,
    when_gates_none: Optional[Callable[[Dict[str, Dict[str, Any]]], Set[str]]] = None,
) -> Set[str]:
    """门禁过滤（行式 raw：entity → {field: value}）。"""
    if not rows:
        return set()

    if gates is None:
        if when_gates_none is not None:
            return when_gates_none(rows)
        return set(rows.keys())

    if len(gates) == 0:
        return set(rows.keys())

    passed: Set[str] = set()
    for entity, row in rows.items():
        ok = True
        for gate in gates:
            field = gate.get("field")
            if not field:
                continue
            if not passes_gate_value((row or {}).get(field), gate):
                ok = False
                break
        if ok:
            passed.add(entity)
    return passed

