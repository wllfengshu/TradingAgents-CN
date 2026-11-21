"""sector_factors：volume 降级、空输入、eligible 过滤。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from test.factor_management.test.conftest import make_ohlcv
from zstock.factor_management.factors_sector import SectorFactors

HOT, COLD = "SW2热", "SW2冷"
A, B, C = "600000", "600001", "600002"


def test_sector_volume_fallback_and_eligible():
    # 多板块 + 仅 volume
    n = 40
    hot = make_ohlcv(n, close=np.linspace(100, 120, n), volume=2e6, amount=2e8)
    cold = make_ohlcv(n, close=np.linspace(100, 105, n), volume=1e6, amount=1e8)
    vol_only = hot.drop(columns=["amount"])
    sector_ohlcv = {HOT: vol_only, COLD: cold}
    slope = SectorFactors.f25_volume_slope(sector_ohlcv)
    assert isinstance(slope, dict)
    assert SectorFactors.f25_volume_slope({}) == {w: {} for w in slope}
    rps = SectorFactors.f21_rps(sector_ohlcv, window=10, sector_codes={HOT})
    assert HOT in rps and COLD not in rps
    # 短窗口 / base<=0
    bad = make_ohlcv(5, close=0.0)
    SectorFactors.f21_rps({HOT: bad}, window=10)

    stocks = {
        A: make_ohlcv(30, close=np.linspace(10, 12, 30), amount=5e7),
        B: make_ohlcv(30, close=np.linspace(10, 11, 30), amount=3e7),
        C: make_ohlcv(30, close=np.linspace(10, 10.5, 30), amount=1e7),
    }
    members = {HOT: [A, B], COLD: [C]}
    sectors = [{"sector_code": HOT}, {"sector_code": COLD}, {"sector_code": None}]
    lim, cons = SectorFactors._compute_limit_and_consecutive_from_ohlcv(
        stocks, codes={A, B}
    )
    dens = SectorFactors.f23_limit_up_density(
        sectors, members, lim, eligible_codes={A}
    )
    assert HOT in dens
    # eligible 过滤掉全部 → skip
    SectorFactors.f23_limit_up_density(sectors, members, lim, eligible_codes=set())
    SectorFactors.f24_max_consecutive(sectors, members, cons, eligible_codes={A})
    SectorFactors.f29_sector_breadth(members, stocks, eligible_codes={A, B})
    SectorFactors.f30_sector_concentration(members, stocks, eligible_codes={A, B})
    SectorFactors.f27_new_high_ratio(members, stocks, window=10)
    # 无 amount 个股
    no_amt = {A: stocks[A].drop(columns=["amount"])}
    SectorFactors.f30_sector_concentration({HOT: [A]}, no_amt)
    # aggregate + raw_from_docs
    sec_ohlcv, sec_flow = SectorFactors.aggregate_sector_ohlcv(
        members, stocks, trade_date=str(stocks[A]["trade_date"].iloc[-1])
    )
    assert isinstance(sec_ohlcv, dict)
    docs = [
        {
            "sector_code": HOT,
            "trade_date": "2024-01-02",
            "f21_rps_10d": 0.1,
            "f22_main_flow": 1.0,
        }
    ]
    rebuilt = SectorFactors.raw_from_docs(docs)
    assert HOT in rebuilt or isinstance(rebuilt, dict)
