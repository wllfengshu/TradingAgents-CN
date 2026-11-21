"""db_utils：环境解析与 ContextVar 隔离，不连真实 Mongo。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from zstock.common.utils import db_utils


def test_mongo_settings_with_auth(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# comment",
                "MONGODB_HOST=mongo.test",
                "MONGODB_PORT=27018",
                "MONGODB_USERNAME=u1",
                "MONGODB_PASSWORD=p1",
                "MONGODB_DATABASE=zstock_ut",
                "ALREADY=fromfile",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ALREADY", "keep")
    for key in (
        "MONGODB_HOST",
        "MONGODB_PORT",
        "MONGODB_USERNAME",
        "MONGODB_PASSWORD",
        "MONGODB_DATABASE",
    ):
        monkeypatch.delenv(key, raising=False)

    def _load():
        import os

        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

    monkeypatch.setattr(db_utils, "_load_project_env", _load)
    cfg = db_utils._mongo_settings()
    assert cfg["database"] == "zstock_ut"
    assert "u1:p1@" in str(cfg["uri"])
    assert "mongo.test" in str(cfg["uri"])
    assert __import__("os").environ["ALREADY"] == "keep"
    assert db_utils._env_int("MISSING_INT", 7) == 7
    monkeypatch.setenv("MISSING_INT", "9")
    assert db_utils._env_int("MISSING_INT", 7) == 9
    monkeypatch.setenv("MISSING_INT", "  ")
    assert db_utils._env_int("MISSING_INT", 7) == 7


def test_mongo_settings_without_auth(monkeypatch):
    monkeypatch.setattr(db_utils, "_load_project_env", lambda: None)
    monkeypatch.delenv("MONGODB_USERNAME", raising=False)
    monkeypatch.delenv("MONGODB_PASSWORD", raising=False)
    monkeypatch.setenv("MONGODB_HOST", "localhost")
    monkeypatch.setenv("MONGODB_PORT", "27017")
    monkeypatch.setenv("MONGODB_DATABASE", "tradingagents")
    cfg = db_utils._mongo_settings()
    assert "@" not in str(cfg["uri"]).split("//")[1].split("/")[0]


def test_get_database_requires_init(monkeypatch):
    token = db_utils._db_override.set(None)
    try:
        # 强制走无 override 分支
        db_utils._db_override.reset(token)
        token = db_utils._db_override.set(None)
        old_client = db_utils.db_manager.mongo_client
        old_db = db_utils.db_manager.mongo_db
        db_utils.db_manager.mongo_client = None
        db_utils.db_manager.mongo_db = None
        try:
            with pytest.raises(RuntimeError, match="未初始化"):
                db_utils.get_database()
        finally:
            db_utils.db_manager.mongo_client = old_client
            db_utils.db_manager.mongo_db = old_db
    finally:
        db_utils._db_override.reset(token)


@pytest.mark.asyncio
async def test_isolated_mongo_session_injects_override(monkeypatch):
    fake_db = object()
    fake_client = MagicMock()
    fake_client.__getitem__.return_value = fake_db
    fake_client.admin = SimpleNamespace(command=AsyncMock(return_value={"ok": 1}))
    fake_client.close = MagicMock()

    monkeypatch.setattr(db_utils, "AsyncIOMotorClient", lambda *a, **k: fake_client)
    monkeypatch.setattr(
        db_utils,
        "_mongo_settings",
        lambda: {
            "uri": "mongodb://localhost:27017/t",
            "database": "t",
            "max_pool": 8,
            "min_pool": 1,
            "connect_timeout_ms": 1000,
            "socket_timeout_ms": 1000,
            "server_selection_timeout_ms": 1000,
        },
    )
    old_client = db_utils.db_manager.mongo_client
    old_db = db_utils.db_manager.mongo_db
    db_utils.db_manager.mongo_client = None
    db_utils.db_manager.mongo_db = None
    try:
        async with db_utils.isolated_mongo_session(max_pool=4, min_pool=1) as db:
            assert db is fake_db
            assert db_utils.get_database() is fake_db
        fake_client.close.assert_called()
        with pytest.raises(RuntimeError, match="未初始化"):
            db_utils.get_database()
    finally:
        db_utils.db_manager.mongo_client = old_client
        db_utils.db_manager.mongo_db = old_db


@pytest.mark.asyncio
async def test_init_mongodb_reuses_on_ping_and_force(monkeypatch):
    conn = db_utils.MongoConnection()
    ping = AsyncMock(return_value={"ok": 1})
    client = MagicMock()
    client.admin = SimpleNamespace(command=ping)
    conn.mongo_client = client
    conn.mongo_db = object()
    await conn.init_mongodb(force=False)
    ping.assert_awaited()

    created = []

    def _factory(*a, **k):
        c = MagicMock()
        c.admin = SimpleNamespace(command=AsyncMock(return_value={"ok": 1}))
        c.__getitem__.return_value = object()
        c.close = MagicMock()
        created.append(c)
        return c

    monkeypatch.setattr(db_utils, "AsyncIOMotorClient", _factory)
    monkeypatch.setattr(
        db_utils,
        "_mongo_settings",
        lambda: {
            "uri": "mongodb://localhost:27017/t",
            "database": "t",
            "max_pool": 8,
            "min_pool": 1,
            "connect_timeout_ms": 1000,
            "socket_timeout_ms": 1000,
            "server_selection_timeout_ms": 1000,
        },
    )
    await conn.init_mongodb(force=True, max_pool=4, min_pool=2)
    assert created
    await conn.close_connections()
    assert conn.mongo_client is None


@pytest.mark.asyncio
async def test_init_skips_when_override_set(monkeypatch):
    conn = db_utils.MongoConnection()
    token = db_utils._db_override.set(object())
    try:
        called = []

        def _boom(*a, **k):
            called.append(1)
            raise AssertionError("should not connect")

        monkeypatch.setattr(db_utils, "AsyncIOMotorClient", _boom)
        await conn.init_mongodb(force=False)
        assert called == []
    finally:
        db_utils._db_override.reset(token)
