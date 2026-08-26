"""DatabaseService：FakeMongo 覆盖 CRUD，不写真实库。"""

import pytest

from zstock.data_management.database_service import DatabaseService
from zstock.data_management.test.conftest import FakeMongoDB


@pytest.fixture
def db_service(monkeypatch):
    fake = FakeMongoDB()
    monkeypatch.setattr(
        "zstock.data_management.database_service.get_database",
        lambda: fake,
    )
    return DatabaseService(), fake


@pytest.mark.asyncio
async def test_crud_roundtrip(db_service, stock_ohlcv_docs):
    svc, fake = db_service
    sample = [d for d in stock_ohlcv_docs if d["code"] == "603201"][:3]
    assert sample
    oid = await svc.insert_one("zstock_ohlcv", dict(sample[0]))
    assert oid
    ids = await svc.insert_many("zstock_ohlcv", [dict(x) for x in sample[1:]])
    assert len(ids) == len(sample) - 1
    assert await svc.insert_many("zstock_ohlcv", []) == []

    found = await svc.query("zstock_ohlcv", {"code": "603201"})
    assert len(found) == len(sample)
    one = await svc.query_one("zstock_ohlcv", {"code": "603201", "trade_date": sample[0]["trade_date"]})
    assert one["close"] == sample[0]["close"]

    n = await svc.update_one("zstock_ohlcv", {"code": "603201"}, {"note": "ut"})
    assert n >= 1
    n2 = await svc.update_many("zstock_ohlcv", {"code": "603201"}, {"$set": {"flag": 1}})
    assert n2 >= 1
    n3 = await svc.replace_one(
        "zstock_ohlcv",
        {"code": "603201", "trade_date": sample[0]["trade_date"]},
        {**sample[0], "replaced": True},
    )
    assert n3 >= 1
    n4 = await svc.replace_one("zstock_ohlcv", {"code": "missing"}, {"code": "missing"}, upsert=True)
    assert n4 >= 1
    assert await svc.count("zstock_ohlcv", {"code": "603201"}) >= 1
    all_docs = await svc.query("zstock_ohlcv")
    assert all_docs
    one_any = await svc.query_one("zstock_ohlcv")
    assert one_any is not None
    assert await svc.count("zstock_ohlcv") >= 1
    assert await svc.delete_one("zstock_ohlcv", {"code": "missing"}) == 1
    deleted = await svc.delete_many("zstock_ohlcv", {"code": "603201"})
    assert deleted >= 1
    names = await svc.list_collections()
    assert "zstock_ohlcv" in names
    await svc.drop_collection("zstock_ohlcv")
    assert await svc.count("zstock_ohlcv") == 0


def test_prepare_update_wraps_set(db_service):
    svc, _ = db_service
    assert svc._prepare_update({"a": 1}) == {"$set": {"a": 1}}
    already = {"$set": {"a": 1}}
    assert svc._prepare_update(already) is already


def test_get_database_service_singleton(monkeypatch):
    import zstock.data_management.database_service as ds
    from zstock.data_management.test.conftest import FakeMongoDB

    monkeypatch.setattr(ds, "get_database", lambda: FakeMongoDB())
    monkeypatch.setattr(ds, "_database_service", None)
    a = ds.get_database_service()
    b = ds.get_database_service()
    assert a is b


def test_init_fails_without_db(monkeypatch):
    monkeypatch.setattr(
        "zstock.data_management.database_service.get_database",
        lambda: None,
    )
    with pytest.raises(Exception):
        DatabaseService()


class _BoomCol:
    def find(self, *args, **kwargs):
        raise RuntimeError("mongo down")

    async def find_one(self, *args, **kwargs):
        raise RuntimeError("mongo down")

    async def insert_one(self, *args, **kwargs):
        raise RuntimeError("mongo down")

    async def insert_many(self, *args, **kwargs):
        raise RuntimeError("mongo down")

    async def update_one(self, *args, **kwargs):
        raise RuntimeError("mongo down")

    async def update_many(self, *args, **kwargs):
        raise RuntimeError("mongo down")

    async def replace_one(self, *args, **kwargs):
        raise RuntimeError("mongo down")

    async def delete_one(self, *args, **kwargs):
        raise RuntimeError("mongo down")

    async def delete_many(self, *args, **kwargs):
        raise RuntimeError("mongo down")

    async def count_documents(self, *args, **kwargs):
        raise RuntimeError("mongo down")

    async def drop(self):
        raise RuntimeError("mongo down")


class _BoomDB:
    def __getitem__(self, name):
        return _BoomCol()

    async def list_collection_names(self):
        raise RuntimeError("mongo down")


@pytest.mark.asyncio
async def test_database_service_error_paths(monkeypatch):
    monkeypatch.setattr(
        "zstock.data_management.database_service.get_database",
        lambda: _BoomDB(),
    )
    svc = DatabaseService()
    with pytest.raises(RuntimeError):
        await svc.query("zstock_ohlcv", {"code": "603201"})
    with pytest.raises(RuntimeError):
        await svc.query_one("zstock_ohlcv", {"code": "603201"})
    with pytest.raises(RuntimeError):
        await svc.insert_one("zstock_ohlcv", {"code": "603201"})
    with pytest.raises(RuntimeError):
        await svc.insert_many("zstock_ohlcv", [{"code": "603201"}])
    with pytest.raises(RuntimeError):
        await svc.update_one("zstock_ohlcv", {"code": "603201"}, {"flag": 1})
    with pytest.raises(RuntimeError):
        await svc.update_many("zstock_ohlcv", {"code": "603201"}, {"flag": 1})
    with pytest.raises(RuntimeError):
        await svc.replace_one("zstock_ohlcv", {"code": "603201"}, {"code": "603201"})
    with pytest.raises(RuntimeError):
        await svc.delete_one("zstock_ohlcv", {"code": "603201"})
    with pytest.raises(RuntimeError):
        await svc.delete_many("zstock_ohlcv", {"code": "603201"})
    with pytest.raises(RuntimeError):
        await svc.count("zstock_ohlcv")
    with pytest.raises(RuntimeError):
        await svc.drop_collection("zstock_ohlcv")
    assert await svc.list_collections() == []
