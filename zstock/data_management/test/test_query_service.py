"""DataQueryService：注入 FakeDatabaseService，文档来自真实导出。"""

import pytest

from zstock.data_management.query_service import (
    COL_OHLCV,
    COL_SECTOR,
    COL_STOCK_INFO,
    COL_FACTOR_MARKET,
    COL_FACTOR_SECTOR,
    COL_FACTOR_DRAGON,
    COL_FACTOR_FORCE,
    COL_LHB,
    COL_CAPITAL_FLOW,
    DataQueryService,
)
from zstock.data_management.test.conftest import FakeDatabaseService


def _make_qs(store) -> DataQueryService:
    qs = DataQueryService.__new__(DataQueryService)
    qs.database_service = FakeDatabaseService(store)
    return qs


@pytest.mark.asyncio
async def test_get_ohlcv_399300(hs300_rows):
    qs = _make_qs({COL_OHLCV: hs300_rows})
    df, src = await qs.get_ohlcv("399300", "2023-10-01", "2024-03-15")
    assert src == "mongodb"
    assert len(df) == len(hs300_rows)
    assert "close" in df.columns
    with pytest.raises(ValueError):
        await qs.get_ohlcv("399300", "2010-01-01", "2010-01-05")


@pytest.mark.asyncio
async def test_get_ohlcv_batch_real_names(stock_ohlcv_docs):
    qs = _make_qs({COL_OHLCV: stock_ohlcv_docs})
    batch = await qs.get_ohlcv_batch(["603201", "000060"], "2024-01-02", "2024-01-16")
    assert "603201" in batch
    assert "000060" in batch
    assert not batch["603201"].empty
    empty = await qs.get_ohlcv_batch([], "2024-01-02", "2024-01-16")
    assert empty == {}


@pytest.mark.asyncio
async def test_stock_info_and_universe(stock_info_sample):
    docs = list(stock_info_sample.get("sample") or [])
    qs = _make_qs({COL_STOCK_INFO: docs})
    code = docs[0]["code"]
    info, src = await qs.get_stock_info(code)
    assert info["code"] == code
    assert src == "mongodb"
    all_docs, src2 = await qs.get_all_stocks()
    assert len(all_docs) == len(docs)
    assert src2 == "mongodb"
    with pytest.raises(ValueError):
        await qs.get_stock_info("999999")


@pytest.mark.asyncio
async def test_sector_list_and_members(sector_list_sample):
    docs = list(sector_list_sample.get("sample") or [])
    for d in docs:
        d.setdefault("source", "xtquant")
        d.setdefault("stocks", ["603201"])
    qs = _make_qs({COL_SECTOR: docs})
    sectors, src = await qs.get_sector_list("SW2")
    assert src == "mongodb"
    assert sectors
    all_sectors, _ = await qs.get_sector_list(None)
    assert len(all_sectors) >= len(sectors)
    code = docs[0]["sector_code"]
    members, _ = await qs.get_sector_stocks(code)
    assert members == docs[0]["stocks"]
    batch = await qs.get_sector_stocks_batch([code])
    assert batch[code] == docs[0]["stocks"]
    assert await qs.get_sector_stocks_batch([]) == {}


@pytest.mark.asyncio
async def test_factor_market_from_dump():
    from pathlib import Path
    import json

    path = Path(__file__).resolve().parents[2] / "factor_management" / "test" / "fixtures" / "factor_raw_20240102.json"
    if not path.is_file():
        pytest.skip("缺少因子原始值")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    qs = _make_qs({COL_FACTOR_MARKET: [raw["market"]]})
    doc = await qs.get_factor_market("2024-01-02")
    assert doc["index_code"] == "399300"
    assert doc["trade_date"] == "2024-01-02"


@pytest.mark.asyncio
async def test_factor_layers_and_empty_lhb():
    qs = _make_qs(
        {
            COL_FACTOR_SECTOR: [{"trade_date": "2024-01-02", "sector_code": "SW2出版"}],
            COL_FACTOR_DRAGON: [{"trade_date": "2024-01-02", "code": "601921", "sector_code": "SW2出版"}],
            COL_FACTOR_FORCE: [{"trade_date": "2024-01-02", "code": "601921"}],
            COL_LHB: [],
            COL_CAPITAL_FLOW: [],
        }
    )
    secs = await qs.get_factor_sectors("2024-01-02")
    assert secs[0]["sector_code"] == "SW2出版"
    dragons = await qs.get_factor_dragons("2024-01-02", sector_codes=["SW2出版"])
    assert dragons[0]["code"] == "601921"
    forces = await qs.get_factor_forces("2024-01-02")
    assert forces[0]["code"] == "601921"
    assert await qs.get_lhb("603201", "2024-01-02") is None
    assert await qs.get_lhb_recent("603201", "2024-01-02", days=0) == []
    with pytest.raises(ValueError):
        await qs.get_capital_flow("603201", "2024-01-02")


@pytest.mark.asyncio
async def test_ensure_indexes_on_fake_mongo():
    from zstock.data_management.database_service import DatabaseService
    from zstock.data_management.test.conftest import FakeMongoDB

    qs = DataQueryService.__new__(DataQueryService)
    svc = DatabaseService.__new__(DatabaseService)
    svc.db = FakeMongoDB()
    qs.database_service = svc
    await qs.ensure_indexes()


@pytest.mark.asyncio
async def test_capital_flow_and_lhb_from_live_dump():
    from pathlib import Path
    import json

    live = Path(__file__).resolve().parents[2] / "factor_management" / "test" / "fixtures" / "live_calc_20240102.json"
    if not live.is_file():
        pytest.skip("缺少资金流夹具")
    with open(live, encoding="utf-8") as f:
        payload = json.load(f)
    flow_docs = []
    for code, docs in (payload.get("flow") or {}).items():
        flow_docs.extend(docs)
    if not flow_docs:
        pytest.skip("资金流夹具为空")
    qs = _make_qs(
        {
            COL_CAPITAL_FLOW: flow_docs,
            COL_LHB: [
                {"code": "603201", "trade_date": "2024-01-02", "source": "xtquant"},
            ],
        }
    )
    doc, src = await qs.get_capital_flow("603201", "2024-01-02")
    assert src == "mongodb"
    assert doc["code"] == "603201"
    recent = await qs.get_capital_flow_recent_days(["603201", "000060"], "2024-01-02", days=5)
    assert "603201" in recent
    ranged = await qs.get_capital_flow_range(["603201"], "2023-12-01", "2024-01-02")
    assert "603201" in ranged
    assert await qs.get_capital_flow_range([], "2024-01-01", "2024-01-02") == {}
    lhb = await qs.get_lhb("603201", "2024-01-02")
    assert lhb["code"] == "603201"
    recent_lhb = await qs.get_lhb_recent("603201", "2024-01-02", days=10)
    assert recent_lhb
    batch = await qs.batch_get_lhb(["603201", "000060"], "2024-01-02")
    assert batch["603201"] is not None
    assert await qs.batch_get_lhb([], "2024-01-02") == {}
    batch_r = await qs.batch_get_lhb_recent(["603201"], "2024-01-02", days=5)
    assert "603201" in batch_r
    assert await qs.batch_get_lhb_recent(["603201"], "2024-01-02", days=0) == {}


@pytest.mark.asyncio
async def test_get_factor_dragons_filter():
    qs = _make_qs(
        {
            COL_FACTOR_DRAGON: [
                {"trade_date": "2024-01-02", "code": "603201", "sector_code": "SW2汽车零部件"},
                {"trade_date": "2024-01-02", "code": "000060", "sector_code": "SW2工业金属"},
            ]
        }
    )
    docs = await qs.get_factor_dragons("2024-01-02", sector_codes=["SW2汽车零部件"])
    assert [d["code"] for d in docs] == ["603201"]
    all_docs = await qs.get_factor_dragons("2024-01-02")
    assert {d["code"] for d in all_docs} == {"603201", "000060"}


class _BoomDB:
    async def query(self, *args, **kwargs):
        raise RuntimeError("mongo down")

    async def query_one(self, *args, **kwargs):
        raise RuntimeError("mongo down")


class _NoneDB:
    async def query(self, *args, **kwargs):
        return None

    async def query_one(self, *args, **kwargs):
        return None


def _qs_with(db) -> DataQueryService:
    qs = DataQueryService.__new__(DataQueryService)
    qs.database_service = db
    return qs


@pytest.mark.asyncio
async def test_ohlcv_batch_windows_and_empty():
    from zstock.data_management.test.conftest import STRATEGY_OHLCV
    import json

    if not STRATEGY_OHLCV.is_file():
        pytest.skip("缺少个股 OHLCV 夹具")
    with open(STRATEGY_OHLCV, encoding="utf-8") as f:
        payload = json.load(f)
    docs = []
    for code, rows in (payload.get("by_code") or {}).items():
        for r in rows:
            doc = dict(r)
            doc["code"] = code
            doc["period"] = "D"
            docs.append(doc)
    qs = _make_qs({COL_OHLCV: docs})
    inverted = await qs.get_ohlcv_batch(["603201"], "2024-06-01", "2024-01-01")
    assert inverted == {}
    weekly = await qs.get_ohlcv_batch(["603201"], "2024-01-02", "2024-01-16", period="weekly")
    assert weekly == {}
    batch = await qs.get_ohlcv_batch(
        ["603201", "000060"],
        "2024-01-02",
        "2024-06-14",
        batch_size=1,
        date_chunk_days=30,
        query_concurrency=0,
    )
    assert "603201" in batch
    assert "000060" in batch


@pytest.mark.asyncio
async def test_ohlcv_drops_id_and_batch_query_error(hs300_rows):
    row = dict(next(r for r in hs300_rows if r["trade_date"] == "2024-01-02"))
    row["_id"] = "mongo-id"
    qs = _make_qs({COL_OHLCV: [row]})
    df, _ = await qs.get_ohlcv("399300", "2024-01-02", "2024-01-02")
    assert "_id" not in df.columns
    assert float(df.iloc[0]["close"]) == float(row["close"])
    boom = _qs_with(_BoomDB())
    with pytest.raises(ValueError, match="get_ohlcv_batch"):
        await boom.get_ohlcv_batch(["603201"], "2024-01-02", "2024-01-02")


@pytest.mark.asyncio
async def test_stock_info_strips_id_and_empty_universe(stock_info_sample):
    base = dict(stock_info_sample["sample"][0])
    code = base["code"]
    base["_id"] = "mongo-id"
    qs = _make_qs({COL_STOCK_INFO: [base]})
    info, _ = await qs.get_stock_info(code)
    assert "_id" not in info
    assert info["code"] == code
    empty = _make_qs({COL_STOCK_INFO: []})
    with pytest.raises(ValueError, match="get_all_stocks"):
        await empty.get_all_stocks()
    with pytest.raises(ValueError, match="get_all_stocks"):
        await _qs_with(_BoomDB()).get_all_stocks()


@pytest.mark.asyncio
async def test_capital_flow_edge_and_errors():
    live = __import__("pathlib").Path(__file__).resolve().parents[2] / "factor_management" / "test" / "fixtures" / "live_calc_20240102.json"
    if not live.is_file():
        pytest.skip("缺少资金流夹具")
    import json

    with open(live, encoding="utf-8") as f:
        payload = json.load(f)
    docs = list(payload["flow"]["000060"])
    docs[0] = dict(docs[0], _id="flow1")
    qs = _make_qs({COL_CAPITAL_FLOW: docs})
    doc, _ = await qs.get_capital_flow("000060", "20240102")
    assert "_id" not in doc
    assert await qs.get_capital_flow_recent_days([], "2024-01-02") == {}
    assert await qs.get_capital_flow_recent_days(["000060"], "2024-01-02", days=0) == {}
    with pytest.raises(ValueError, match="非法 end_date"):
        await qs.get_capital_flow_recent_days(["000060"], "bad-date", days=5)
    recent = await qs.get_capital_flow_recent_days(["000060"], "2024-01-02", days=3)
    assert len(recent["000060"]) == 3
    assert await qs.get_capital_flow_range(["000060"], "2024-06-01", "2024-01-01") == {}
    ranged = await qs.get_capital_flow_range(
        ["000060"],
        "2023-12-01",
        "2024-01-02",
        query_concurrency=0,
        date_chunk_days=10,
    )
    assert ranged["000060"]
    with pytest.raises(ValueError, match="get_capital_flow"):
        await _qs_with(_BoomDB()).get_capital_flow("000060", "2024-01-02")
    with pytest.raises(RuntimeError):
        await _qs_with(_BoomDB()).get_capital_flow_recent_days(["000060"], "2024-01-02")
    with pytest.raises(RuntimeError):
        await _qs_with(_BoomDB()).get_capital_flow_range(["000060"], "2024-01-01", "2024-01-02")


@pytest.mark.asyncio
async def test_lhb_and_sector_error_paths():
    boom = _qs_with(_BoomDB())
    assert await boom.get_lhb("603201", "2024-01-02") is None
    assert await boom.get_lhb_recent("603201", "2024-01-02") == []
    assert await boom.get_lhb_recent("603201", "not-a-date") == []
    batch = await boom.batch_get_lhb(["603201"], "2024-01-02")
    assert batch["603201"] is None
    recent = await boom.batch_get_lhb_recent(["603201"], "2024-01-02")
    assert recent["603201"] == []
    empty_date = await boom.batch_get_lhb_recent(["603201"], "not-a-date")
    assert empty_date["603201"] == []
    with pytest.raises(ValueError, match="get_sector_list"):
        await boom.get_sector_list("SW2")
    with pytest.raises(ValueError, match="get_sector_stocks"):
        await boom.get_sector_stocks("SW2出版")
    with pytest.raises(ValueError, match="get_sector_stocks_batch"):
        await boom.get_sector_stocks_batch(["SW2出版"])
    none_qs = _qs_with(_NoneDB())
    with pytest.raises(ValueError, match="get_sector_list"):
        await none_qs.get_sector_list(None)
    with pytest.raises(ValueError, match="get_sector_stocks"):
        await none_qs.get_sector_stocks("SW2出版")
    assert await none_qs.get_sector_stocks_batch(["SW2出版"]) == {}


@pytest.mark.asyncio
async def test_ensure_indexes_legacy_drop_missing():
    from zstock.data_management.database_service import DatabaseService
    from zstock.data_management.test.conftest import FakeMongoDB

    class RaisingMongo(FakeMongoDB):
        def __getitem__(self, name):
            col = super().__getitem__(name)

            async def drop_index(_n):
                raise RuntimeError("index not found")

            col.drop_index = drop_index
            return col

    qs = DataQueryService.__new__(DataQueryService)
    svc = DatabaseService.__new__(DatabaseService)
    svc.db = RaisingMongo()
    qs.database_service = svc
    await qs.ensure_indexes()


def test_get_data_query_service_singleton(monkeypatch):
    import zstock.data_management.query_service as qs_mod
    from zstock.data_management.test.conftest import FakeDatabaseService

    fake = FakeDatabaseService({})
    monkeypatch.setattr(qs_mod, "get_database_service", lambda: fake)
    monkeypatch.setattr(qs_mod, "_data_query_service", None)
    a = qs_mod.get_data_query_service()
    b = qs_mod.get_data_query_service()
    assert a is b
    assert a.database_service is fake
