"""逐项验证 code-review 修复（对齐 / 入场价 / 日历 / 印花税 / snapshot / f35）。"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from zstock.common.config.strategy_config import build_runtime_config
from zstock.strategy_management import market_book as mb
from zstock.strategy_management.strategy_section.exit import f35_map_from_ohlcv
from zstock.strategy_management.market_book import (
    BOND,
    BOND30,
    GOLD,
    INDEX_CODE,
    INDUSTRY_ETFS,
    POLICY,
    MarketBook,
    park_treasury,
)
from zstock.strategy_management.strategy_industry_etf import industry_targets
from zstock.strategy_management.script.backtester import Backtester
from zstock.strategy_management.turnover_controller import TurnoverController
from test.strategy_management.test.conftest import make_offline_pipeline


def _noisy_up_series(n: int = 80) -> list[float]:
    return [
        100.0 * (1.002 ** i) * (1.0 + 0.008 * ((i % 5) - 2)) for i in range(n)
    ]


def test_industry_alignment_with_longer_index():
    n = 80
    closes = {c: [100.0 + (i % 7) * 0.1 for i in range(n)] for c in INDUSTRY_ETFS}
    for c in (BOND, BOND30, GOLD, POLICY):
        closes[c] = [100.0] * n
    closes[INDUSTRY_ETFS[0]] = _noisy_up_series(n)
    closes[INDEX_CODE] = [100.0] * (n + 200)
    closes[INDUSTRY_ETFS[1]] = closes[INDUSTRY_ETFS[1]][:50]
    w = industry_targets(closes, held=[INDUSTRY_ETFS[0]])
    assert INDUSTRY_ETFS[0] in w
    assert abs(sum(w.values()) - 1.0) < 1e-6


def test_defensive_with_uneven_lengths():
    dw = MarketBook()._defensive_targets(
        {
            BOND: [100.0] * 80,
            BOND30: [100.0] * 80,
            GOLD: [100.0 + i * 0.5 for i in range(80)],
            POLICY: [100.0] * 40,
        }
    )
    assert abs(sum(dw.values()) - 1.0) < 1e-6


def test_entry_price_fill_open_and_hard_stop_on_low():
    bt = Backtester(
        strategy_pipeline=make_offline_pipeline(),
        fee_rate=0.0015,
        initial_capital=1e6,
    )
    meta = Backtester._attach_entry_meta(
        pd.DataFrame([{"code": "600000", "weight": 0.5, "score": 1.0}]),
        None,
        {},
        {},
        "2024-01-02",
    )
    assert math.isnan(float(meta.iloc[0]["entry_price"]))

    holdings = pd.DataFrame(
        [
            {
                "code": "600000",
                "weight": 0.5,
                "score": 1.0,
                "entry_price": np.nan,
                "entry_date": "2024-01-02",
            }
        ]
    )
    day = {
        "600000": pd.DataFrame(
            [
                {
                    "trade_date": "2024-01-03",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                }
            ]
        )
    }
    h2, rets, bases, _prices, _n = bt._mark_to_market_and_fill_entries(
        holdings, {}, day, "2024-01-03"
    )
    assert float(h2.iloc[0]["entry_price"]) == pytest.approx(10.0)
    assert rets["600000"] == pytest.approx(0.02)
    assert bases["600000"] == pytest.approx(10.0)

    h_stop = pd.DataFrame(
        [{"code": "600000", "weight": 0.5, "score": 1.0, "entry_price": 10.0}]
    )
    day_stop = {
        "600000": pd.DataFrame(
            [
                {
                    "trade_date": "2024-01-04",
                    "open": 9.5,
                    "high": 9.6,
                    "low": 9.0,
                    "close": 9.3,
                }
            ]
        )
    }
    out, day_ret, _tov, cost, _px = bt._apply_hard_stops(
        h_stop,
        {"600000": 9.3},
        {"600000": -0.07},
        {"600000": 10.0},
        day_stop,
        "2024-01-04",
        -0.08,
    )
    assert out.empty
    assert day_ret == pytest.approx(0.5 * (9.2 / 10.0 - 1.0))
    assert cost == pytest.approx(0.5 * (0.0015 + bt.stamp_tax))


def test_trade_calendar_from_ohlcv_not_weekdays():
    bt = Backtester(
        strategy_pipeline=make_offline_pipeline(),
        fee_rate=0.0015,
        initial_capital=1e6,
    )
    bt._ohlcv_full = {
        INDEX_CODE: pd.DataFrame(
            {
                "trade_date": ["2024-01-02", "2024-01-03", "2024-01-04"],
                "open": [1, 1, 1],
                "high": [1, 1, 1],
                "low": [1, 1, 1],
                "close": [1, 1, 1],
            }
        )
    }
    cal = bt._resolve_trade_dates("2024-01-02", "2024-01-10")
    assert cal == ["2024-01-02", "2024-01-03", "2024-01-04"]
    assert len(Backtester._gen_trade_dates("2024-01-01", "2024-01-07")) == 5


def test_stamp_tax_buy_sell_asymmetry():
    tc = TurnoverController(fee_rate=0.0015, stamp_tax=0.0005)
    buy = pd.DataFrame([{"code": "A", "weight": 1.0}])
    c_buy = tc.estimate_trading_costs(None, buy)
    c_sell = tc.estimate_trading_costs(buy, pd.DataFrame(columns=["code", "weight"]))
    assert c_buy["cost_pct"] == pytest.approx(0.0015)
    assert c_sell["cost_pct"] == pytest.approx(0.0020)


def test_market_book_gold_snapshot_roundtrip():
    book = MarketBook(confirm_green_days=2)
    book._gold_active = "gold"
    book._gold_wish = "gold"
    book._gold_wish_streak = 3
    book._gold_peak = 9.5
    book._gold_cooldown = 4
    book.state = "defense"
    book._last_date = "2024-06-01"
    other = MarketBook()
    other.restore(book.snapshot())
    assert other._gold_active == "gold"
    assert other._gold_wish_streak == 3
    assert other._gold_peak == pytest.approx(9.5)
    assert other._gold_cooldown == 4
    assert other.state == "defense"


def test_book_result_costs_and_deep_merge_and_init_override():
    from zstock.strategy_management.pipeline import StrategyPipeline

    class _Duck:
        async def compute_signals(self, **kwargs):
            return pd.DataFrame()

    # config_override 在 __init__ 早期调用不得炸（早于 turnover_controller 创建）
    pipe2 = StrategyPipeline(
        signals=_Duck(), config_override={"style_switching": False}
    )
    assert pipe2.config.get("style_switching") is False

    pipe = make_offline_pipeline()
    pipe.config = {
        "portfolio": {"max_holdings": 5},
        "final_score": {"top_k": 5},
        "a": {"x": 1, "z": {"n": 1}},
    }
    pipe._apply_config_override(
        {"a": {"z": {"m": 2}}, "portfolio": {"max_weight_per_stock": 0.15}}
    )
    assert pipe.config["a"]["z"]["n"] == 1
    assert pipe.config["a"]["z"]["m"] == 2

    holdings = pd.DataFrame([{"code": BOND, "weight": 1.0, "score": 0.0}])
    br = pipe._book_result(
        "2024-01-02",
        {"market_grade": "red", "signals": pd.DataFrame()},
        "defense",
        holdings,
        1e6,
        current_positions=pd.DataFrame([{"code": "600000", "weight": 0.5}]),
    )
    assert br["results"]["trading_costs"]["cost_pct"] > 0
    br_skip = pipe._book_result(
        "2024-01-02",
        {"market_grade": "yellow", "signals": pd.DataFrame()},
        "neutral",
        holdings,
        1e6,
        current_positions=holdings,
        rebalance_skipped=True,
    )
    assert br_skip["results"]["trading_costs"]["cost_pct"] == 0.0


def test_f35_from_ohlcv_and_runtime_stamp():
    dates = pd.bdate_range("2024-01-01", periods=40)
    closes = np.linspace(10, 12, 40)
    ohlcv = {
        "600000": pd.DataFrame(
            {
                "trade_date": [d.strftime("%Y-%m-%d") for d in dates],
                "open": closes,
                "high": closes * 1.01,
                "low": closes * 0.99,
                "close": closes,
            }
        )
    }
    td = dates[-1].strftime("%Y-%m-%d")
    f35 = f35_map_from_ohlcv(ohlcv, td, {"600000"})
    assert "600000" in f35 and np.isfinite(f35["600000"])

    rt = build_runtime_config()
    assert float(rt["turnover_control"]["stamp_tax"]) == pytest.approx(0.0005)

    parked = park_treasury(
        pd.DataFrame([{"code": "600000", "weight": 0.7, "score": 0.0}])
    )
    assert abs(float(parked["weight"].sum()) - 1.0) < 1e-8


def test_end_i_helper_exported_behavior():
    closes = {"A": [1.0, 2.0, 3.0], "B": [1.0]}
    assert mb._end_i(closes, "A") == 2
    assert mb._end_i(closes, "B") == 0
    assert mb._end_i(closes, "missing") == -1
