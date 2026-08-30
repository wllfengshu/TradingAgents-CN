"""执行管道：注入纸面 QMT / FakeDB，目标持仓来自真实截面。"""

import pandas as pd
import pytest

from zstock.order_management.execution_strategy import ExecutionStrategy
from zstock.order_management.order_generator import OrderGenerator
from zstock.order_management.pipeline import OrderManagementPipeline
from zstock.order_management.trade_settlement import TradeSettlement
from zstock.order_management.xtquant_executor import XtQuantExecutor
from zstock.order_management.test.conftest import FakeQMT, make_position


def _pipe(price_map, fake_db, positions=None, capital=10_000_000, allow_mock=True, mock_mode=None):
    gen = OrderGenerator()
    gen._database_service = fake_db
    exe = XtQuantExecutor(
        qmt_util=FakeQMT(price_map, positions=positions or [], capital=capital),
        allow_mock=allow_mock,
    )
    exe._db_service = fake_db
    if mock_mode is not None:
        exe.mock_mode = mock_mode
    return OrderManagementPipeline(
        order_generator=gen,
        xtquant_executor=exe,
        trade_settlement=TradeSettlement(),
        execution_strategy=ExecutionStrategy(twap_interval_seconds=0),
        allow_mock=allow_mock,
    )


@pytest.mark.asyncio
async def test_empty_target_is_no_target(real_price_map, fake_db):
    pipe = _pipe(real_price_map, fake_db)
    out = await pipe.execute_full_pipeline(pd.DataFrame())
    assert out["status"] == "no_target"


@pytest.mark.asyncio
async def test_mock_without_allow_is_failed(real_price_map, fake_db, target_holdings):
    pipe = _pipe(real_price_map, fake_db, allow_mock=False, mock_mode=True)
    out = await pipe.execute_full_pipeline(target_holdings, trade_date="2024-01-02")
    assert out["status"] == "failed"
    assert "Mock" in out["error"]


@pytest.mark.asyncio
async def test_closed_market_refuses_submit(target_holdings, real_price_map, fake_db, initial_capital):
    pipe = _pipe(real_price_map, fake_db, capital=initial_capital)
    out = await pipe.execute_full_pipeline(
        target_holdings,
        current_positions=pd.DataFrame(),
        trade_date="2024-01-02",
        price_map=real_price_map,
        total_capital=initial_capital,
        strategy="closed",
    )
    assert out["status"] == "failed"
    assert "关闭" in out["error"] or "午休" in out["error"]


@pytest.mark.asyncio
async def test_twap_happy_path_from_empty_broker(target_holdings, real_price_map, fake_db, initial_capital):
    pipe = _pipe(real_price_map, fake_db, capital=initial_capital)
    out = await pipe.execute_full_pipeline(
        target_holdings,
        current_positions=pd.DataFrame(),
        trade_date="2024-01-02",
        price_map=real_price_map,
        total_capital=initial_capital,
        strategy="twap_buy",
    )
    assert out["status"] in {"success", "failed"}
    assert out["statistics"]["orders_generated"] >= 1
    assert out["statistics"]["orders_submitted"] >= 1


@pytest.mark.asyncio
async def test_no_orders_when_already_aligned(target_holdings, real_price_map, fake_db, initial_capital):
    from zstock.order_management.order_generator import OrderGenerator

    row = target_holdings.iloc[0]
    code = str(row["code"])
    vol = OrderGenerator._row_to_volume(
        pd.Series({"stock_code": code, "weight": float(row["weight"])}),
        real_price_map,
        initial_capital,
    )
    pipe = _pipe(
        real_price_map,
        fake_db,
        positions=[make_position(code, vol, real_price_map[code])],
        capital=initial_capital,
    )
    target = pd.DataFrame([{"code": code, "weight": float(row["weight"])}])
    out = await pipe.execute_full_pipeline(
        target,
        current_positions=pd.DataFrame([{"stock_code": code, "volume": vol}]),
        trade_date="2024-01-02",
        price_map=real_price_map,
        total_capital=initial_capital,
        strategy="twap_buy",
    )
    assert out["status"] == "no_orders"


@pytest.mark.asyncio
async def test_auction_partial_when_buys_deferred(target_holdings, real_price_map, fake_db, initial_capital):
    pipe = _pipe(real_price_map, fake_db, capital=initial_capital)
    out = await pipe.execute_full_pipeline(
        target_holdings,
        current_positions=pd.DataFrame(),
        trade_date="2024-01-02",
        price_map=real_price_map,
        total_capital=initial_capital,
        strategy="auction_sell",
    )
    assert out["status"] in {"partial", "no_orders", "success"}
    if out["status"] == "partial":
        assert out["statistics"]["orders_deferred"] >= 1


@pytest.mark.asyncio
async def test_auto_price_map_and_invalid_capital(target_holdings, real_price_map, fake_db, initial_capital):
    pipe = _pipe(real_price_map, fake_db, capital=initial_capital)
    out = await pipe.execute_full_pipeline(
        target_holdings,
        current_positions=pd.DataFrame(),
        trade_date="2024-01-02",
        total_capital=initial_capital,
        strategy="twap_buy",
    )
    assert out["status"] in {"success", "failed", "partial"}
    broke = _pipe(real_price_map, fake_db, capital=0)
    bad = await broke.execute_full_pipeline(
        target_holdings,
        trade_date="2024-01-02",
        strategy="twap_buy",
    )
    assert bad["status"] == "failed"


@pytest.mark.asyncio
async def test_broker_positions_none_stops(target_holdings, real_price_map, fake_db):
    from zstock.order_management.test.conftest import FakeQMT

    class NonePos(FakeQMT):
        def get_positions(self):
            return None

    gen = OrderGenerator()
    gen._database_service = fake_db
    exe = XtQuantExecutor(qmt_util=NonePos(real_price_map), allow_mock=True)
    exe._db_service = fake_db
    pipe = OrderManagementPipeline(
        order_generator=gen,
        xtquant_executor=exe,
        trade_settlement=TradeSettlement(),
        execution_strategy=ExecutionStrategy(twap_interval_seconds=0),
        allow_mock=True,
    )
    out = await pipe.execute_full_pipeline(target_holdings, trade_date="2024-01-02")
    assert out["status"] == "failed"


def test_default_ctor_uses_injected_deps(monkeypatch, real_price_map, fake_db):
    monkeypatch.setattr(
        "zstock.order_management.pipeline.OrderGenerator",
        lambda: OrderGenerator(),
    )
    monkeypatch.setattr(
        "zstock.order_management.xtquant_executor.XtQuantExecutor",
        lambda allow_mock=False: XtQuantExecutor(
            qmt_util=FakeQMT(real_price_map), allow_mock=True
        ),
    )
    pipe = OrderManagementPipeline(allow_mock=True)
    assert pipe.order_generator is not None
    assert pipe.xtquant_executor is not None
