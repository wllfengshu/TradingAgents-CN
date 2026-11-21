"""OrderManagementPipeline：失败路径与异常。"""

from __future__ import annotations

import pandas as pd
import pytest

from zstock.order_management.broker import XtQuantExecutor
from zstock.order_management.orders import OrderGenerator, TradeSettlement
from zstock.order_management.pipeline import OrderManagementPipeline
from zstock.order_management.timing import ExecutionStrategy
from test.order_management.test.conftest import FakeQMT


def _pipe(price_map, fake_db, capital=10_000_000):
    exe = XtQuantExecutor(
        qmt_util=FakeQMT(price_map, positions=[], capital=capital),
        allow_mock=True,
    )
    exe._db_service = fake_db
    return OrderManagementPipeline(
        order_generator=OrderGenerator(),
        xtquant_executor=exe,
        trade_settlement=TradeSettlement(),
        execution_strategy=ExecutionStrategy(twap_interval_seconds=0),
        allow_mock=True,
        database_service=fake_db,
    )


@pytest.mark.asyncio
async def test_pipeline_all_submits_fail(
    target_holdings, real_price_map, fake_db, initial_capital, monkeypatch
):
    pipe = _pipe(real_price_map, fake_db, capital=initial_capital)

    async def _fail(order):
        return None

    monkeypatch.setattr(pipe._xtquant_executor, "submit_order", _fail)
    out = await pipe.execute_full_pipeline(
        target_holdings,
        current_positions=pd.DataFrame(),
        trade_date="2024-01-02",
        price_map=real_price_map,
        total_capital=initial_capital,
        strategy="twap_buy",
    )
    assert out["status"] == "failed"
    assert out["statistics"]["orders_failed"] >= 1


@pytest.mark.asyncio
async def test_pipeline_generate_exception(
    target_holdings, real_price_map, fake_db, initial_capital, monkeypatch
):
    pipe = _pipe(real_price_map, fake_db, capital=initial_capital)

    def boom(*a, **k):
        raise RuntimeError("gen boom")

    monkeypatch.setattr(pipe._order_generator, "generate_orders", boom)
    out = await pipe.execute_full_pipeline(
        target_holdings,
        current_positions=pd.DataFrame(),
        trade_date="2024-01-02",
        price_map=real_price_map,
        total_capital=initial_capital,
        strategy="twap_buy",
    )
    assert out["status"] == "failed"
    assert "boom" in out["error"]


@pytest.mark.asyncio
async def test_pipeline_codes_from_frame_edges():
    assert OrderManagementPipeline._codes_from_frame(None) == []
    assert OrderManagementPipeline._codes_from_frame(pd.DataFrame()) == []
    assert OrderManagementPipeline._codes_from_frame(
        pd.DataFrame({"code": ["603201", None, ""]})
    ) == ["603201"]
