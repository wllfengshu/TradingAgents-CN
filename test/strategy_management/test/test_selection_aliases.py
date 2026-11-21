"""selection：别名字段、并列、门禁空、force 空配置。"""

from __future__ import annotations

import pytest

from zstock.strategy_management.strategy_section.selection import (
    score_and_compose_final,
    score_dragons_from_raw,
    score_sectors_from_raw,
    select_top_sectors,
)


def test_select_top_sectors_tiebreak_and_empty():
    assert select_top_sectors({}) == {}
    scores = {"A": 1.0, "B": 1.0, "C": 0.5}
    tb = {"A": 0.1, "B": 0.9}
    out = select_top_sectors(scores, tiebreaker=tb, top_n=2)
    assert list(out.keys())[0] == "B"


def test_sector_alias_and_empty_gates():
    raw = {
        "f21_rps": {"S1": 0.2, "S2": 0.1},
        "f23_limit_up_density": {"S1": 0.0, "S2": 0.0},  # 默认门禁全杀
    }
    assert (
        score_sectors_from_raw(
            raw,
            regime="momentum",
            active_factors={
                "sector": {
                    "momentum": [{"field": "f21_rps_20d", "weight": 1.0, "polarity": "positive"}]
                }
            },
            top_n=2,
            gates=None,
        )
        == {}
    )

    raw2 = {
        "f21_rps": {"S1": 0.2, "S2": 0.05},
        "f23_limit_up_density": {"S1": 0.1, "S2": 0.05},
        "f26_volume_growth": {"S1": 0.2, "S2": 0.1},
    }
    scores = score_sectors_from_raw(
        raw2,
        regime="momentum",
        active_factors={
            "sector": {
                "momentum": [{"field": "f21_rps_20d", "weight": 1.0, "polarity": "positive"}]
            }
        },
        top_n=1,
        gates=[],
    )
    assert "S1" in scores


def test_dragon_alias_and_compose_missing_map():
    raw = {
        "A": {"f31_excess_return_5d": 0.1, "f35_bollinger_trend": 60.0, "f34_resonance_pct": 0.2},
        "B": {"f31_excess_return_5d": 0.0, "f35_bollinger_trend": 40.0},
    }
    cfg = {
        "dragon": {
            "momentum": [
                {"field": "f31_excess_return_5d", "weight": 0.7, "polarity": "positive"},
                {"field": "f34_resonance_pct_5d", "weight": 0.3, "polarity": "positive"},
            ]
        }
    }
    scores = score_dragons_from_raw(raw, regime="momentum", active_factors=cfg, gates=[])
    assert "A" in scores

    ranked = score_and_compose_final(
        candidates=[{"code": "A", "sector_code": "S", "dragon_score": 80}],
        top_sectors=[("S", 1.0)],
        force_docs_map={"A": {"fcoop1_main_net_ratio": 0.05, "fcoop3_sustained_days": 2}},
        m4_threshold=0.01,
        gate_mode="inflow",
        min_sustained_days=1,
        active_factors={
            "force": {
                "momentum": [
                    {"field": "fcoop1_main_net_ratio", "weight": 1.0, "polarity": "positive"}
                ]
            }
        },
        style_info={"regime": "momentum"},
    )
    assert ranked and ranked[0]["code"] == "A"

    # force_docs_map 缺 code + gate off → 仍保留（空文档过门禁）
    kept = score_and_compose_final(
        candidates=[{"code": "Z", "sector_code": "S", "dragon_score": 80}],
        top_sectors=[("S", 1.0)],
        force_docs_map={},
        gate_mode="off",
    )
    assert len(kept) == 1 and kept[0]["code"] == "Z"
    # 缺文档 + inflow → 跳过门禁并保留（避免预计算系统性少票）
    kept_missing = score_and_compose_final(
        candidates=[{"code": "Z", "sector_code": "S", "dragon_score": 80}],
        top_sectors=[("S", 1.0)],
        force_docs_map={},
        gate_mode="inflow",
        m4_threshold=0.01,
        min_sustained_days=1,
    )
    assert len(kept_missing) == 1 and kept_missing[0]["code"] == "Z"
