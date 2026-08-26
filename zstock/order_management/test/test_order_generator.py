"""订单生成：目标来自 2024-01-02 真实截面优化结果，价格来自当日收盘。"""

import pandas as pd
import pytest

from zstock.order_management.order_generator import OrderGenerator


def test_empty_target_returns_no_orders():
    gen = OrderGenerator()
    assert gen.generate_orders(pd.DataFrame()) == []


def test_weight_to_lots_from_real_holdings(target_holdings, real_price_map, initial_capital):
    gen = OrderGenerator()
    orders = gen.generate_orders(
        target_holdings,
        current_positions=pd.DataFrame(),
        trade_date="2024-01-02",
        price_map=real_price_map,
        total_capital=initial_capital,
    )
    assert orders
    buys = [o for o in orders if o.direction == "buy"]
    assert len(buys) == len(target_holdings)
    for o in buys:
        assert o.volume >= 100
        assert o.volume % 100 == 0
        assert o.stock_code in set(target_holdings["code"].astype(str))
        px = real_price_map[o.stock_code]
        w = float(target_holdings.loc[target_holdings["code"].astype(str) == o.stock_code, "weight"].iloc[0])
        expected = int((initial_capital * w) / px) // 100 * 100
        assert o.volume == expected


def test_missing_price_skips_without_flatten(target_holdings, real_price_map, initial_capital):
    code = str(target_holdings["code"].iloc[0])
    priced = {k: v for k, v in real_price_map.items() if k != code}
    assert priced, "至少还要留一只有价标的"
    gen = OrderGenerator()
    orders = gen.generate_orders(
        target_holdings,
        current_positions=pd.DataFrame([{"stock_code": code, "volume": 500}]),
        trade_date="2024-01-02",
        price_map=priced,
        total_capital=initial_capital,
    )
    skipped_sells = [o for o in orders if o.stock_code == code]
    assert skipped_sells == []
    bought = {o.stock_code for o in orders if o.direction == "buy"}
    assert code not in bought
    assert bought


def test_missing_capital_skips_weight_rows(target_holdings, real_price_map):
    gen = OrderGenerator()
    orders = gen.generate_orders(
        target_holdings,
        trade_date="2024-01-02",
        price_map=real_price_map,
        total_capital=None,
    )
    assert orders == []


def test_sell_codes_not_in_target(target_holdings, real_price_map, initial_capital):
    leftover = "600000"
    leftover_px = 8.12
    gen = OrderGenerator()
    orders = gen.generate_orders(
        target_holdings,
        current_positions=pd.DataFrame([{"code": leftover, "volume": 800}]),
        trade_date="2024-01-02",
        price_map={**real_price_map, leftover: leftover_px},
        total_capital=initial_capital,
    )
    sells = [o for o in orders if o.direction == "sell"]
    assert any(o.stock_code == leftover and o.volume == 800 for o in sells)


def test_adjust_down_and_up(real_price_map, initial_capital):
    code = next(iter(real_price_map))
    px = real_price_map[code]
    target = pd.DataFrame([{"code": code, "weight": 0.10}])
    gen = OrderGenerator()
    target_vol = int((initial_capital * 0.10) / px) // 100 * 100
    assert target_vol >= 200
    down = gen.generate_orders(
        target,
        current_positions=pd.DataFrame([{"stock_code": code, "volume": target_vol + 300}]),
        trade_date="2024-01-02",
        price_map=real_price_map,
        total_capital=initial_capital,
    )
    assert len(down) == 1
    assert down[0].direction == "sell"
    assert down[0].volume == 300

    up = OrderGenerator().generate_orders(
        target,
        current_positions=pd.DataFrame([{"stock_code": code, "volume": target_vol - 200}]),
        trade_date="2024-01-02",
        price_map=real_price_map,
        total_capital=initial_capital,
    )
    assert len(up) == 1
    assert up[0].direction == "buy"
    assert up[0].volume == 200


def test_target_shares_preferred_over_weight(real_price_map):
    code = next(iter(real_price_map))
    gen = OrderGenerator()
    orders = gen.generate_orders(
        pd.DataFrame([{"code": code, "weight": 0.99, "target_shares": 400}]),
        trade_date="2024-01-02",
        price_map=real_price_map,
        total_capital=10_000_000,
    )
    assert len(orders) == 1
    assert orders[0].volume == 400


def test_update_export_and_get_orders(target_holdings, real_price_map, initial_capital):
    gen = OrderGenerator()
    orders = gen.generate_orders(
        target_holdings,
        trade_date="2024-01-02",
        price_map=real_price_map,
        total_capital=initial_capital,
    )
    oid = orders[0].order_id
    assert gen.get_orders(oid)[0].order_id == oid
    assert gen.get_orders("missing") == []
    assert gen.update_order_status(oid, "filled", filled_volume=100, filled_price=12.3)
    assert not gen.update_order_status("missing", "filled")
    df = gen.export_orders("dataframe")
    assert not df.empty
    assert isinstance(gen.export_orders("list"), list)
    assert oid in gen.export_orders("dict")
    assert gen.export_orders("xml") is None


@pytest.mark.asyncio
async def test_save_orders_to_db(target_holdings, real_price_map, initial_capital, fake_db):
    gen = OrderGenerator()
    gen._database_service = fake_db
    orders = gen.generate_orders(
        target_holdings,
        trade_date="2024-01-02",
        price_map=real_price_map,
        total_capital=initial_capital,
    )
    assert await gen.save_orders_to_db(orders)
    assert len(fake_db.inserted) == len(orders)
    assert await gen.save_orders_to_db([])


def test_normalize_requires_code_column():
    with pytest.raises(KeyError):
        OrderGenerator.normalize_positions_df(pd.DataFrame([{"volume": 100}]))
