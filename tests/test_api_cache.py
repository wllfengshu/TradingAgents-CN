import importlib
from pathlib import Path
import sys
import types
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.get_calls = 0
        self.setex_calls = []

    def get(self, key: str) -> Any:
        self.get_calls += 1
        return self.store.get(key)

    def setex(self, key: str, expire: int, value: str) -> None:
        self.setex_calls.append((key, expire, value))
        self.store[key] = value


def _load_api_cache(monkeypatch):
    fake_redis = FakeRedis()

    app_mod = types.ModuleType("app")
    core_mod = types.ModuleType("app.core")
    db_mod = types.ModuleType("app.core.database")
    db_mod.init_db = lambda: None
    db_mod.close_db = lambda: None
    db_mod.get_redis_client = lambda: fake_redis

    monkeypatch.setitem(sys.modules, "app", app_mod)
    monkeypatch.setitem(sys.modules, "app.core", core_mod)
    monkeypatch.setitem(sys.modules, "app.core.database", db_mod)

    sys.modules.pop("tradingagents.utils.api_cache", None)
    import tradingagents.utils.api_cache as api_cache

    api_cache = importlib.reload(api_cache)
    api_cache._local_cache.clear()
    api_cache._stats["hits"] = 0
    api_cache._stats["misses"] = 0

    return api_cache, fake_redis


def test_call_bypass_cache_when_expire_non_positive(monkeypatch):
    api_cache, fake_redis = _load_api_cache(monkeypatch)

    counter = {"n": 0}

    def source():
        counter["n"] += 1
        return {"v": counter["n"]}

    r1 = api_cache.call("k:realtime", source, expire=0)
    r2 = api_cache.call("k:realtime", source, expire=-1)

    assert r1 == {"v": 1}
    assert r2 == {"v": 2}
    assert counter["n"] == 2
    assert fake_redis.get_calls == 0
    assert fake_redis.setex_calls == []


def test_call_hit_local_cache(monkeypatch):
    api_cache, fake_redis = _load_api_cache(monkeypatch)

    counter = {"n": 0}

    def source():
        counter["n"] += 1
        return "payload"

    first = api_cache.call("k:local", source, expire=60)
    second = api_cache.call("k:local", source, expire=60)

    assert first == "payload"
    assert second == "payload"
    assert counter["n"] == 1
    assert api_cache._stats["misses"] == 1
    assert api_cache._stats["hits"] >= 1
    assert len(fake_redis.setex_calls) == 1


def test_call_hit_redis_cache_and_backfill_local(monkeypatch):
    api_cache, fake_redis = _load_api_cache(monkeypatch)

    key = api_cache._redis_key("k:redis")
    fake_redis.store[key] = api_cache._serialize({"x": 1})

    source_called = {"n": 0}

    def source():
        source_called["n"] += 1
        return {"x": 999}

    result = api_cache.call("k:redis", source, expire=60)

    assert result == {"x": 1}
    assert source_called["n"] == 0
    assert api_cache._local_cache["k:redis"] == {"x": 1}
    assert api_cache._stats["hits"] == 1


def test_serialize_deserialize_dataframe_roundtrip(monkeypatch):
    api_cache, _ = _load_api_cache(monkeypatch)

    df = pd.DataFrame([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])

    raw = api_cache._serialize(df)
    restored = api_cache._deserialize(raw)

    assert isinstance(restored, pd.DataFrame)
    assert restored.to_dict(orient="records") == df.to_dict(orient="records")

