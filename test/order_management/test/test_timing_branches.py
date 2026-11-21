"""ExecutionStrategy：时段分支与 TWAP 失败路径。"""

from __future__ import annotations

from datetime import time as time_obj
from types import SimpleNamespace

import pytest

from zstock.common.entity.order_entity import Order
from zstock.order_management.timing import ExecutionStrategy


class StubExec:
    def __init__(self, fail_ids=None):
        self.fail_ids = set(fail_ids or [])
        self.submitted = []

    async def submit_order(self, order):
        self.submitted.append(order)
        return order.order_id not in self.fail_ids


@pytest.mark.parametrize(
    "hhmm,expected",
    [
        ((9, 18), "auction_sell"),
        ((9, 27), "closed"),
        ((10, 0), "twap_buy"),
        ((12, 0), "closed"),
        ((14, 58), "final"),
        ((15, 5), "closed"),
    ],
)
def test_get_current_strategy_windows(monkeypatch, hhmm, expected):
    strat = ExecutionStrategy(twap_interval_seconds=0)
    monkeypatch.setattr(strat, "_now_local", lambda: time_obj(hhmm[0], hhmm[1]))
    assert strat.get_current_strategy() == expected


@pytest.mark.asyncio
async def test_auction_forces_limit_and_defers_buys():
    strat = ExecutionStrategy(twap_interval_seconds=0)
    sell = Order("s1", "603201", "sell", 100, price_type="market", price=15.0)
    buy = Order("b1", "000060", "buy", 100, price_type="limit", price=4.0)
    stub = StubExec()
    report = await strat.execute_with_strategy([sell, buy], stub, strategy="auction_sell")
    assert sell.price_type == "limit"
    assert report["submitted"] == 1
    assert report["deferred"] == 1
    assert strat.execution_stats["auction_sell_count"] == 1


@pytest.mark.asyncio
async def test_twap_sell_stat_and_batch_failure():
    strat = ExecutionStrategy(twap_slices=2, twap_interval_seconds=0)
    sell = Order("s2", "601107", "sell", 200, price_type="limit", price=3.6)
    buy = Order("b2", "603201", "buy", 200, price_type="limit", price=15.0)
    stub = StubExec(fail_ids={"b2_B0"})
    report = await strat.execute_with_strategy([sell, buy], stub, strategy="twap_buy")
    assert strat.execution_stats["twap_sell_count"] == 1
    assert report["submitted"] >= 1
    assert report["failed"] >= 1


def test_lot_batches_edges():
    strat = ExecutionStrategy()
    assert strat._lot_batches(500, 5) == [100, 100, 100, 100, 100]
    assert strat._lot_batches(50, 5) == []
    assert strat._lot_batches(300, 1) == [300]
