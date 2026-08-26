"""查询窗口切分与 CacheService 无 Redis 降级。"""

import pytest

from zstock.data_management.cache_service import CacheService
from zstock.data_management.query_service import _iter_date_windows


def test_iter_date_windows_real_backtest_range():
    wins = _iter_date_windows("2024-01-01", "2024-03-15", 90)
    assert wins[0][0] == "2024-01-01"
    assert wins[-1][1] == "2024-03-15"
    assert all(a <= b for a, b in wins)
    assert _iter_date_windows("2024-03-15", "2024-01-01", 30) == []
    single = _iter_date_windows("20240102", "20240102", 0)
    assert single == [("2024-01-02", "2024-01-02")]


@pytest.mark.asyncio
async def test_cache_without_redis():
    cache = CacheService()
    cache.redis_client = None
    assert await cache.get("k") is None
    assert await cache.set("k", {"code": "603201"}) is False
    assert await cache.delete("k") is False
    stats = cache.get_stats()
    assert stats["total_misses"] >= 1
    cache.reset_stats()
    assert cache.get_stats()["redis_miss"] == 0


@pytest.mark.asyncio
async def test_cache_with_fake_redis():
    class FakeRedis:
        def __init__(self):
            self.store = {}

        async def get(self, key):
            return self.store.get(key)

        async def setex(self, key, ttl, value):
            self.store[key] = value

        async def delete(self, key):
            self.store.pop(key, None)

    cache = CacheService()
    cache.redis_client = FakeRedis()
    payload = {"code": "603201", "close": 15.175619047619051}
    assert await cache.set("ohlcv:603201", payload)
    hit = await cache.get("ohlcv:603201")
    assert hit["code"] == "603201"
    miss = await cache.get("missing")
    assert miss is None
    assert await cache.delete("ohlcv:603201")
    stats = cache.get_stats()
    assert stats["total_hits"] >= 1


@pytest.mark.asyncio
async def test_cache_redis_bytes_and_failures():
    class BytesRedis:
        def __init__(self):
            self.store = {}

        async def get(self, key):
            val = self.store.get(key)
            return val.encode("utf-8") if isinstance(val, str) else val

        async def setex(self, key, ttl, value):
            self.store[key] = value

        async def delete(self, key):
            self.store.pop(key, None)

    cache = CacheService()
    cache.redis_client = BytesRedis()
    assert await cache.set("k", {"code": "000060"})
    hit = await cache.get("k")
    assert hit["code"] == "000060"

    class DictRedis:
        async def get(self, key):
            return {"already": True}

        async def setex(self, key, ttl, value):
            raise RuntimeError("setex fail")

        async def delete(self, key):
            raise RuntimeError("delete fail")

    cache.redis_client = DictRedis()
    assert await cache.get("any") == {"already": True}
    assert await cache.set("k", {"a": 1}) is False
    assert await cache.delete("k") is False

    class BoomRedis:
        async def get(self, key):
            raise RuntimeError("get fail")

    cache.redis_client = BoomRedis()
    assert await cache.get("k") is None


def test_cache_init_from_db_manager(monkeypatch):
    class DummyRedis:
        pass

    class Mgr:
        redis_client = DummyRedis()

    monkeypatch.setattr("app.core.database.db_manager", Mgr())
    cache = CacheService()
    assert isinstance(cache.redis_client, DummyRedis)


def test_cache_init_from_redis_service(monkeypatch):
    class DummyRedis:
        pass

    class Mgr:
        redis_client = None

    class Svc:
        redis = DummyRedis()

    monkeypatch.setattr("app.core.database.db_manager", Mgr())
    monkeypatch.setattr("app.core.redis_client.get_redis_service", lambda: Svc())
    cache = CacheService()
    assert isinstance(cache.redis_client, DummyRedis)


def test_get_cache_service_singleton(monkeypatch):
    import zstock.data_management.cache_service as cs

    monkeypatch.setattr(cs, "_cache_service", None)
    a = cs.get_cache_service()
    b = cs.get_cache_service()
    assert a is b
