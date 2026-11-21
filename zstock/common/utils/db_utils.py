"""zstock 的 MongoDB 连接。查询走 data_management.query_service。"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

mongo_client: Optional[AsyncIOMotorClient] = None
mongo_db = None

# 隔离线程/事件循环用的临时库句柄；绝不写入全局 db_manager，避免主循环拿到跨 loop 的 Motor。
_db_override: ContextVar[Any] = ContextVar("zstock_db_override", default=None)


def _load_project_env() -> None:
    """把项目根目录 .env 填进环境变量，不覆盖进程里已经设置的值。

    仅支持简单 KEY=VALUE 格式（不含多行值、转义字符、export 前缀）。
    项目未引入 python-dotenv，如需高级特性再迁移。
    """
    env_path = Path(__file__).resolve().parents[3] / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _mongo_settings() -> Dict[str, object]:
    _load_project_env()
    host = os.environ.get("MONGODB_HOST", "localhost")
    port = _env_int("MONGODB_PORT", 27017)
    username = os.environ.get("MONGODB_USERNAME", "")
    password = os.environ.get("MONGODB_PASSWORD", "")
    database = os.environ.get("MONGODB_DATABASE", "tradingagents")
    auth_source = os.environ.get("MONGODB_AUTH_SOURCE", "admin")
    if username and password:
        uri = (
            f"mongodb://{username}:{password}@{host}:{port}/{database}"
            f"?authSource={auth_source}"
        )
    else:
        uri = f"mongodb://{host}:{port}/{database}"
    return {
        "uri": uri,
        "database": database,
        "max_pool": _env_int("MONGO_MAX_CONNECTIONS", 60),
        "min_pool": _env_int("MONGO_MIN_CONNECTIONS", 5),
        "connect_timeout_ms": _env_int("MONGO_CONNECT_TIMEOUT_MS", 30000),
        "socket_timeout_ms": _env_int("MONGO_SOCKET_TIMEOUT_MS", 60000),
        "server_selection_timeout_ms": _env_int("MONGO_SERVER_SELECTION_TIMEOUT_MS", 5000),
    }


class MongoConnection:
    """MongoDB 连接。调用 init_mongodb 之后，用 get_database 取库。"""

    def __init__(self) -> None:
        self.mongo_client: Optional[AsyncIOMotorClient] = None
        self.mongo_db = None

    async def init_mongodb(
        self,
        *,
        force: bool = False,
        max_pool: Optional[int] = None,
        min_pool: Optional[int] = None,
    ) -> None:
        """初始化连接。若已连接且 force=False，仅 ping 复用，避免重复开大连接池。

        隔离会话（ContextVar 已注入）下直接返回，避免在错误的 event loop 上 ping 全局客户端。
        """
        global mongo_client, mongo_db
        if _db_override.get() is not None and not force:
            return
        if self.mongo_client is not None and not force:
            try:
                await self.mongo_client.admin.command("ping")
                return
            except Exception:
                logger.warning("已有 Mongo 客户端 ping 失败，将重建连接")
        cfg = _mongo_settings()
        pool_max = int(max_pool) if max_pool is not None else int(cfg["max_pool"])
        pool_min = int(min_pool) if min_pool is not None else int(cfg["min_pool"])
        pool_min = max(0, min(pool_min, pool_max))
        logger.info("正在初始化 MongoDB 连接")
        try:
            self.mongo_client = AsyncIOMotorClient(
                cfg["uri"],
                maxPoolSize=pool_max,
                minPoolSize=pool_min,
                maxIdleTimeMS=30000,
                serverSelectionTimeoutMS=cfg["server_selection_timeout_ms"],
                connectTimeoutMS=cfg["connect_timeout_ms"],
                socketTimeoutMS=cfg["socket_timeout_ms"],
            )
            self.mongo_db = self.mongo_client[cfg["database"]]
            await self.mongo_client.admin.command("ping")
            mongo_client = self.mongo_client
            mongo_db = self.mongo_db
            logger.info(
                "MongoDB 已连接，库 %s，连接池 %s-%s",
                cfg["database"],
                pool_min,
                pool_max,
            )
        except Exception as e:
            logger.error("MongoDB 连接失败: %s", e)
            raise

    async def close_connections(self) -> None:
        global mongo_client, mongo_db
        if self.mongo_client is not None:
            try:
                self.mongo_client.close()
                logger.info("MongoDB 连接已关闭")
            except Exception as e:
                logger.error("关闭 MongoDB 连接时出错: %s", e)
        self.mongo_client = None
        self.mongo_db = None
        mongo_client = None
        mongo_db = None


db_manager = MongoConnection()


def get_database():
    """返回当前可用的 MongoDB 数据库。

    优先使用隔离会话注入的 ContextVar；否则用进程级 db_manager。
    未初始化时抛出 RuntimeError。
    """
    override = _db_override.get()
    if override is not None:
        return override
    if db_manager.mongo_client is None:
        raise RuntimeError("MongoDB客户端未初始化")
    return db_manager.mongo_db


@asynccontextmanager
async def isolated_mongo_session(
    *,
    max_pool: int = 8,
    min_pool: int = 1,
) -> AsyncIterator[Any]:
    """在独立事件循环里开临时 Mongo 连接，仅通过 ContextVar 注入。

    不改动全局 db_manager / 模块级 mongo_*，主循环轮询 API 可继续用原客户端。
    """
    cfg = _mongo_settings()
    pool_max = max(1, int(max_pool))
    pool_min = max(0, min(int(min_pool), pool_max))
    client = AsyncIOMotorClient(
        cfg["uri"],
        maxPoolSize=pool_max,
        minPoolSize=pool_min,
        maxIdleTimeMS=30000,
        serverSelectionTimeoutMS=cfg["server_selection_timeout_ms"],
        connectTimeoutMS=cfg["connect_timeout_ms"],
        socketTimeoutMS=cfg["socket_timeout_ms"],
    )
    db = client[cfg["database"]]
    await client.admin.command("ping")
    token = _db_override.set(db)
    try:
        yield db
    finally:
        _db_override.reset(token)
        try:
            client.close()
        except Exception:
            pass


async def init_zstock_database() -> None:
    """初始化 zstock 所需的 MongoDB 连接。"""
    await db_manager.init_mongodb()


async def close_zstock_database() -> None:
    """关闭 zstock 数据库连接。"""
    await db_manager.close_connections()
