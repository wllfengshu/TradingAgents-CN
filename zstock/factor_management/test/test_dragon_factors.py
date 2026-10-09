"""M3：合成个股日线校验龙头因子公式。"""

from __future__ import annotations

import numpy as np
import pytest

from zstock.factor_management.dragon_factors import DragonFactors
from zstock.factor_management.test.conftest import make_ohlcv

N = 40
A = "600000"
B = "600001"
C = "600002"
SEC = "SW2测试"


def _leader_close(n: int = N) -> np.ndarray:
    """末两日连板 10%，5 日收益 (12.1-10)/10 = 0.21。"""
    close = np.full(n, 10.0)
    close[-2] = 11.0
    close[-1] = 12.1
    return close


def _ohlcv_map():
    close_a = _leader_close()
    amt_a = np.full(N, 1e7)
    amt_a[-2] = 2e7
    amt_a[-1] = 3e7
    to_a = np.full(N, 5.0)
    to_a[-1] = 10.0
    to_flat = np.full(N, 5.0)
    return {
        A: make_ohlcv(N, close=close_a, volume=1e6, amount=amt_a, turnover=to_a),
        B: make_ohlcv(N, close=10.0, volume=1e6, amount=5e6, turnover=to_flat),
        C: make_ohlcv(N, close=10.0, volume=1e6, amount=5e6, turnover=to_flat),
    }


class _FakeFund:
    is_loaded = True

    def compute_pb(self, code, close, trade_date):
        return float(close) / 5.0

    def get_holder_change(self, code, trade_date):
        return 0.12 if code == A else -0.05


def test_f31_excess_return_is_stock_minus_sector_median():
    raw = DragonFactors.calculate_all_dragon_factors_in_sector_raw(
        [A, B, C], _ohlcv_map()
    )
    ret_a = (12.1 - 10.0) / 10.0
    median = float(np.median([ret_a, 0.0, 0.0]))
    assert raw[A]["f31_excess_return_5d"] == pytest.approx(ret_a - median)
    assert raw[A]["f31_excess_return"] == pytest.approx(ret_a - median)
    assert raw[B]["f31_excess_return_5d"] == pytest.approx(0.0 - median)
    assert raw[C]["f31_excess_return_5d"] == pytest.approx(0.0 - median)


def test_f31b_percentile_and_f37_zscore():
    raw = DragonFactors.calculate_all_dragon_factors_in_sector_raw(
        [A, B, C], _ohlcv_map()
    )
    ret_a = (12.1 - 10.0) / 10.0
    rets = np.array([ret_a, 0.0, 0.0])
    unique = np.unique(rets)
    # searchsorted left / (n-1) * 100
    assert raw[A]["f31b_rps_percentile"] == pytest.approx(1.0 / 2.0 * 100.0)
    assert raw[B]["f31b_rps_percentile"] == pytest.approx(0.0)
    mean = float(np.mean(rets))
    std = float(np.std(rets))
    assert raw[A]["f37_relative_strength"] == pytest.approx((ret_a - mean) / std)


def test_f32_amount_f33_boards_f36_identity():
    ohlcv = _ohlcv_map()
    raw = DragonFactors.calculate_all_dragon_factors_in_sector_raw([A, B, C], ohlcv)
    assert raw[A]["f32_amount"] == pytest.approx(float(ohlcv[A]["amount"].iloc[-1]))
    assert raw[A]["f33_consecutive_boards"] == 2
    assert raw[B]["f33_consecutive_boards"] == 0
    # 换手 10 / mean(10,5,5)
    mean_to = (10.0 + 5.0 + 5.0) / 3.0
    assert raw[A]["f36_identity_premium"] == pytest.approx(10.0 / mean_to)
    assert raw[B]["f36_identity_premium"] == pytest.approx(5.0 / mean_to)


def test_f34_resonance_and_f38_turnover_anomaly():
    ohlcv = _ohlcv_map()
    raw = DragonFactors.calculate_all_dragon_factors_in_sector_raw([A, B, C], ohlcv)
    # A 末 5 日价变: 0,0,0,+1,+1.1；额变末两日为正 → 健康日 2/5
    assert raw[A]["f34_resonance_pct_5d"] == pytest.approx(2.0 / 5.0)
    assert raw[B]["f34_resonance_pct_5d"] == pytest.approx(0.0)
    to = ohlcv[A]["turnover"].to_numpy()
    expected_anom = float(to[-1] / np.nanmean(to[-20:]))
    assert raw[A]["f38_turnover_anomaly"] == pytest.approx(expected_anom)


def test_f35_bollinger_pass_on_uptrend():
    close = 10.0 + 0.2 * np.arange(N)
    up = make_ohlcv(N, close=close)
    flat = make_ohlcv(N, close=10.0)
    raw = DragonFactors.calculate_all_dragon_factors_in_sector_raw(
        [A, B], {A: up, B: flat}
    )
    assert np.isfinite(raw[A]["f35_bollinger_trend"])
    assert raw[A]["f35_bollinger_pass"] == 1.0
    assert raw[B]["f35_bollinger_pass"] == 0.0


def test_f39_f40_from_injected_provider():
    ohlcv = _ohlcv_map()
    last = str(ohlcv[A]["trade_date"].iloc[-1])
    raw = DragonFactors.calculate_all_dragon_factors_in_sector_raw(
        [A, B, C],
        ohlcv,
        trade_date=last,
        fund_provider=_FakeFund(),
    )
    assert raw[A]["f39_pb"] == pytest.approx(12.1 / 5.0)
    assert raw[A]["f40_holder_change"] == pytest.approx(0.12)
    assert raw[B]["f40_holder_change"] == pytest.approx(-0.05)
    no_fund = DragonFactors.calculate_all_dragon_factors_in_sector_raw([A], ohlcv)
    assert np.isnan(no_fund[A]["f39_pb"])


def test_multi_sector_reuses_same_stock_features():
    ohlcv = _ohlcv_map()
    by_sec = DragonFactors.calculate_all_dragon_factors_raw(
        [(SEC, [A, B, C]), ("SW2其它", [B, C])],
        ohlcv,
    )
    assert SEC in by_sec and "SW2其它" in by_sec
    assert by_sec[SEC][A]["f33_consecutive_boards"] == 2
    assert A not in by_sec["SW2其它"]
    assert by_sec["SW2其它"][B]["f32_amount"] == pytest.approx(by_sec[SEC][B]["f32_amount"])


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
    gated = DragonFactors.scores_from_raw(raw, regime="momentum", active_factors=cfg, gates=None)
    assert set(gated) == {A, C}
    assert gated[A] == pytest.approx(100.0)
    assert gated[C] == pytest.approx(0.0)
    all_codes = DragonFactors.scores_from_raw(raw, regime="momentum", active_factors=cfg, gates=[])
    assert B in all_codes
    assert DragonFactors.scores_from_raw({}, regime="momentum", active_factors=cfg) == {}
    assert DragonFactors.scores_from_raw(raw, regime="momentum", active_factors=None) == {}


def test_raw_from_doc_aliases():
    packed = DragonFactors.raw_from_doc(
        {
            "code": A,
            "f31_excess_return": 0.21,
            "f32_amount": 3e7,
            "f33_consecutive_boards": 2,
            "f34_resonance_pct": 0.4,
        }
    )
    assert packed["f31_excess_return_5d"] == 0.21
    assert packed["f32_amount"] == 3e7
    assert packed["f33_consecutive_boards"] == 2
    assert packed["f34_resonance_pct_5d"] == 0.4


def test_dragon_scores_from_signal_names(signal_names_raw, strategy_params):
    raw = {d["code"]: d for d in signal_names_raw["dragons"]}
    scores = DragonFactors.scores_from_raw(
        raw,
        regime="momentum",
        active_factors=strategy_params["active_factors"],
    )
    assert set(scores) <= set(raw)
    assert scores
