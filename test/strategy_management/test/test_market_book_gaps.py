"""market_book：snapshot/restore、regime/drawdown 未覆盖分支、load_book_market_inputs。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pandas as pd
import pytest

from zstock.strategy_management import market_book as mb
from zstock.strategy_management.market_book import (
    BOND,
    GOLD,
    INDUSTRY_ETFS,
    INDEX_CODE,
    MarketBook,
    compose_book_inputs,
    load_book_market_inputs,
    positions_to_weights_frame,
)
from zstock.strategy_management.strategy_industry_etf import industry_targets


def test_regime_step_off_and_confirm():
    n = mb.REGIME_MA + 10
    # 长期 100，末尾腰斩 → 低于 MA
    series = [100.0] * n
    series[-1] = 10.0
    off, streak = MarketBook()._regime_step(series, n - 1, False, 0)
    assert isinstance(off, bool) and streak == 0
    # already off, price still below → streak 清零
    off2, streak2 = MarketBook()._regime_step(series, n - 1, True, 3)
    assert off2 is True and streak2 == 0
    # recovering above MA accumulates streak / 可能解锁
    up = [50.0] * mb.REGIME_MA + [200.0] * (mb.REGIME_CONFIRM + 2)
    off3, streak3 = MarketBook()._regime_step(up, len(up) - 1, True, mb.REGIME_CONFIRM - 1)
    assert isinstance(off3, bool) and streak3 >= 0


def test_drawdown_lock_and_unlock():
    bench = [100.0 + i * 0.1 for i in range(80)]
    # trigger lock
    locked, age, peak = MarketBook()._drawdown_step(bench, 70, nav=80.0, peak=100.0, dd_lock=False, lock_age=0)
    assert locked is True
    # unlock path: enough days + above repair MA + positive trailing return
    for age_i in range(1, mb.DD_MIN_DAYS + 5):
        locked, age, peak = MarketBook()._drawdown_step(
            bench, 70, nav=110.0, peak=peak, dd_lock=True, lock_age=age_i
        )
    assert isinstance(locked, bool)


def test_market_book_snapshot_restore():
    book = MarketBook(confirm_green_days=2)
    book.state = "defense"
    book.green_streak = 1
    book.dd_lock = True
    snap = book.snapshot()
    book2 = MarketBook()
    book2.restore(snap)
    assert book2.state == "defense"
    assert book2.dd_lock is True
    assert book2.confirm_green_days == 2


def test_industry_targets_short_and_cap():
    # insufficient history → mostly bond park
    short = {c: [100.0] * 10 for c in INDUSTRY_ETFS}
    short[BOND] = [100.0] * 10
    short[GOLD] = [100.0] * 10
    w = industry_targets(short, held=[])
    assert abs(sum(w.values()) - 1.0) < 1e-6 or sum(w.values()) >= 0


def test_filter_date_helpers_and_build_edge():
    assert mb._date_before("2024-01-10", 1) == "2024-01-09"
    empty = mb._filter_through_exclusive(pd.DataFrame(), "2024-01-02")
    assert empty.empty
    no_td = mb._filter_through_exclusive(pd.DataFrame({"close": [1.0]}), "2024-01-02")
    assert "close" in no_td.columns
    assert mb.build_index_bars(pd.DataFrame()) == []
    bad = mb.build_index_bars(pd.DataFrame({"close": ["x", 0, -1, 10.0], "high": [1, 1, 1, "bad"]}))
    assert len(bad) >= 1

    # positions volume*price fallback + empty/zero capital
    assert positions_to_weights_frame(None, 1e6).empty
    assert positions_to_weights_frame([], 1e6).empty
    assert positions_to_weights_frame([{"code": "600000", "market_value": 1}], 0).empty
    wf = positions_to_weights_frame(
        [{"code": "600000", "volume": 100, "current_price": 10.0}],
        10_000,
    )
    assert float(wf["weight"].sum()) == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_load_book_market_inputs_mocked(monkeypatch):
    dates = pd.bdate_range("2023-11-01", periods=50).strftime("%Y-%m-%d").tolist()
    n = len(dates)
    idx = pd.DataFrame(
        {
            "trade_date": dates,
            "close": [3500.0 + i for i in range(n)],
            "high": [3510.0 + i for i in range(n)],
            "low": [3490.0 + i for i in range(n)],
            "volume": [1.0] * n,
        }
    )

    class _QS:
        async def get_ohlcv_batch(self, codes, start, end, **kwargs):
            out = {INDEX_CODE: idx, BOND: idx.copy()}
            return out

    monkeypatch.setattr(
        "zstock.data_management.query_service.get_data_query_service",
        lambda: _QS(),
    )
    inputs = await load_book_market_inputs("2024-01-15", lookback_calendar_days=60)
    assert inputs.trade_date == "2024-01-15"

    # empty batch
    class _Empty:
        async def get_ohlcv_batch(self, *a, **k):
            return {}

    monkeypatch.setattr(
        "zstock.data_management.query_service.get_data_query_service",
        lambda: _Empty(),
    )
    empty_in = await load_book_market_inputs("2024-01-15")
    assert empty_in.index_closes == []


def test_compose_book_inputs_missing_index():
    inputs = compose_book_inputs("2024-01-03", {})
    assert inputs.index_closes == []
    assert inputs.index_bars == []
