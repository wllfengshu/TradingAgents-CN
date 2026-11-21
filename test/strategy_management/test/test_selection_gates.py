"""selection：M4 outflow/inflow 门禁与异常值。"""

from __future__ import annotations

import pytest

from zstock.strategy_management.strategy_section.selection import (
    _passes_m4_gate,
    score_and_compose_final,
)


def test_outflow_uses_negative_threshold():
    # threshold=0.02 → outflow 要求 fcoop1 <= -0.02
    doc_pass = {"fcoop1_main_net_ratio": -0.05, "fcoop3_sustained_days": 3}
    doc_fail_mild = {"fcoop1_main_net_ratio": -0.01, "fcoop3_sustained_days": 3}
    doc_fail_inflow = {"fcoop1_main_net_ratio": 0.05, "fcoop3_sustained_days": 3}
    assert _passes_m4_gate(doc_pass, 0.02, "outflow", min_sustained_days=2)
    assert not _passes_m4_gate(doc_fail_mild, 0.02, "outflow", min_sustained_days=2)
    assert not _passes_m4_gate(doc_fail_inflow, 0.02, "invert", min_sustained_days=2)
    assert _passes_m4_gate(doc_fail_inflow, 0.02, "off")


def test_inflow_and_bad_values():
    good = {"fcoop1_main_net_ratio": 0.05, "fcoop3_sustained_days": 2}
    assert _passes_m4_gate(good, 0.03, "inflow", min_sustained_days=1)
    assert not _passes_m4_gate(
        {"fcoop1_main_net_ratio": "bad", "fcoop3_sustained_days": 2},
        0.01,
        "inflow",
    )
    assert not _passes_m4_gate(
        {"fcoop1_main_net_ratio": 0.05, "fcoop3_sustained_days": None},
        0.01,
        "inflow",
        min_sustained_days=1,
    )
    assert not _passes_m4_gate(
        {"fcoop1_main_net_ratio": "x", "fcoop3_sustained_days": 2},
        0.0,
        "outflow",
    )


def test_compose_final_force_raw_and_missing_active():
    cands = [
        {"code": "600000", "sector_code": "S1", "dragon_score": 80.0},
        {"code": "600001", "sector_code": "S1", "dragon_score": 60.0},
        {"code": None, "sector_code": "S1", "dragon_score": 10.0},
    ]
    force_raw = [
        {
            "code": "600000",
            "fcoop1_main_net_ratio": 0.05,
            "fcoop3_sustained_days": 3,
            "fcoop1_main_net_ratio": 0.05,
        },
        {
            "code": "600001",
            "fcoop1_main_net_ratio": -0.01,
            "fcoop3_sustained_days": 1,
        },
    ]
    # duplicate key in first dict is fine; ensure field present
    force_raw[0]["fcoop1_main_net_ratio"] = 0.05
    ranked = score_and_compose_final(
        candidates=cands,
        top_sectors=[("S1", 1.0)],
        m5_weights={"sector": 0.4, "dragon": 0.35, "cooperative": 0.25},
        style_info={"regime": "momentum"},
        active_factors=None,  # 合力分退化 50
        force_raw=force_raw,
        m4_threshold=0.0,
        gate_mode="inflow",
        min_sustained_days=0,
    )
    assert len(ranked) == 1  # 600001 fails inflow if threshold>ratio? -0.01 < 0 with gate?
    # threshold 0.0: -0.01 < 0 fails inflow
    assert ranked[0]["code"] == "600000"
    assert ranked[0]["force_composite_score"] == pytest.approx(50.0)
