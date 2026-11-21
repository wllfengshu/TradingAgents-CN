"""strategy_store：用 FakeMongo + 真实 strategy_params 模板测 CRUD。"""

from __future__ import annotations

import pytest

from zstock.common.config.strategy_config import load_strategy_params
from zstock.data_management import strategy_store as ss
from test.data_management.test.conftest import FakeMongoDB


@pytest.fixture
def db():
    return FakeMongoDB()


def test_count_active_factors_and_template():
    params = load_strategy_params()
    n = ss.count_active_factors(params.get("active_factors"))
    assert n > 0
    assert ss.count_active_factors(None) == 0
    assert ss.count_active_factors({"sector": "bad"}) == 0
    tpl = ss.default_template_params()
    assert tpl["final_score"]["top_k"] == params["final_score"]["top_k"]
    tpl["final_score"]["top_k"] = 99
    assert load_strategy_params()["final_score"]["top_k"] != 99


@pytest.mark.asyncio
async def test_strategy_crud_lifecycle(db):
    await ss.ensure_strategy_indexes(db)
    created = await ss.create_strategy(
        db,
        name="ut-overnight",
        description="真实参数模板",
        active_factors={"sector": {"momentum": [{"field": "f_x", "weight": 1.0}]}},
        backtest={"start_date": "2024-01-02", "end_date": "2024-01-16"},
    )
    assert created["name"] == "ut-overnight"
    assert created["id"]
    assert created["params"]["strategy_name"] == "ut-overnight"
    assert created["params"]["active_factors"]["sector"]["momentum"][0]["field"] == "f_x"
    assert created["params"]["backtest"]["start_date"] == "2024-01-02"

    listed = await ss.list_strategies(db)
    assert listed
    assert "params" not in listed[0]
    assert listed[0]["factor_summary"]["sector"]["momentum"] == 1

    got = await ss.get_strategy(db, created["id"])
    assert got["params"]["description"] == "真实参数模板"

    updated = await ss.update_strategy(
        db,
        created["id"],
        name="ut-overnight-2",
        description="updated",
        active_factors={"dragon": {"momentum": []}},
        backtest={"end_date": "2024-02-01"},
    )
    assert updated["name"] == "ut-overnight-2"
    assert updated["params"]["active_factors"]["dragon"]["momentum"] == []
    assert updated["params"]["backtest"]["end_date"] == "2024-02-01"

    full = await ss.update_strategy(
        db,
        created["id"],
        params={"strategy_name": "full", "active_factors": {}, "version": "ut"},
    )
    assert full["params"]["version"] == "ut"

    assert await ss.get_strategy(db, "000000000000000000000000") is None
    assert await ss.delete_strategy(db, created["id"]) is True
    assert await ss.get_strategy(db, created["id"]) is None
    assert await ss.delete_strategy(db, created["id"]) is False


@pytest.mark.asyncio
async def test_backtest_records(db):
    strat = await ss.create_strategy(db, name="bt-host")
    rec = await ss.create_backtest_record(
        db,
        strategy_id=strat["id"],
        start_date="2024-01-02",
        end_date="2024-01-16",
        job_id="job-1",
    )
    assert rec["status"] == "running"
    assert rec["strategy_id"] == strat["id"]

    finished = await ss.finish_backtest_record(
        db,
        rec["id"],
        status="ok",
        metrics={"sharpe": 1.2},
        equity=[{"date": "2024-01-02", "equity": 1.0}],
    )
    assert finished["status"] == "ok"
    assert finished["metrics"]["sharpe"] == 1.2
    assert finished["equity"]

    got = await ss.get_backtest(db, rec["id"])
    assert got["job_id"] == "job-1"
    listed = await ss.list_backtests(db, strategy_id=strat["id"])
    assert listed
    assert "equity" not in listed[0]
