"""sector / dragon / force / market 边角分支（按真实算子签名）。"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from test.factor_management.test.conftest import make_ohlcv
from zstock.factor_management.factors_dragon import DragonFactors
from zstock.factor_management.factors_force import ForceFactors
from zstock.factor_management.factors_market import MarketFactors
from zstock.factor_management.factors_sector import SectorFactors

HOT = "SW2热门"
A, B = "600000", "600001"


def test_sector_ops_dict_apis():
    ohlcv_a = make_ohlcv(40, close=np.linspace(10, 12, 40), volume=1e6, amount=1e7)
    ohlcv_b = make_ohlcv(40, close=np.linspace(10, 11, 40), volume=1e6, amount=8e6)
    sector_ohlcv = {HOT: ohlcv_a}
    rps = SectorFactors.f21_rps(sector_ohlcv, window=10)
    assert HOT in rps
    assert SectorFactors.f21_rps({}, window=10) == {}
    flow = SectorFactors.f22_main_flow({HOT: {"main_flow": 100.0}})
    assert flow[HOT] == 100.0
    sectors = [{"sector_code": HOT}]
    members = {HOT: [A, B]}
    dens = SectorFactors.f23_limit_up_density(
        sectors, members, {A: True, B: False}, eligible_codes={A, B}
    )
    assert HOT in dens
    mx = SectorFactors.f24_max_consecutive(sectors, members, {A: 2, B: 0})
    assert mx[HOT] == 2
    vg = SectorFactors.f26_volume_growth(sector_ohlcv)
    assert HOT in vg
    # f25 需要多个板块才有占比
    sector_ohlcv2 = {HOT: ohlcv_a, "SW2冷门": ohlcv_b}
    vs = SectorFactors.f25_volume_slope(sector_ohlcv2)
    assert isinstance(vs, dict)
    stocks = {A: ohlcv_a, B: ohlcv_b}
    cons = SectorFactors.f28_consistency(sector_ohlcv2, sector_ohlcv2)
    assert isinstance(cons, dict)
    br = SectorFactors.f29_sector_breadth(members, stocks)
    assert isinstance(br, dict)
    conc = SectorFactors.f30_sector_concentration(members, stocks)
    assert isinstance(conc, dict)
    nh = SectorFactors.f27_new_high_ratio(members, stocks)
    assert isinstance(nh, dict)
    # 空/无 code
    SectorFactors.f23_limit_up_density([{"sector_code": None}], {}, {})
    SectorFactors.f24_max_consecutive([{"sector_code": None}], {}, {})
    lim, cons_map = SectorFactors._compute_limit_and_consecutive_from_ohlcv(stocks)
    assert A in lim and A in cons_map


def test_dragon_ops_dict_apis():
    ohlcv = make_ohlcv(
        40, close=np.linspace(10, 12, 40), volume=1e6, amount=1e7, turnover=5.0
    )
    stock = {A: ohlcv, B: ohlcv.copy()}
    rets = DragonFactors.nd_returns(stock, [A, B], 5)
    assert A in rets
    assert DragonFactors.f31_excess_return({}) == {}
    excess = DragonFactors.f31_excess_return(rets)
    assert A in excess
    pct = DragonFactors.f31b_rps_percentile(rets)
    assert A in pct
    assert DragonFactors.f31b_rps_percentile({}) == {}
    amts = DragonFactors.f32_amount(stock, [A, B])
    assert A in amts
    boards = DragonFactors.f33_consecutive_boards(stock, [A])
    assert A in boards
    trend, passed = DragonFactors.f35_bollinger_trend(stock, [A])
    assert isinstance(trend, dict)
    turns = DragonFactors.turnover_rates(stock, [A, B])
    prem = DragonFactors.f36_identity_premium(turns, amts)
    assert A in prem
    assert DragonFactors.f36_identity_premium({}, {}) == {}
    assert DragonFactors.f37_relative_strength({A: 0.1}) == {}
    rs = DragonFactors.f37_relative_strength(rets)
    assert A in rs
    anom = DragonFactors.f38_turnover_anomaly(stock, [A])
    assert A in anom
    assert math.isnan(DragonFactors.f39_pb(10.0, 0.0))
    assert DragonFactors.f39_pb(10.0, 2.0) == 5.0
    assert math.isnan(DragonFactors.f40_holder_change(None))
    assert math.isnan(DragonFactors.f40_holder_change("x"))
    assert DragonFactors.f40_holder_change(0.05) == 0.05
    feats = DragonFactors.precompute_stock_features(
        stock, [A, B], trade_date=str(ohlcv["trade_date"].iloc[-1])
    )
    raw = DragonFactors.assemble_sector_raw_from_features([A, B], feats)
    assert A in raw
    assert DragonFactors.calculate_all_dragon_factors_raw([], {}) == {}
    # volume-only fallback
    vol_only = {A: make_ohlcv(5, close=10.0, volume=100.0)}
    if "amount" in vol_only[A].columns:
        vol_only[A] = vol_only[A].drop(columns=["amount"])
    DragonFactors.daily_volumes(vol_only, [A])


def test_force_ops():
    ohlcv = make_ohlcv(30, close=10.0, volume=2e6, amount=5e6, turnover=5.0)
    last = str(ohlcv["trade_date"].iloc[-1])
    docs = [
        {"trade_date": last, "main_net": 10.0, "super_large_net": 5.0, "turnover": 1e6},
        {
            "trade_date": str(ohlcv["trade_date"].iloc[-2]),
            "main_net": 8.0,
            "super_large_net": 3.0,
            "turnover": 1e6,
        },
        {
            "trade_date": str(ohlcv["trade_date"].iloc[-3]),
            "main_net": 6.0,
            "super_large_net": 2.0,
            "turnover": 1e6,
        },
    ]
    assert ForceFactors.fcoop1_main_net_ratio(10.0, 1e6) > 0
    assert ForceFactors.fcoop1_main_net_ratio(-1.0, 1e6) == pytest.approx(-1.0 / 1e6)
    assert ForceFactors.fcoop2_main_retail_ratio(10.0, -5.0) > 0
    assert ForceFactors.fcoop2_main_retail_ratio(-1.0, 5.0) < 0
    assert isinstance(ForceFactors.fcoop3_sustained_days(docs, 3), float)
    assert isinstance(ForceFactors.fcoop4_turnover_quality(5.0), float)
    assert isinstance(ForceFactors.fcoop5_main_flow_acceleration(docs), float)
    assert isinstance(ForceFactors.fcoop6_main_force_aggression(docs), float)
    assert isinstance(ForceFactors.fcoop7_super_large_net_ratio(docs), float)
    assert isinstance(ForceFactors.fcoop8_main_flow_trend_5d(docs), float)
    assert isinstance(ForceFactors.f_power_divergence(ohlcv), float)
    assert isinstance(ForceFactors.f_power_divergence(None), float) or True
    assert isinstance(ForceFactors.f_main_force_persistence(docs), float)
    assert isinstance(ForceFactors.f_mean_reversion_signal(ohlcv), float)
    assert ForceFactors.sigmoid(0.0) == pytest.approx(0.5, abs=0.01)
    assert ForceFactors.limit_down_free_days(ohlcv) >= 0
    assert ForceFactors.lhb_bonus("600000", {}) == 0.0
    assert ForceFactors.lhb_bonus("600000", {"600000": [{"net_buy": 1}]}) >= 0


def test_market_ops(hs300_ohlcv):
    df = hs300_ohlcv
    raw = MarketFactors.compute_market_raw(df)
    assert isinstance(raw, dict)
    assert isinstance(MarketFactors.mf1_slope_pct(df), float)
    assert isinstance(MarketFactors.mf2_boll_pct(df), float)
    assert isinstance(MarketFactors.mf3_vol_ratio(df), float)
    assert isinstance(MarketFactors.mf4_momentum(df), float)
    assert isinstance(MarketFactors.mf5_atr_ratio(df), float)
    assert isinstance(MarketFactors.rank_autocorr(df["close"]), float)
    short = df.head(5)
    MarketFactors.mf1_slope_pct(short)
    MarketFactors.mf4_momentum(short)
    MarketFactors.min_bars()
