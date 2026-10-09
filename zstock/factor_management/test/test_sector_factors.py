"""M2：合成 OHLCV / 资金流校验板块因子公式。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from zstock.common.utils.common_utils import WAN_TO_YUAN
from zstock.factor_management.sector_factors import SectorFactors
from zstock.factor_management.test.conftest import make_ohlcv

N = 65
HOT = "SW2热门"
COLD = "SW2冷门"
A = "600000"
B = "600001"
C = "600002"
D = "600003"


def _close_with_last_return(n: int, last_ret: float, base: float = 10.0) -> np.ndarray:
    close = np.full(n, base, dtype=float)
    close[-1] = base * (1.0 + last_ret)
    return close


def _limit_up_close(n: int, boards: int, base: float = 10.0) -> np.ndarray:
    close = np.full(n, base, dtype=float)
    px = base
    for i in range(n - boards, n):
        px *= 1.10
        close[i] = px
    return close


def _dates(n: int = N) -> list:
    return make_ohlcv(n, close=10.0)["trade_date"].tolist()


def _sector_index(n: int, last_n_ret: float) -> pd.DataFrame:
    close = np.full(n, 100.0)
    close[-1] = 100.0 * (1.0 + last_n_ret)
    # 让 close[-21] 仍为 100，使 20 日 RPS = last_n_ret
    return make_ohlcv(n, close=close, volume=1_000_000.0, amount=1_000_000.0)


def _bundle():
    dates = _dates()
    last = dates[-1]
    stocks = {
        A: make_ohlcv(N, close=_limit_up_close(N, 2), volume=1e6, amount=np.concatenate(
            [np.full(N - 1, 1e7), np.array([4e7])]
        )),
        B: make_ohlcv(N, close=_close_with_last_return(N, -0.01), volume=1e6, amount=1e7),
        C: make_ohlcv(N, close=_close_with_last_return(N, 0.02), volume=2e6, amount=2e7),
        D: make_ohlcv(N, close=_close_with_last_return(N, -0.01), volume=1e6, amount=1e7),
    }
    # A 末两日连板，C 20 日收益 20% 但非涨停（10→12 一天）
    flow = {
        A: [{"trade_date": last, "main_net": 50.0, "turnover": 1_000_000.0}],
        B: [{"trade_date": last, "main_net": 30.0, "turnover": 1_000_000.0}],
        C: [{"trade_date": last, "main_net": 10.0, "turnover": 1_000_000.0}],
        D: [{"trade_date": last, "main_net": 0.0, "turnover": 1_000_000.0}],
    }
    members = {HOT: [A, B], COLD: [C, D]}
    sectors = [
        {"sector_code": HOT, "sector_name": "热门"},
        {"sector_code": COLD, "sector_name": "冷门"},
    ]
    return stocks, flow, members, sectors, last


def test_rps_is_last_over_base_minus_one():
    hot_ohlcv = _sector_index(N, 0.20)
    cold_ohlcv = _sector_index(N, 0.0)
    stocks, flow, members, sectors, last = _bundle()
    raw = SectorFactors.calculate_all_sector_factors_raw(
        sectors,
        members,
        stocks,
        stock_flow_recent=flow,
        sector_ohlcv={HOT: hot_ohlcv, COLD: cold_ohlcv},
        market_sector_ohlcv={HOT: hot_ohlcv, COLD: cold_ohlcv},
        trade_date=last,
    )
    assert raw["f21_rps_20d"][HOT] == pytest.approx(0.20)
    assert raw["f21_rps"][HOT] == pytest.approx(0.20)
    assert raw["f21_rps_20d"][COLD] == pytest.approx(0.0)
    assert raw["f21_rps_10d"][HOT] == pytest.approx(0.20)
    assert raw["f21_rps_60d"][HOT] == pytest.approx(0.20)


def test_f22_main_flow_sums_wan_to_yuan():
    stocks, flow, members, sectors, last = _bundle()
    dummy = _sector_index(N, 0.0)
    raw = SectorFactors.calculate_all_sector_factors_raw(
        sectors,
        members,
        stocks,
        stock_flow_recent=flow,
        sector_ohlcv={HOT: dummy, COLD: dummy},
        market_sector_ohlcv={HOT: dummy, COLD: dummy},
        trade_date=last,
    )
    assert raw["f22_main_flow"][HOT] == pytest.approx((50.0 + 30.0) * WAN_TO_YUAN)
    assert raw["f22_main_flow"][COLD] == pytest.approx(10.0 * WAN_TO_YUAN)


def test_f23_density_and_f24_max_boards():
    stocks, flow, members, sectors, last = _bundle()
    dummy = _sector_index(N, 0.0)
    raw = SectorFactors.calculate_all_sector_factors_raw(
        sectors,
        members,
        stocks,
        stock_flow_recent=flow,
        sector_ohlcv={HOT: dummy, COLD: dummy},
        market_sector_ohlcv={HOT: dummy, COLD: dummy},
        trade_date=last,
    )
    # HOT: A 涨停、B 否 → 0.5；连板高度 = A 的 2
    assert raw["f23_limit_up_density"][HOT] == pytest.approx(0.5)
    assert raw["f24_max_consecutive"][HOT] == 2
    assert raw["f23_limit_up_density"][COLD] == pytest.approx(0.0)
    assert raw["f24_max_consecutive"][COLD] == 0


def test_eligible_codes_exclude_limit_up_member():
    stocks, flow, members, sectors, last = _bundle()
    dummy = _sector_index(N, 0.0)
    raw = SectorFactors.calculate_all_sector_factors_raw(
        sectors,
        members,
        stocks,
        stock_flow_recent=flow,
        sector_ohlcv={HOT: dummy, COLD: dummy},
        market_sector_ohlcv={HOT: dummy, COLD: dummy},
        trade_date=last,
        eligible_codes={B, C, D},
    )
    assert raw["f23_limit_up_density"][HOT] == pytest.approx(0.0)
    assert raw["f24_max_consecutive"][HOT] == 0
    assert raw["f22_main_flow"][HOT] == pytest.approx(30.0 * WAN_TO_YUAN)


def test_f26_volume_growth_is_short_ma_over_long_ma():
    amt_hot = np.concatenate([np.full(45, 1e6), np.full(20, 3e6)])
    amt_cold = np.full(N, 1e6)
    hot = make_ohlcv(N, close=100.0, amount=amt_hot)
    cold = make_ohlcv(N, close=100.0, amount=amt_cold)
    stocks, flow, members, sectors, last = _bundle()
    raw = SectorFactors.calculate_all_sector_factors_raw(
        sectors,
        members,
        stocks,
        stock_flow_recent=flow,
        sector_ohlcv={HOT: hot, COLD: cold},
        market_sector_ohlcv={HOT: hot, COLD: cold},
        trade_date=last,
    )
    ma5 = float(pd.Series(amt_hot).rolling(5).mean().iloc[-1])
    ma20 = float(pd.Series(amt_hot).rolling(20).mean().iloc[-1])
    assert raw["f26_volume_growth_5d"][HOT] == pytest.approx(ma5 / ma20 - 1.0)
    assert raw["f26_volume_growth"][COLD] == pytest.approx(0.0)


def test_f27_f29_f30_structure_factors():
    stocks, flow, members, sectors, last = _bundle()
    dummy = _sector_index(N, 0.0)
    raw = SectorFactors.calculate_all_sector_factors_raw(
        sectors,
        members,
        stocks,
        stock_flow_recent=flow,
        sector_ohlcv={HOT: dummy, COLD: dummy},
        market_sector_ohlcv={HOT: dummy, COLD: dummy},
        trade_date=last,
    )
    # HOT: A 上涨 B 平 → breadth 0.5；A 创 20 日新高（连板）B 否 → 0.5
    assert raw["f29_sector_breadth"][HOT] == pytest.approx(0.5)
    assert raw["f27_new_high_ratio"][HOT] == pytest.approx(0.5)
    # HOT 末额 4e7 + 1e7，Top3/全部 = 1.0（仅两只）
    assert raw["f30_sector_concentration"][HOT] == pytest.approx(1.0)


def test_f28_consistency_counts_top_decile_days():
    n = 15
    dates = pd.bdate_range("2024-01-02", periods=n).strftime("%Y-%m-%d").tolist()
    market = {}
    for i in range(20):
        close = np.full(n, 100.0)
        code = f"S{i:02d}"
        if i <= 2:
            # 至少 3 个高收益板块，使 90 分位严格高于其余板块
            close = 100.0 * (1.02 ** np.arange(n))
        else:
            close = 100.0 * (0.995 ** np.arange(n))
        market[code] = pd.DataFrame(
            {
                "trade_date": dates,
                "close": close,
                "open": close,
                "high": close,
                "low": close,
                "volume": np.full(n, 1e6),
                "amount": np.full(n, 1e6),
            }
        )
    sectors = [{"sector_code": k, "sector_name": k} for k in market]
    members = {k: [A] for k in market}
    dummy_stock = {A: make_ohlcv(n, close=10.0, start="2024-01-02")}
    raw = SectorFactors.calculate_all_sector_factors_raw(
        sectors,
        members,
        dummy_stock,
        sector_ohlcv=market,
        market_sector_ohlcv=market,
        trade_date=dates[-1],
    )
    assert raw["f28_consistency"]["S00"] == 10
    assert raw["f28_consistency"]["S10"] == 0


def test_f25_volume_slope_positive_when_share_rises():
    amt_hot = np.linspace(1e6, 5e6, N)
    amt_cold = np.full(N, 5e6)
    hot = make_ohlcv(N, close=100.0, amount=amt_hot)
    cold = make_ohlcv(N, close=100.0, amount=amt_cold)
    stocks, flow, members, sectors, last = _bundle()
    raw = SectorFactors.calculate_all_sector_factors_raw(
        sectors,
        members,
        stocks,
        stock_flow_recent=flow,
        sector_ohlcv={HOT: hot, COLD: cold},
        market_sector_ohlcv={HOT: hot, COLD: cold},
        trade_date=last,
    )
    assert raw["f25_volume_slope_5d"][HOT] > raw["f25_volume_slope_5d"][COLD]


def test_scores_from_raw_gates_and_top_n(strategy_params):
    raw = {
        "f21_rps_20d": {HOT: 0.2, COLD: 0.0},
        "f23_limit_up_density": {HOT: 0.5, COLD: 0.0},
        "f26_volume_growth_5d": {HOT: 0.1, COLD: 0.0},
    }
    # gates=None：兼容旧逻辑，要求 f23>0
    gated = SectorFactors.scores_from_raw(
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
    # 过门槛后仅一只，min-max 退化为中性 50
    assert gated[HOT] == pytest.approx(50.0)

    both = SectorFactors.scores_from_raw(
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
    assert SectorFactors.scores_from_raw({}, regime="momentum", active_factors=strategy_params["active_factors"]) == {}


def test_raw_from_docs_roundtrip_fields():
    docs = [
        {
            "sector_code": HOT,
            "f21_rps_20d": 0.2,
            "f22_main_flow": 800000.0,
            "f23_limit_up_density": 0.5,
            "f24_max_consecutive": 2,
        }
    ]
    packed = SectorFactors.raw_from_docs(docs)
    assert packed["f21_rps_20d"][HOT] == 0.2
    assert packed["f22_main_flow"][HOT] == 800000.0
    assert packed["f23_limit_up_density"][HOT] == 0.5
    assert packed["f24_max_consecutive"][HOT] == 2


def test_aggregate_sector_ohlcv_reconstructs_equal_member_return():
    n = 10
    stocks = {
        A: make_ohlcv(n, close=_close_with_last_return(n, 0.10)),
        B: make_ohlcv(n, close=_close_with_last_return(n, 0.10)),
    }
    ohlcv, flow = SectorFactors.aggregate_sector_ohlcv(
        {HOT: [A, B]},
        stocks,
        stock_flow_recent={
            A: [{"trade_date": stocks[A]["trade_date"].iloc[-1], "main_net": 1.0, "turnover": 100.0}],
            B: [{"trade_date": stocks[B]["trade_date"].iloc[-1], "main_net": 2.0, "turnover": 100.0}],
        },
    )
    df = ohlcv[HOT]
    last_ret = float(df["close"].iloc[-1] / df["close"].iloc[-2] - 1.0)
    assert last_ret == pytest.approx(0.10)
    assert flow[HOT]["main_flow"] == pytest.approx(3.0 * WAN_TO_YUAN)


def test_sector_scores_from_dumped_raw(factor_raw, strategy_params):
    from zstock.factor_management.test.conftest import docs_to_field_map

    docs = factor_raw["sectors"]
    raw = docs_to_field_map(docs, "sector_code")
    scores = SectorFactors.scores_from_raw(
        raw,
        regime="momentum",
        active_factors=strategy_params["active_factors"],
        top_n=int(strategy_params["sector_layer"]["top_sectors"]),
    )
    assert scores
    assert len(scores) <= int(strategy_params["sector_layer"]["top_sectors"])
    assert all(v >= 0 for v in scores.values())
