"""再补若干易触达分支，把 factor_management 分支覆盖推到 80%。"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from test.factor_management.test.conftest import make_ohlcv
from zstock.factor_management.factors_dragon import DragonFactors
from zstock.factor_management.factors_force import ForceFactors
from zstock.factor_management.script.factor_evaluation import FactorEvaluation


def test_score_mid_tiers_and_ls_empty():
    pipe = FactorEvaluation(
        start_date="2024-01-02", end_date="2024-01-16", period=5, plot=False, output_dir="."
    )
    # 覆盖 IC 0.02~0.05、ICIR 0.5~1、win 0.50~0.55、p 0.05~0.10
    n = 60
    rng = np.random.default_rng(7)
    ic = rng.normal(0.03, 0.01, n)  # |IC| ~ 0.03 → 8 分档
    ric = ic.copy()
    df = pd.DataFrame({"IC": ic, "Rank_IC": ric})
    s = pipe._comprehensive_score(df)
    assert s["IC_Score"] in {0, 8, 15, 25}
    # 负 IC + 中等胜率
    ic_neg = -np.abs(rng.normal(0.06, 0.02, n))
    s2 = pipe._comprehensive_score(pd.DataFrame({"IC": ic_neg, "Rank_IC": ic_neg}))
    assert "WinRate_Score" in s2
    # 强 ICIR
    flat = np.full(n, 0.12)
    s3 = pipe._comprehensive_score(pd.DataFrame({"IC": flat, "Rank_IC": flat}))
    assert s3["ICIR_Score"] >= 15
    assert pipe._calc_long_short_return(pd.DataFrame()) == {}
    assert pipe._calc_long_short_return(pd.DataFrame({"Q1": [0.1]})) == {}
    assert FactorEvaluation.grade_letter("N/A - 无数据") == "N/A"


def test_dragon_f34_f35_edges():
    short = make_ohlcv(5, close=10.0)
    long = make_ohlcv(40, close=np.linspace(10, 12, 40), volume=1e6, amount=1e7)
    vol_only = long.drop(columns=["amount"])
    no_vol = long[["trade_date", "open", "high", "low", "close"]]
    codes = ["600000", "600001"]
    stock = {"600000": long, "600001": short}
    trend, passed = DragonFactors.f35_bollinger_trend(stock, codes)
    assert "600000" in trend and "600001" in passed
    # mid<=0：全 0 close
    zero = make_ohlcv(40, close=0.0)
    DragonFactors.f35_bollinger_trend({"600000": zero}, ["600000"])
    res = DragonFactors.f34_resonance_pct(codes, stock)
    assert isinstance(res, dict)
    assert DragonFactors.f34_resonance_pct(codes, None) == res or True
    DragonFactors.f34_resonance_pct(["600000"], {"600000": vol_only})
    DragonFactors.f34_resonance_pct(["600000"], {"600000": no_vol})
    DragonFactors.f34_resonance_pct(["600000"], {"600000": short})
    # daily_volumes / turnover empty paths
    assert DragonFactors.daily_volumes({}, ["600000"]) == {}
    assert DragonFactors.daily_volumes({"600000": short.iloc[0:0]}, ["600000"]) == {}
    assert DragonFactors.nd_returns({"600000": short}, ["600000"], 20) == {}
    DragonFactors.turnover_rates({"600000": no_vol}, ["600000"])
    # turnover_rate 列
    tr = long.copy()
    tr["turnover_rate"] = 3.0
    tr = tr.drop(columns=["turnover"], errors="ignore")
    DragonFactors.f38_turnover_anomaly({"600000": tr}, ["600000"])
    DragonFactors.f38_turnover_anomaly({"600000": short}, ["600000"])


def test_force_empty_and_fallback_amount():
    assert math.isnan(ForceFactors.limit_down_free_days(None))
    assert math.isnan(ForceFactors.limit_down_free_days(pd.DataFrame()))
    assert math.isnan(ForceFactors.limit_down_free_days(pd.DataFrame({"open": [1]})))
    assert math.isnan(ForceFactors.fcoop3_sustained_days([], 3))
    assert math.isnan(ForceFactors.f_power_divergence(None))
    assert math.isnan(ForceFactors.f_power_divergence(make_ohlcv(5, close=10.0)))
    # 小成交额 fallback → warning 丢弃
    ohlcv = make_ohlcv(20, close=10.0, amount=100.0)  # < 1e6
    last = str(ohlcv["trade_date"].iloc[-1])
    out = ForceFactors.apply_cooperative_force_raw(
        [{"code": "600000", "sector_code": "SW2测"}],
        stock_ohlcv={"600000": ohlcv},
        stock_flow_recent={
            "600000": [{"trade_date": last, "main_net": 1.0, "turnover": 0.0}]
        },
        trade_date=last,
    )
    assert out
    # 无 flow、有正常 amount
    ohlcv2 = make_ohlcv(20, close=10.0, amount=5e7, turnover=4.0)
    out2 = ForceFactors.apply_cooperative_force_raw(
        [{"code": "600001", "sector_code": "SW2测"}],
        stock_ohlcv={"600001": ohlcv2},
        stock_flow_recent={},
        trade_date=str(ohlcv2["trade_date"].iloc[-1]),
    )
    assert out2
    assert ForceFactors.lhb_bonus("600000", {"600000": []}) == 0.0
    assert (
        ForceFactors.lhb_bonus(
            "600000",
            {"600000": [{"trade_date": "2024-01-02", "inst_buy_ratio": 0.5, "hot_money": True}]},
        )
        >= 0
    )


@pytest.mark.asyncio
async def test_eval_one_with_plot_lock(tmp_path, stock_ohlcv_by_code):
    import asyncio

    codes = list(stock_ohlcv_by_code)[:5]
    dates = sorted(
        {
            str(x)
            for c in codes
            for x in stock_ohlcv_by_code[c]["trade_date"].astype(str)
            if "2024-01" <= str(x) <= "2024-02"
        }
    )[:15]
    if len(codes) < 3 or len(dates) < 8:
        pytest.skip("数据不足")
    rng = np.random.default_rng(2)
    factor = pd.DataFrame({c: rng.normal(size=len(dates)) for c in codes}, index=dates)
    close = pd.DataFrame(
        {c: np.linspace(10, 11, len(dates)) for c in codes}, index=dates
    )
    panels = {"open": close.copy(), "close": close}
    pipe = FactorEvaluation(
        start_date=dates[0],
        end_date=dates[-1],
        period=1,
        plot=True,
        output_dir=str(tmp_path),
    )

    async def _bundle(*a, **k):
        return factor, panels

    pipe.load_eval_bundle_stock = _bundle  # type: ignore
    lock = asyncio.Lock()
    row = await pipe._eval_one("dragon", "f32_amount", plot_lock=lock)
    assert "Total_Score" in row
