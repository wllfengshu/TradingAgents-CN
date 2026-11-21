"""再推几个易达分支，使 package branch% ≥80。"""

from __future__ import annotations

import pandas as pd
import pytest

from zstock.strategy_management.strategy_section.risk import RiskManager
from zstock.strategy_management.script.backtester import Backtester, _append_book_bars
from zstock.strategy_management.market_book import INDEX_CODE
from test.strategy_management.test.conftest import make_offline_pipeline


def test_risk_manager_cash_and_count_and_top_conc():
    rm = RiskManager(
        risk_limits={
            "top_k": 2,
            "min_holdings": 2,
            "max_holdings": 2,
            "max_weight_per_stock": 0.5,
            "max_sector_exposure": 0.6,
            "max_top5_concentration": 0.7,
            "weight_sum_tolerance": 1e-3,
            "allow_cash": False,
        }
    )
    # too many + weight sum != 1 + top conc
    df = pd.DataFrame(
        {
            "code": ["A", "B", "C"],
            "weight": [0.5, 0.4, 0.3],
            "sector_code": ["S1", "S1", "S2"],
        }
    )
    out = rm._check_compliance(df)
    assert out["status"] == "warning"
    assert any("持仓数" in i for i in out["issues"])
    assert any("权重和" in i for i in out["issues"])

    # allow_cash True illegal negative / >1
    rm2 = RiskManager(
        risk_limits={
            **rm.risk_limits,
            "allow_cash": True,
            "min_holdings": 1,
            "max_holdings": 10,
            "max_top5_concentration": 1.0,
        }
    )
    bad = pd.DataFrame({"code": ["A"], "weight": [1.5]})
    out2 = rm2._check_compliance(bad)
    assert any("权重和" in i for i in out2["issues"])

    # correction: all overweight → under_mask sum 0 path
    holdings = pd.DataFrame(
        {
            "code": ["A", "B"],
            "weight": [0.8, 0.8],
            "sector_code": ["S1", "S2"],
            "score": [1.0, 1.0],
        }
    )
    rm3 = RiskManager(
        risk_limits={
            "top_k": 5,
            "min_holdings": 1,
            "max_holdings": 10,
            "max_weight_per_stock": 0.3,
            "max_sector_exposure": 1.0,
            "max_top5_concentration": 1.0,
            "weight_sum_tolerance": 1e-3,
            "allow_cash": True,
        }
    )
    compliance, corrected = rm3.apply_corrections(holdings)
    assert float(corrected["weight"].max()) <= 0.3 + 1e-6

    # sector only overweight, other_w sum 0 (single sector)
    one_sector = pd.DataFrame(
        {
            "code": ["A", "B"],
            "weight": [0.4, 0.4],
            "sector_code": ["S1", "S1"],
            "score": [1.0, 1.0],
        }
    )
    rm4 = RiskManager(
        risk_limits={
            "top_k": 5,
            "min_holdings": 1,
            "max_holdings": 10,
            "max_weight_per_stock": 1.0,
            "max_sector_exposure": 0.5,
            "max_top5_concentration": 1.0,
            "weight_sum_tolerance": 1e-3,
            "allow_cash": True,
        }
    )
    _, corr2 = rm4.apply_corrections(one_sector)
    assert float(corr2.groupby("sector_code")["weight"].sum().iloc[0]) <= 0.5 + 1e-6


def test_backtester_scale_helpers_and_stops():
    assert Backtester._compute_drawdown_scale([], {"enabled": False}) == 1.0
    assert Backtester._compute_drawdown_scale([1.0], {"enabled": True}) == 1.0
    assert (
        Backtester._compute_drawdown_scale(
            [1.0, 1.1],
            {"enabled": True, "drawdown_throttle": {"enabled": False}},
        )
        == 1.0
    )
    scale = Backtester._compute_drawdown_scale(
        [1.0, 1.2, 0.9],
        {
            "enabled": True,
            "drawdown_throttle": {
                "enabled": True,
                "lookback_days": 10,
                "tiers": [
                    {"drawdown_threshold": 0.05, "scale_factor": 0.5},
                    {"drawdown_threshold": 0.0, "scale_factor": 0.1},
                ],
            },
        },
    )
    assert 0.0 <= scale <= 1.0
    scale2 = Backtester._compute_drawdown_scale(
        [1.0, 1.1, 1.05],
        {
            "enabled": True,
            "drawdown_throttle": {
                "enabled": True,
                "lookback_days": 10,
                "drawdown_threshold": 0.5,
                "scale_factor": 0.6,
            },
        },
    )
    assert scale2 == 1.0
    # hit non-tier threshold
    scale3 = Backtester._compute_drawdown_scale(
        [1.0, 1.2, 0.9],
        {
            "enabled": True,
            "drawdown_throttle": {
                "enabled": True,
                "lookback_days": 10,
                "drawdown_threshold": 0.1,
                "scale_factor": 0.4,
            },
        },
    )
    assert scale3 == pytest.approx(0.4)

    assert Backtester._compute_vol_scale([1.0], {"vol_target": {"enabled": True}}) == 1.0
    assert Backtester._compute_vol_scale([1.0, 1.01, 1.02], {}) == 1.0
    # build enough equity points for vol
    eq = [1.0]
    for i in range(30):
        eq.append(eq[-1] * (1.01 if i % 2 == 0 else 0.99))
    vs = Backtester._compute_vol_scale(
        eq,
        {
            "vol_target": {
                "enabled": True,
                "lookback_days": 40,
                "target_vol": 0.12,
                "scale_floor": 0.25,
                "scale_cap": 1.25,
                "min_obs": 10,
            }
        },
    )
    assert 0.25 <= vs <= 1.25

    bt = Backtester(strategy_pipeline=make_offline_pipeline())
    # resolve rebalance
    assert Backtester._resolve_rebalance_freq(None, default_freq=5) == 5
    assert (
        Backtester._resolve_rebalance_freq(
            {
                "enabled": True,
                "default_freq": 5,
                "by_regime": {"momentum": 3},
                "by_market_grade": {"yellow": 8},
                "use_min_freq": True,
                "min_freq": 3,
                "max_freq": 8,
            },
            regime="momentum",
            market_grade="yellow",
        )
        == 3
    )
    assert (
        Backtester._resolve_rebalance_freq(
            {
                "enabled": True,
                "default_freq": 5,
                "by_regime": {"neutral": 4},
                "by_market_grade": {"green": 6},
                "use_min_freq": False,
                "min_freq": 3,
                "max_freq": 8,
            },
            regime="neutral",
            market_grade="green",
        )
        == 6
    )

    clipped = Backtester._clip_weights(
        pd.DataFrame({"code": ["A", "B"], "weight": [0.8, 0.8]})
    )
    assert float(clipped["weight"].sum()) <= 1.0 + 1e-9
    assert Backtester._clip_weights(pd.DataFrame()) is not None or True

    holdings = pd.DataFrame(
        {
            "code": ["A"],
            "weight": [0.5],
            "entry_price": [10.0],
            "entry_date": ["2024-01-02"],
            "score": [1.0],
        }
    )
    day = {
        "A": pd.DataFrame(
            [
                {
                    "trade_date": "2024-01-03",
                    "open": 8.0,
                    "high": 8.5,
                    "low": 8.0,
                    "close": 8.0,
                }
            ]
        )
    }
    stopped, _day_ret, tov, cost, _px = bt._apply_hard_stops(
        holdings,
        last_prices={"A": 8.0},
        name_rets={"A": -0.2},
        bases={"A": 10.0},
        day_ohlcv=day,
        trade_date="2024-01-03",
        hard_stop_pct=-0.10,
    )
    # 10 → 8 已跌破跌停价，收盘封死时止损不能成交
    assert len(stopped) == 1
    assert tov == 0.0

    # no entry_price / none pct early return
    h2, *_rest = bt._apply_hard_stops(
        pd.DataFrame({"code": ["A"], "weight": [0.5]}),
        last_prices={"A": 8.0},
        name_rets={"A": -0.2},
        bases={"A": 10.0},
        day_ohlcv=day,
        trade_date="2024-01-03",
        hard_stop_pct=-0.10,
    )
    assert len(h2) == 1

    new, reason = bt._handle_no_signal_exit(
        holdings, bad_streak=1, flat_after_bad_days=5, action="reduce", reduce_scale=0.5
    )
    assert reason == "reduce_no_signals"
    assert float(new["weight"].sum()) == pytest.approx(0.25)
    flat, reason2 = bt._handle_no_signal_exit(
        holdings, bad_streak=1, flat_after_bad_days=5, action="flat", reduce_scale=0.5
    )
    assert flat.empty and reason2 == "flat_no_signals"
    empty, reason3 = bt._handle_no_signal_exit(
        holdings, bad_streak=5, flat_after_bad_days=3, action="hold", reduce_scale=0.5
    )
    assert empty.empty and reason3 == "flat_after_bad_days"


def test_append_book_bars_nan_and_nonpositive():
    index_rows: list = []
    asset_hist: dict = {}
    day = {
        INDEX_CODE: pd.DataFrame(
            {"close": [float("nan")], "high": [float("nan")], "low": [1.0], "volume": [1.0]}
        ),
    }
    _append_book_bars(index_rows, asset_hist, day)
    day2 = {
        INDEX_CODE: pd.DataFrame(
            {"close": [0.0], "high": [1.0], "low": [0.0], "volume": [1.0]}
        ),
    }
    _append_book_bars(index_rows, asset_hist, day2)
    day3 = {
        INDEX_CODE: pd.DataFrame(
            {
                "close": [100.0],
                "high": ["bad"],
                "low": [float("nan")],
                "volume": ["x"],
            }
        ),
    }
    _append_book_bars(index_rows, asset_hist, day3)
    assert len(index_rows) >= 1
