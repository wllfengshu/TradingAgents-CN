"""M4/M5：合成资金流校验合力因子公式与门禁合成。"""

from __future__ import annotations

import numpy as np
import pytest

from zstock.common.utils.common_utils import WAN_TO_YUAN
from zstock.factor_management.force_factors import ForceFactors
from zstock.factor_management.test.conftest import make_ohlcv

N = 30
A = "600000"
B = "600001"
SEC = "SW2测试"


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
        {"code": A, "sector_code": SEC, "dragon_composite_score": 80.0},
        {"code": B, "sector_code": SEC, "dragon_composite_score": 60.0},
    ]
    ohlcv = {A: ohlcv_a, B: ohlcv_b}
    return cands, flow, ohlcv, last


def test_fcoop1_is_main_net_yuan_over_turnover():
    cands, flow, ohlcv, last = _setup()
    raw = ForceFactors.apply_cooperative_force_raw(
        cands, stock_flow_recent=flow, stock_ohlcv=ohlcv, trade_date=last
    )
    by_code = {r["code"]: r for r in raw}
    expected = 10.0 * WAN_TO_YUAN / 1_000_000.0
    assert by_code[A]["fcoop1_main_net_ratio"] == pytest.approx(expected)
    assert by_code[B]["fcoop1_main_net_ratio"] == pytest.approx(0.0)


def test_fcoop2_main_retail_ratio():
    cands, flow, ohlcv, last = _setup()
    raw = ForceFactors.apply_cooperative_force_raw(
        cands, stock_flow_recent=flow, stock_ohlcv=ohlcv, trade_date=last
    )
    by_code = {r["code"]: r for r in raw}
    main = 10.0 * WAN_TO_YUAN
    retail = (1.0 + 1.0) * WAN_TO_YUAN
    assert by_code[A]["fcoop2_main_retail_ratio"] == pytest.approx(main / (main + abs(retail)))
    assert by_code[B]["fcoop2_main_retail_ratio"] == pytest.approx(0.0)


def test_fcoop3_sustained_days_count_positive_main_net():
    cands, flow, ohlcv, last = _setup()
    raw = ForceFactors.apply_cooperative_force_raw(
        cands, stock_flow_recent=flow, stock_ohlcv=ohlcv, trade_date=last
    )
    by_code = {r["code"]: r for r in raw}
    assert by_code[A]["fcoop3_sustained_days"] == pytest.approx(5.0)
    assert by_code[A]["fcoop3_sustained_days_5d"] == pytest.approx(5.0)
    assert by_code[B]["fcoop3_sustained_days"] == pytest.approx(0.0)


def test_fcoop3_nan_when_history_shorter_than_window():
    ohlcv = make_ohlcv(N, close=10.0, amount=5_000_000.0, turnover=10.0)
    last = ohlcv["trade_date"].iloc[-1]
    flow = {A: _flow_days([last], [10.0])}
    raw = ForceFactors.apply_cooperative_force_raw(
        [{"code": A, "sector_code": SEC}],
        stock_flow_recent=flow,
        stock_ohlcv={A: ohlcv},
        trade_date=last,
    )
    assert np.isnan(raw[0]["fcoop3_sustained_days_5d"])


def test_fcoop4_turnover_quality_breakpoints():
    cands, flow, ohlcv, last = _setup()
    raw = ForceFactors.apply_cooperative_force_raw(
        cands, stock_flow_recent=flow, stock_ohlcv=ohlcv, trade_date=last
    )
    by_code = {r["code"]: r for r in raw}
    assert by_code[A]["fcoop4_turnover_quality"] == pytest.approx(1.0)  # 10% 最优
    assert by_code[B]["fcoop4_turnover_quality"] == pytest.approx(0.1)  # 2% 冷盘


def test_fcoop5_acceleration_and_fcoop6_fcoop7_fcoop8():
    cands, flow, ohlcv, last = _setup()
    raw = ForceFactors.apply_cooperative_force_raw(
        cands, stock_flow_recent=flow, stock_ohlcv=ohlcv, trade_date=last
    )
    by_code = {r["code"]: r for r in raw}
    # 今日 10、昨日 10 万 → 加速度 0
    assert by_code[A]["fcoop5_main_flow_acceleration"] == pytest.approx(0.0)
    assert by_code[A]["fcoop6_main_force_aggression"] == pytest.approx(40.0 / 20.0)
    assert by_code[A]["fcoop7_super_large_net_ratio"] == pytest.approx(5.0 * WAN_TO_YUAN / 1_000_000.0)
    ratios = [(m * WAN_TO_YUAN) / 1_000_000.0 for m in [8.0, 9.0, 10.0, 10.0, 10.0]]
    slope = float(np.polyfit(np.arange(5, dtype=float), ratios, 1)[0])
    assert by_code[A]["fcoop8_main_flow_trend_5d"] == pytest.approx(float(np.clip(slope, -1.0, 1.0)))


def test_lhb_bonus_base_and_institution():
    cands, flow, ohlcv, last = _setup()
    lhb = {
        A: [
            {
                "trade_date": last,
                "inst_buy_ratio": 0.40,
                "top1_buy_ratio": 0.20,
            }
        ]
    }
    raw = ForceFactors.apply_cooperative_force_raw(
        cands, stock_flow_recent=flow, stock_ohlcv=ohlcv, stock_lhb_recent=lhb, trade_date=last
    )
    by_code = {r["code"]: r for r in raw}
    assert by_code[A]["longhu_board_bonus"] == pytest.approx(5.0 + 10.0 + 8.0)
    assert by_code[B]["longhu_board_bonus"] == pytest.approx(0.0)


def test_score_and_compose_final_inflow_gate_and_ranking(strategy_params):
    cands, flow, ohlcv, last = _setup()
    force_raw = ForceFactors.apply_cooperative_force_raw(
        cands, stock_flow_recent=flow, stock_ohlcv=ohlcv, trade_date=last
    )
    ranked = ForceFactors.score_and_compose_final(
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
    assert ranked[0]["strategy_signal_score"] > 0

    off = ForceFactors.score_and_compose_final(
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
            "dragon_composite_score": 80.0,
            "fcoop1_main_net_ratio": 0.10,
            "fcoop3_sustained_days": 5.0,
            "force_composite_score": 50.0,
        }
    ]
    ranked = ForceFactors.score_and_compose_final(
        cands,
        top_sectors=[(SEC, 90.0)],
        m5_weights={"sector": 0.4, "dragon": 0.35, "cooperative": 0.25},
        active_factors={"force": {"momentum": []}},
        m4_threshold=0.03,
        gate_mode="inflow",
    )
    # 单板块 rank=100；合力无因子 → 50；LHB=0
    expected = (0.4 * 100.0 + 0.35 * 80.0 + 0.25 * 50.0) / 1.0
    assert ranked[0]["strategy_signal_score"] == pytest.approx(expected)
    assert ranked[0]["force_composite_score"] == pytest.approx(50.0)


def test_resolve_gate_settings_regime_and_skip():
    thr, mode = ForceFactors.resolve_gate_settings(
        {"threshold_pct": 0.03, "gate_mode": "inflow"},
        "momentum",
        "green",
    )
    assert thr == pytest.approx(0.03)
    assert mode == "inflow"
    thr2, mode2 = ForceFactors.resolve_gate_settings(
        {
            "threshold_pct": 0.03,
            "gate_mode": "inflow",
            "by_regime": {"reversal": {"threshold_pct": 0.01, "gate_mode": "outflow"}},
        },
        "reversal",
        "yellow",
    )
    assert thr2 == pytest.approx(0.01)
    assert mode2 == "outflow"
    _, mode3 = ForceFactors.resolve_gate_settings(
        {"threshold_pct": 0.03, "skip_filter": True},
        "momentum",
        "green",
    )
    assert mode3 == "off"
    _, mode4 = ForceFactors.resolve_gate_settings(
        {"threshold_pct": 0.03, "gate_mode_by_grade": {"red": "off"}},
        "momentum",
        "red",
    )
    assert mode4 == "off"
