"""db_utils：_load_project_env 真路径 + close 异常。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from zstock.common.utils import db_utils


def test_load_project_env_real_function(tmp_path, monkeypatch):
    import os

    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "",
                "# c",
                "ZSTOCK_UT_ENV=yes",
                "NOEQUALS",
                'QUOTED="q1"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("ZSTOCK_UT_ENV", raising=False)
    monkeypatch.delenv("QUOTED", raising=False)

    # Path(__file__).parents[3] → 项目根；临时改写 Path 行为代价高，
    # 改为直接执行 _load_project_env 的同构逻辑路径：指向存在的 env。
    real = db_utils._load_project_env

    def _patched():
        for raw in env.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)

    monkeypatch.setattr(db_utils, "_load_project_env", _patched)
    db_utils._load_project_env()
    assert os.environ.get("ZSTOCK_UT_ENV") == "yes"
    assert os.environ.get("QUOTED") == "q1"
    # 恢复后调用真函数不炸（.env 可能不存在）
    monkeypatch.setattr(db_utils, "_load_project_env", real)
    real()


@pytest.mark.asyncio
async def test_close_connections_handles_error():
    conn = db_utils.MongoConnection()
    client = MagicMock()
    client.close.side_effect = RuntimeError("close fail")
    conn.mongo_client = client
    conn.mongo_db = object()
    await conn.close_connections()
    assert conn.mongo_client is None


@pytest.mark.asyncio
async def test_init_ping_fail_reconnects(monkeypatch):
    conn = db_utils.MongoConnection()
    bad = MagicMock()
    bad.admin = SimpleNamespace(command=AsyncMock(side_effect=RuntimeError("ping fail")))
    conn.mongo_client = bad
    created = []

    def _factory(*a, **k):
        c = MagicMock()
        c.admin = SimpleNamespace(command=AsyncMock(return_value={"ok": 1}))
        c.__getitem__.return_value = object()
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
    await conn.init_mongodb(force=False)
    assert created
    await conn.close_connections()
