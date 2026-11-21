"""本轮策略层缺陷修复回归。"""

from __future__ import annotations

import pandas as pd
import pytest

from zstock.strategy_management.strategy_section.exit import rank_force_exit_codes
from zstock.strategy_management.strategy_section.selection import score_and_compose_final
from zstock.strategy_management.strategy_section.universe import normalize_universe_mode


def test_missing_force_doc_gate_off_keeps_candidate():
    ranked = score_and_compose_final(
        candidates=[
            {"code": "600000", "sector_code": "S", "dragon_score": 80.0},
            {"code": "600001", "sector_code": "S", "dragon_score": 70.0},
        ],
        top_sectors=[("S", 1.0)],
        force_docs_map={
            "600000": {
                "fcoop1_main_net_ratio": 0.05,
                "fcoop3_sustained_days": 3,
            }
            # 600001 故意缺失
        },
        gate_mode="off",
        m5_weights={"sector": 0.4, "dragon": 0.35, "cooperative": 0.25},
    )
    codes = {r["code"] for r in ranked}
    assert codes == {"600000", "600001"}


def test_missing_force_doc_inflow_keeps_missing_skips_gate():
    """缺文档保留；有文档但不过 inflow 门禁才剔除。"""
    ranked = score_and_compose_final(
        candidates=[
            {"code": "600000", "sector_code": "S", "dragon_score": 80.0},
            {"code": "600001", "sector_code": "S", "dragon_score": 70.0},
            {"code": "600002", "sector_code": "S", "dragon_score": 60.0},
        ],
        top_sectors=[("S", 1.0)],
        force_docs_map={
            "600000": {
                "fcoop1_main_net_ratio": 0.05,
                "fcoop3_sustained_days": 3,
            },
            "600002": {
                "fcoop1_main_net_ratio": 0.0,  # 不过门禁
                "fcoop3_sustained_days": 0,
            },
        },
        gate_mode="inflow",
        m4_threshold=0.01,
        min_sustained_days=1,
    )
    codes = [r["code"] for r in ranked]
    assert "600000" in codes
    assert "600001" in codes  # 缺文档保留
    assert "600002" not in codes


def test_rank_exit_forces_names_absent_from_signals():
    held = pd.DataFrame({"code": ["A", "GONE"], "weight": [0.5, 0.5]})
    sig = pd.DataFrame({"code": ["A", "B"], "rank": [1, 2]})
    force = rank_force_exit_codes(
        held, sig, {"rank_percentile_threshold": 0.99}
    )
    assert "GONE" in force
    assert "A" not in force  # 仍在池且排名好


def test_universe_mode_api():
    assert normalize_universe_mode("FUNNEL") == "funnel"
    with pytest.raises(ValueError):
        normalize_universe_mode("bogus")
