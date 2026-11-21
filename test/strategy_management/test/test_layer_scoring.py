"""策略层打分：M2/M3/M4/M5（从 factor 测试迁出）。"""

from __future__ import annotations

import pytest

from zstock.factor_management.factors_force import ForceFactors
from zstock.strategy_management.strategy_section.selection import (
    resolve_force_gate_settings,
    score_and_compose_final,
    score_dragons_from_raw,
    score_sectors_from_raw,
)

from test.factor_management.test.conftest import make_ohlcv
from zstock.common.utils.common_utils import WAN_TO_YUAN
import numpy as np

HOT = "SW2热门"
COLD = "SW2冷门"
A = "600000"
B = "600001"
C = "600002"
SEC = "SW2测试"
N = 30


def test_scores_from_raw_gates_and_top_n(strategy_params):
    raw = {
        "f21_rps_20d": {HOT: 0.2, COLD: 0.0},
        "f23_limit_up_density": {HOT: 0.5, COLD: 0.0},
        "f26_volume_growth_5d": {HOT: 0.1, COLD: 0.0},
    }
    gated = score_sectors_from_raw(
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
    assert set(gated) == {HOT}
    assert gated[HOT] == pytest.approx(50.0)

    both = score_sectors_from_raw(
        raw,
        regime="momentum",
        active_factors={
            "sector": {
                "momentum": [{"field": "f21_rps_20d", "weight": 1.0, "polarity": "positive"}]
            }
        },
        top_n=1,
        gates=[],
    )
    assert set(both) == {HOT}
    assert score_sectors_from_raw({}, regime="momentum", active_factors=strategy_params["active_factors"]) == {}


def test_sector_scores_from_dumped_raw(factor_raw, strategy_params):
    from test.factor_management.test.conftest import docs_to_field_map

    docs = factor_raw["sectors"]
    raw = docs_to_field_map(docs, "sector_code")
    scores = score_sectors_from_raw(
        raw,
        regime="momentum",
        active_factors=strategy_params["active_factors"],
        top_n=int(strategy_params["sector_layer"]["top_sectors"]),
    )
    assert scores
    assert len(scores) <= int(strategy_params["sector_layer"]["top_sectors"])
    assert all(v >= 0 for v in scores.values())


def test_scores_from_raw_gates_and_minmax():
    raw = {
        A: {"f31_excess_return_5d": 0.21, "f35_bollinger_trend": 70.0},
        B: {"f31_excess_return_5d": 0.0, "f35_bollinger_trend": float("nan")},
        C: {"f31_excess_return_5d": 0.0, "f35_bollinger_trend": 40.0},
    }
    cfg = {
        "dragon": {
            "momentum": [
                {"field": "f31_excess_return_5d", "weight": 1.0, "polarity": "positive"}
            ]
        }
    }
    gated = score_dragons_from_raw(raw, regime="momentum", active_factors=cfg, gates=None)
    assert set(gated) == {A, C}
    assert gated[A] == pytest.approx(100.0)
    assert gated[C] == pytest.approx(0.0)
    all_codes = score_dragons_from_raw(raw, regime="momentum", active_factors=cfg, gates=[])
    assert B in all_codes
    assert score_dragons_from_raw({}, regime="momentum", active_factors=cfg) == {}
    assert score_dragons_from_raw(raw, regime="momentum", active_factors=None) == {}


def test_dragon_scores_from_signal_names(signal_names_raw, strategy_params):
    raw = {d["code"]: d for d in signal_names_raw["dragons"]}
    scores = score_dragons_from_raw(
        raw,
        regime="momentum",
        active_factors=strategy_params["active_factors"],
    )
    assert set(scores) <= set(raw)
    assert scores


def _flow_days(dates, main_nets, turnover=1_000_000.0, **extra):
    rows = []
    for d, m in zip(dates, main_nets):
        row = {
            "trade_date": d,
            "main_net": m,
            "turnover": turnover,
            "m_net": extra.get("m_net", 1.0),
            "s_net": extra.get("s_net", 1.0),
            "xl_net": extra.get("xl_net", 5.0),
            "xl_buy_amount": extra.get("xl_buy_amount", 30.0),
            "l_buy_amount": extra.get("l_buy_amount", 10.0),
            "xl_sell_amount": extra.get("xl_sell_amount", 10.0),
            "l_sell_amount": extra.get("l_sell_amount", 10.0),
        }
        rows.append(row)
    return rows


def _setup():
    ohlcv_a = make_ohlcv(N, close=10.0, volume=2e6, amount=5_000_000.0, turnover=10.0)
    ohlcv_b = make_ohlcv(N, close=10.0, volume=2e6, amount=5_000_000.0, turnover=2.0)
    dates = ohlcv_a["trade_date"].iloc[-5:].tolist()
    last = dates[-1]
    flow = {
        A: _flow_days(dates, [8.0, 9.0, 10.0, 10.0, 10.0]),
        B: _flow_days(dates, [-1.0, -1.0, -1.0, -1.0, -1.0], xl_net=0.0),
    }
    cands = [
        {"code": A, "sector_code": SEC, "dragon_score": 80.0},
        {"code": B, "sector_code": SEC, "dragon_score": 60.0},
    ]
    ohlcv = {A: ohlcv_a, B: ohlcv_b}
    return cands, flow, ohlcv, last


def test_score_and_compose_final_inflow_gate_and_ranking(strategy_params):
    cands, flow, ohlcv, last = _setup()
    force_raw = ForceFactors.apply_cooperative_force_raw(
        cands, stock_flow_recent=flow, stock_ohlcv=ohlcv, trade_date=last
    )
    ranked = score_and_compose_final(
        cands,
        top_sectors=[(SEC, 80.0)],
        m5_weights={"sector": 0.4, "dragon": 0.35, "cooperative": 0.25},
        style_info={"regime": "momentum"},
        active_factors=strategy_params["active_factors"],
        force_raw=force_raw,
        m4_threshold=0.03,
        gate_mode="inflow",
    )
    assert [r["code"] for r in ranked] == [A]
    assert ranked[0]["final_score"] > 0

    off = score_and_compose_final(
        cands,
        top_sectors=[(SEC, 80.0)],
        m5_weights={"sector": 0.4, "dragon": 0.35, "cooperative": 0.25},
        style_info={"regime": "momentum"},
        active_factors=strategy_params["active_factors"],
        force_raw=force_raw,
        m4_threshold=0.03,
        gate_mode="off",
    )
    assert {r["code"] for r in off} == {A, B}


def test_m5_weighted_blend_with_known_scores():
    cands = [
        {
            "code": A,
            "sector_code": SEC,
            "dragon_score": 80.0,
            "fcoop1_main_net_ratio": 0.10,
            "fcoop3_sustained_days": 5.0,
            "force_composite_score": 50.0,
        }
    ]
    ranked = score_and_compose_final(
        cands,
        top_sectors=[(SEC, 90.0)],
        m5_weights={"sector": 0.4, "dragon": 0.35, "cooperative": 0.25},
        active_factors={"force": {"momentum": []}},
        m4_threshold=0.03,
        gate_mode="inflow",
    )
    expected = (0.4 * 100.0 + 0.35 * 80.0 + 0.25 * 50.0) / 1.0
    assert ranked[0]["final_score"] == pytest.approx(expected)
    assert ranked[0]["force_composite_score"] == pytest.approx(50.0)


def test_resolve_gate_settings_regime_and_skip():
    thr, mode, sus = resolve_force_gate_settings(
        {"threshold_pct": 0.03, "gate_mode": "inflow"},
        "momentum",
        "green",
    )
    assert thr == pytest.approx(0.03)
    assert mode == "inflow"
    assert sus == 1
    thr2, mode2, sus2 = resolve_force_gate_settings(
        {
            "threshold_pct": 0.03,
            "gate_mode": "inflow",
            "min_sustained_days": 2,
            "by_regime": {
                "reversal": {
                    "threshold_pct": 0.01,
                    "gate_mode": "outflow",
                    "min_sustained_days": 3,
                }
            },
        },
        "reversal",
        "yellow",
    )
    assert thr2 == pytest.approx(0.01)
    assert mode2 == "outflow"
    assert sus2 == 3
    _, mode3, _ = resolve_force_gate_settings(
        {"threshold_pct": 0.03, "skip_filter": True},
        "momentum",
        "green",
    )
    assert mode3 == "off"
    _, mode4, _ = resolve_force_gate_settings(
        {"threshold_pct": 0.03, "gate_mode_by_grade": {"red": "off"}},
        "momentum",
        "red",
    )
    assert mode4 == "off"
    # context(regime_grade) 优先于 grade
    _, mode5, _ = resolve_force_gate_settings(
        {
            "threshold_pct": 0.03,
            "gate_mode": "inflow",
            "gate_mode_by_grade": {"yellow": "off"},
            "gate_mode_by_context": {"reversal_yellow": "outflow"},
        },
        "reversal",
        "yellow",
    )
    assert mode5 == "outflow"
