"""XtQuant 执行器：注入纸面 QMT + FakeDB，行情来自真实收盘价。"""

import pytest

from zstock.common.entity.order_entity import Order
from zstock.order_management.xtquant_executor import XtQuantExecutor, _accepts_volume
from zstock.order_management.test.conftest import FakeDB, FakeQMT, make_position


def _make_executor(price_map, fake_db, positions=None, capital=10_000_000):
    exe = XtQuantExecutor(qmt_util=FakeQMT(price_map, positions=positions, capital=capital), allow_mock=True)
    exe._db_service = fake_db
    return exe


def test_to_broker_code():
    assert XtQuantExecutor.to_broker_code("603201").endswith(".SH")
    assert XtQuantExecutor.to_broker_code("000060").endswith(".SZ")


@pytest.mark.asyncio
async def test_submit_buy_sell_query_cancel(real_price_map, fake_db):
    code = next(iter(real_price_map))
    exe = _make_executor(real_price_map, fake_db)
    buy = Order("202401020001", code, "buy", 200, price=real_price_map[code])
    sell = Order("202401020002", code, "sell", 100, price=real_price_map[code])
    assert await exe.submit_order(buy)
    assert await exe.submit_order(sell)
    assert await exe.query_order("202401020001")
    assert await exe.query_all_orders()
    assert await exe.cancel_order("202401020001")
    assert not await exe.cancel_order("missing")
    bad = Order("x", code, "hold", 100)
    assert await exe.submit_order(bad) is None


@pytest.mark.asyncio
async def test_buy_without_price_uses_quote(real_price_map, fake_db):
    code = next(iter(real_price_map))
    exe = _make_executor(real_price_map, fake_db)
    buy = Order("202401020003", code, "buy", 100, price=None)
    assert await exe.submit_order(buy)


def test_account_positions_price_map(real_price_map, fake_db):
    code = next(iter(real_price_map))
    px = real_price_map[code]
    exe = _make_executor(
        real_price_map,
        fake_db,
        positions=[make_position(code, 500, px)],
        capital=10_000_000,
    )
    acc = exe.get_account_info()
    assert acc["cash"] == 10_000_000
    pos = exe.get_positions()
    assert pos[0]["code"] == code
    assert pos[0]["volume"] == 500
    prices = exe.get_price_map([code])
    assert prices[code] == pytest.approx(px)
    assert exe.get_price_map([]) == {}
    assert exe.is_connected() is True


def test_accepts_volume_on_fake_buy():
    qmt = FakeQMT({"603201": 12.0})
    assert _accepts_volume(qmt.buy) is True


def test_executor_error_paths(real_price_map, fake_db):
    class Boom(FakeQMT):
        def get_account_info(self):
            raise RuntimeError("down")

        def get_positions(self):
            raise RuntimeError("down")

        def get_realtime_quote(self, codes):
            raise RuntimeError("down")

    exe = XtQuantExecutor(qmt_util=Boom(real_price_map), allow_mock=True)
    exe._db_service = fake_db
    assert exe.get_account_info() is None
    assert exe.get_positions() is None
    assert exe.get_price_map(["603201"]) == {}
    assert _accepts_volume(1) is False
