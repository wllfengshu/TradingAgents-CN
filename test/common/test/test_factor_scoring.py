"""factor_scoring：门禁与加权合成，字段名取自真实截面夹具。"""

from __future__ import annotations

from zstock.common.utils.factor_scoring import (
    filter_by_gates_columnar,
    filter_by_gates_rowwise,
    weighted_minmax_combine,
)


def test_weighted_minmax_combine_polarity_and_renorm(signal_names_raw):
    dragons = signal_names_raw.get("dragons") or {}
    # 取夹具里真实出现的字段；若结构是 list/dict 做兼容
    sample_fields = []
    if isinstance(dragons, dict):
        for code, row in list(dragons.items())[:3]:
            if isinstance(row, dict):
                sample_fields = [k for k in row if k.startswith("f")]
                break
    if len(sample_fields) < 1:
        # 退到固定真实字段名（来自 SSOT / 因子目录）
        sample_fields = ["f31_excess_return_5d", "fcoop1_main_net_ratio"]

    field_a = sample_fields[0]
    field_b = sample_fields[1] if len(sample_fields) > 1 else sample_fields[0]
    values_by_field = {
        field_a: {"603201": 1.0, "000060": 3.0, "601107": 2.0},
        field_b: {"603201": 10.0, "000060": 20.0},
    }
    configs = [
        {"field": field_a, "weight": 0.6, "polarity": "positive"},
        {"field": field_b, "weight": 0.4, "polarity": "negative"},
    ]
    out = weighted_minmax_combine(values_by_field, configs)
    assert "603201" in out and "000060" in out
    assert 0.0 <= out["603201"] <= 100.0
    assert weighted_minmax_combine({}, []) == {}
    assert weighted_minmax_combine(values_by_field, [{"weight": 1.0}]) == {}


def test_filter_by_gates_columnar_branches():
    raw = {
        "fcoop1_main_net_ratio": {"603201": 0.05, "000060": -0.01, "601107": 0.02},
    }
    assert filter_by_gates_columnar(raw, None) == {"603201", "000060", "601107"}
    assert filter_by_gates_columnar(raw, []) == {"603201", "000060", "601107"}

    def _none_cb(_raw, universe):
        return {"603201"}

    assert filter_by_gates_columnar(raw, None, when_gates_none=_none_cb) == {"603201"}

    gates = [{"field": "fcoop1_main_net_ratio", "op": "gt", "value": 0}]
    passed = filter_by_gates_columnar(raw, gates)
    assert passed == {"603201", "601107"}
    assert filter_by_gates_columnar({}, gates) == set()


def test_filter_by_gates_rowwise_branches():
    rows = {
        "603201": {"fcoop1_main_net_ratio": 0.05},
        "000060": {"fcoop1_main_net_ratio": -0.01},
    }
    assert filter_by_gates_rowwise(rows, None) == {"603201", "000060"}
    assert filter_by_gates_rowwise(rows, []) == {"603201", "000060"}
    assert filter_by_gates_rowwise({}, []) == set()

    def _cb(r):
        return {"000060"}

    assert filter_by_gates_rowwise(rows, None, when_gates_none=_cb) == {"000060"}
    gates = [{"field": "fcoop1_main_net_ratio", "op": "gt", "value": 0}]
    assert filter_by_gates_rowwise(rows, gates) == {"603201"}
