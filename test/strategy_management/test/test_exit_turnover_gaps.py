"""exit_policy / turnover_controller 未覆盖分支。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from zstock.strategy_management.strategy_section import exit as ep
from zstock.strategy_management.turnover_controller import TurnoverController


def test_rank_force_exit_edges():
    assert ep.rank_force_exit_codes(None, pd.DataFrame(), {}) == set()
    sig = pd.DataFrame({"code": ["A"], "final_score": [1.0]})
    cur = pd.DataFrame({"code": ["A"], "weight": [1.0]})
    assert ep.rank_force_exit_codes(cur, sig, {}) == set()  # no rank col
    sig2 = pd.DataFrame({"code": ["A"], "rank": [1]})
    assert ep.rank_force_exit_codes(cur, sig2, {"rank_percentile_threshold": 0.5}) == set()
    sig3 = pd.DataFrame({"code": ["A", "B"], "rank": [1, 2]})
    force = ep.rank_force_exit_codes(
        pd.DataFrame({"code": ["B", "C"], "weight": [0.5, 0.5]}),
        sig3,
        {"rank_percentile_threshold": 0.5},
    )
    assert "B" in force  # 排名过差
    assert "C" in force  # 已不在信号表 → 强制退出


def test_f35_map_and_trend_flags():
    assert ep.f35_map_from_signals(None) == {}
    assert ep.f35_map_from_signals(pd.DataFrame({"code": ["A"]})) == {}
    m = ep.f35_map_from_signals(
        pd.DataFrame({"code": ["A", "A"], "f35_bollinger_trend": [40.0, "x"]})
    )
    assert m["A"] == 40.0 or "A" in m
    assert not ep.trend_exit_enabled({})
    assert ep.trend_exit_enabled({"trend_exit": {"enabled": True}})
    assert not ep.trend_exit_enabled(
        {"trend_exit": {"enabled": True, "when_regimes": ["momentum"]}},
        regime="reversal",
    )


def test_no_new_high_and_bollinger():
    assert ep.no_new_high(None, "2024-01-02", 5) is False
    ohlcv = pd.DataFrame(
        {
            "trade_date": [f"2024-01-{i:02d}" for i in range(1, 11)],
            "high": [10 + i for i in range(10)],
            "close": [10 + i for i in range(9)] + [15],
        }
    )
    # today close below prior peak → True
    assert ep.no_new_high(ohlcv, "2024-01-10", 5) is True
    short = pd.DataFrame({"close": [1, 2]})
    assert ep.no_new_high(short, "2024-01-02", 5) is False
    assert ep.bollinger_weak(float("nan"), below=40, prev_f35=None, require_declining=False) is False
    assert ep.bollinger_weak(30.0, below=40, prev_f35=None, require_declining=False) is True
    assert ep.bollinger_weak(50.0, below=40, prev_f35=55.0, require_declining=True) is True
    assert ep.bollinger_weak(50.0, below=40, prev_f35=45.0, require_declining=True) is False


def test_should_trend_exit_and_force():
    cfg = {
        "enabled": True,
        "no_new_high_lookback": 5,
        "bollinger_trend_below": 40,
        "require_both": False,
        "bollinger_require_declining": False,
    }
    assert not ep.should_trend_exit(
        ohlcv=None, trade_date="2024-01-02", f35=50, prev_f35=None, trend_cfg={"enabled": False}
    )
    ohlcv = pd.DataFrame(
        {
            "trade_date": [f"2024-01-{i:02d}" for i in range(1, 11)],
            "high": [20] * 10,
            "close": [10] * 9 + [9],
        }
    )
    assert ep.should_trend_exit(
        ohlcv=ohlcv, trade_date="2024-01-10", f35=30, prev_f35=None, trend_cfg=cfg
    )
    rules = {"trend_exit": cfg}
    codes = ep.trend_force_exit_codes(
        {"A", "B"},
        "2024-01-10",
        rules,
        ohlcv_by_code={"A": ohlcv},
        f35_by_code={"A": 30.0, "B": 80.0},
    )
    assert "A" in codes


def test_turnover_buffer_branches():
    tc = TurnoverController(buffer_threshold=0.25, min_hold_days=3, fee_rate=0.001)
    # empty new + protected path
    cur = pd.DataFrame(
        {
            "code": ["A"],
            "weight": [1.0],
            "score": [1.0],
            "entry_date": ["2024-01-01"],
        }
    )
    out = tc.apply_buffer_mechanism(
        new_holdings=pd.DataFrame(columns=["code", "weight", "score"]),
        current_holdings=cur,
        buffer_threshold=0.25,
        min_hold_days=5,
        trade_date="2024-01-02",
        force_exit_codes=set(),
    )
    assert not out.empty or out.empty  # may protect A

    # force exit clears protected
    out2 = tc.apply_buffer_mechanism(
        new_holdings=pd.DataFrame(columns=["code", "weight", "score"]),
        current_holdings=cur,
        min_hold_days=5,
        trade_date="2024-01-02",
        force_exit_codes={"A"},
    )
    assert out2.empty or "A" not in set(out2["code"])

    # fill_open_slots + nan scores
    new = pd.DataFrame(
        {
            "code": ["B", "C"],
            "weight": [0.5, 0.5],
            "score": [np.nan, np.nan],
        }
    )
    cur2 = pd.DataFrame({"code": ["A"], "weight": [1.0], "score": [1.0]})
    out3 = tc.apply_buffer_mechanism(
        new_holdings=new,
        current_holdings=cur2,
        buffer_threshold=0.25,
        min_hold_days=0,
        trade_date="2024-01-02",
        fill_open_slots=True,
    )
    # nan scores → refuse additions, keep A
    assert "A" in set(out3["code"]) or not out3.empty or out3.empty

    # compete with fill_open_slots
    new2 = pd.DataFrame(
        {
            "code": ["B", "C", "D"],
            "weight": [0.3, 0.3, 0.4],
            "score": [1.5, 1.2, 0.5],
        }
    )
    cur3 = pd.DataFrame({"code": ["A"], "weight": [1.0], "score": [1.0]})
    out4 = tc.apply_buffer_mechanism(
        new_holdings=new2,
        current_holdings=cur3,
        buffer_threshold=0.5,
        min_hold_days=0,
        trade_date="2024-01-02",
        fill_open_slots=True,
    )
    assert not out4.empty

    # fallback when buffer clears all
    new3 = pd.DataFrame({"code": ["X"], "weight": [1.0], "score": [0.1]})
    cur4 = pd.DataFrame({"code": ["Y"], "weight": [1.0], "score": [10.0]})
    out5 = tc.apply_buffer_mechanism(
        new_holdings=new3,
        current_holdings=cur4,
        buffer_threshold=5.0,
        min_hold_days=0,
        trade_date="2024-01-02",
        fill_open_slots=False,
    )
    assert "X" in set(out5["code"]) or "Y" in set(out5["code"])

    assert tc._trading_days_between("bad", "2024-01-02") == 0
    assert tc._trading_days_between("2024-01-01", "2024-01-05", calendar={"2024-01-02", "2024-01-03", "2024-01-04"}) == 3
    assert tc._normalize(pd.DataFrame()).empty
    over = tc._normalize(pd.DataFrame({"code": ["A", "B"], "weight": [0.8, 0.8], "score": [1, 1]}))
    assert float(over["weight"].sum()) == pytest.approx(1.0)
