"""market_book：纯函数边角分支与输入组装。"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from zstock.strategy_management import market_book as mb
from zstock.strategy_management.market_book import (
    BOND,
    BOND30,
    GOLD,
    INDUSTRY_ETFS,
    MarketBook,
    build_asset_closes,
    build_index_bars,
    compose_book_inputs,
    holdings_frame,
    needs_book_trade,
    park_treasury,
    positions_to_weights_frame,
    weight_map,
)
from zstock.strategy_management.strategy_industry_etf import bond_code, industry_targets


def test_window_sma_trailing_slope_vol():
    assert mb._window([1, 2, 3], 1, 3) is None
    assert mb._window([1, 2, 0], 2, 3) is None
    assert mb._window([1.0, 2.0, 3.0], 2, 3) == [1.0, 2.0, 3.0]
    assert mb._sma([1.0, 2.0, 3.0], 2, 3) == pytest.approx(2.0)
    assert mb._trailing_return([1.0, 1.1, 1.2], 2, 2) == pytest.approx(0.2)
    assert mb._trailing_return([1.0, 1.1], 0, 2) is None
    slope, r2 = mb._slope_r2([100.0, 101.0, 102.5, 104.0, 106.0])
    assert slope > 0 and r2 >= 0
    assert mb._momentum_score([100.0, 99.0, 98.0]) == 0.0
    assert mb._momentum_score([100.0, 101.0, 103.0, 106.0, 110.0]) >= 0.0
    assert mb._annual_vol([0.01]) == 0.0
    assert mb._annual_vol([0.01, -0.01, 0.02]) > 0
    assert mb._portfolio_vol([], []) == 0.0
    assert mb._portfolio_vol([[0.01], [0.02]], [0.5, 0.5]) == 0.0
    vol = mb._portfolio_vol([[0.01, 0.02, -0.01], [0.0, 0.01, 0.01]], [0.6, 0.4])
    assert vol >= 0


def test_cap_pick_ready_bond_gold():
    capped = mb._cap_weights({"a": 0.6, "b": 0.3, "c": 0.1}, 0.4)
    assert capped["a"] == pytest.approx(0.4)
    assert abs(sum(capped.values()) - 1.0) < 1e-8
    ranked = [(0.9, "x"), (0.8, "y"), (0.7, "z"), (0.6, "w")]
    picked = mb._pick_codes(ranked, held=["z", "w"])
    assert len(picked) <= mb.TOP_N
    assert "z" in picked or "x" in picked
    closes = {BOND: [1.0] * 80, "FALL": [1.0] * 80}
    assert mb._ready(closes, BOND, 70)
    assert not mb._ready(closes, "MISSING", 10)
    assert bond_code(closes, 70) == BOND
    assert bond_code({}, 0) != BOND or bond_code({}, 0) == mb.BOND_FALLBACK
    # 上升趋势：close>MA20 且 MA20>MA60 → 允许黄金
    up = [100.0 + i * 0.5 for i in range(80)]
    assert mb._gold_sleeve_ok(up, 79) is True
    assert mb._gold_sleeve_ok([1.0] * 5, 2) is False
    # 下跌后反弹仅站上 MA20、MA20 仍低于 MA60 → 禁止黄金
    down = [200.0 - i * 0.8 for i in range(70)]
    bounce = down + [down[-1] * 1.02, down[-1] * 1.03, down[-1] * 1.04]
    assert mb._gold_sleeve_ok(bounce, len(bounce) - 1) is False
    # defensive：上升选金；下跌反弹落政金债
    n = 80
    up_closes = {
        BOND: [100.0] * n,
        BOND30: [100.0] * n,
        GOLD: [100.0 + i * 0.4 for i in range(n)],
        mb.POLICY: [100.0] * n,
    }
    assert GOLD in MarketBook()._defensive_targets(up_closes)
    bear_closes = {
        BOND: [100.0] * n,
        BOND30: [100.0] * n,
        GOLD: bounce[-n:] if len(bounce) >= n else bounce,
        mb.POLICY: [100.0] * n,
    }
    # pad bear gold to n if needed
    g = list(bear_closes[GOLD])
    if len(g) < n:
        g = [g[0]] * (n - len(g)) + g
    bear_closes[GOLD] = g[-n:]
    dw_bear = MarketBook()._defensive_targets(bear_closes)
    assert GOLD not in dw_bear
    assert abs(dw_bear[mb.POLICY] - 0.40) < 1e-8
    assert abs(dw_bear[BOND] - 0.60) < 1e-8
    bank_up = {
        BOND: [100.0] * n,
        BOND30: [100.0] * n,
        GOLD: list(bear_closes[GOLD]),
        "512800": [100.0 + i * 0.4 for i in range(n)],
        mb.POLICY: [100.0] * n,
    }
    dw_bank = MarketBook()._defensive_targets(bank_up)
    assert abs(dw_bank["512800"] - 0.30) < 1e-8
    assert abs(dw_bank[mb.POLICY] - 0.40) < 1e-8
    assert abs(dw_bank[BOND] - 0.30) < 1e-8
    assert GOLD not in dw_bank
    both = dict(bank_up)
    both[GOLD] = [100.0 + i * 0.4 for i in range(n)]
    dw_both = MarketBook()._defensive_targets(both)
    assert abs(dw_both[GOLD] - 0.30) < 1e-8
    assert abs(dw_both["512800"] - 0.30) < 1e-8
    assert abs(dw_both[mb.POLICY] - 0.40) < 1e-8
    assert BOND not in dw_both


def test_industry_and_defense_edge(tmp_path):
    n = 80
    closes = {code: [100.0 + (i % 7) * 0.1 for i in range(n)] for code in INDUSTRY_ETFS}
    for code in (BOND, BOND30, GOLD, mb.POLICY):
        closes[code] = [100.0] * n
    # 一只带波动的上行 ETF（纯指数增长日波动≈0 会被 vol 过滤踢掉）
    closes[INDUSTRY_ETFS[0]] = [
        100.0 * (1.002 ** i) * (1.0 + 0.008 * ((i % 5) - 2)) for i in range(n)
    ]
    w = industry_targets(closes, held=[INDUSTRY_ETFS[0]])
    assert abs(sum(w.values()) - 1.0) < 1e-6
    assert INDUSTRY_ETFS[0] in w

    # 指数历史远长于 ETF 时，不得把全部行业 ETF 踢掉退化成纯债
    closes_long_idx = dict(closes)
    closes_long_idx[mb.INDEX_CODE] = [100.0] * (n + 200)
    w2 = industry_targets(closes_long_idx, held=[INDUSTRY_ETFS[0]])
    assert INDUSTRY_ETFS[0] in w2
    assert abs(sum(w2.values()) - 1.0) < 1e-6

    # short history → empty-ish defensive
    short = {BOND: [100.0] * 5, GOLD: [100.0] * 5}
    dw = MarketBook()._defensive_targets(short)
    assert isinstance(dw, dict)


def test_regime_drawdown_grade_holdings():
    off, streak = MarketBook()._regime_step([100.0] * 5, 4, False, 0)
    assert isinstance(off, bool)
    # rising market
    up = [100.0 + i for i in range(60)]
    off2, streak2 = MarketBook()._regime_step(up, 50, False, 2)
    assert isinstance(streak2, int)

    # 从峰值回撤 ≥ DD_STOP → 上锁
    locked, lock_age, peak = MarketBook()._drawdown_step(
        bench=[100.0] * 40, i=30, nav=85.0, peak=100.0, dd_lock=False, lock_age=0
    )
    assert locked is True
    locked2, lock_age2, peak2 = MarketBook()._drawdown_step(
        bench=[100.0 + i * 0.5 for i in range(80)],
        i=70,
        nav=100.0,
        peak=100.0,
        dd_lock=True,
        lock_age=20,
    )
    assert isinstance(locked2, bool) and peak2 > 0

    hf = holdings_frame({})
    assert BOND in set(hf["code"])
    hf2 = holdings_frame({"600000": 0.4, "600001": 0.6})
    assert len(hf2) == 2
    assert park_treasury(None)["code"].iloc[0] == BOND
    over = park_treasury(pd.DataFrame({"code": ["A"], "weight": [1.5]}))
    assert float(over["weight"].sum()) == pytest.approx(1.0)
    assert weight_map(None) == {}
    assert weight_map(hf2)["600000"] == pytest.approx(0.4)
    # 利用率 < MIN_UTILIZATION(0.90) 也会触发调仓
    assert needs_book_trade({"A": 0.95}, {"A": 0.95}, band=0.1) is False
    assert needs_book_trade({"A": 0.5}, {"A": 0.5}, band=0.1) is True
    assert needs_book_trade({"A": 0.1}, {"B": 0.9}, band=0.05) is True


def test_build_bars_closes_compose_positions():
    idx = pd.DataFrame(
        {
            "trade_date": ["2024-01-02", "2024-01-03", "2024-01-04"],
            "open": [1, 2, 3],
            "high": [1, 2, 3],
            "low": [1, 2, 3],
            "close": [10.0, 11.0, 12.0],
            "volume": [1, 1, 1],
        }
    )
    bars = build_index_bars(idx)
    assert len(bars) == 3
    # build_asset_closes 只收集 BOOK_OHLCV_CODES
    ohlcv = {
        "399300": idx,
        BOND: pd.DataFrame(
            {"trade_date": ["2024-01-02", "2024-01-03"], "close": [100.0, 100.1]}
        ),
        "512480": pd.DataFrame(
            {"trade_date": ["2024-01-02", "2024-01-03"], "close": [1.0, 1.1]}
        ),
    }
    closes = build_asset_closes(ohlcv, "2024-01-03")
    assert BOND in closes or "512480" in closes or "399300" in closes
    inputs = compose_book_inputs("2024-01-03", ohlcv)
    assert inputs.trade_date
    wf = positions_to_weights_frame(
        [{"code": "600000", "market_value": 200_000}, {"code": "600001", "market_value": 300_000}],
        1_000_000,
    )
    assert abs(float(wf["weight"].sum()) - 0.5) < 1e-9


def test_market_book_prepare_and_step_branches():
    book = MarketBook(confirm_green_days=2)
    book.prepare_for_date("2024-01-02")
    d1 = book.step("2024-01-02", "green", index_closes=[100.0] * 40, nav=1e6)
    assert d1.state in ("neutral", "attack", "defense")
    d2 = book.step("2024-01-03", "green", index_closes=[100.0 + i * 0.1 for i in range(40)], nav=1e6)
    assert d2.state in ("neutral", "attack", "defense")
    d3 = book.step(
        "2024-01-04",
        "yellow",
        index_closes=[100.0] * 40,
        nav=1e6,
        closes={c: [100.0] * 40 for c in list(INDUSTRY_ETFS)[:3] + [BOND, GOLD, BOND30]},
    )
    assert d3.state == "neutral"

