"""zstock 的 MongoDB 连接。查询走 data_management.query_service。"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, Optional

from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

mongo_client: Optional[AsyncIOMotorClient] = None
mongo_db = None


def _load_project_env() -> None:
    """把项目根目录 .env 填进环境变量，不覆盖进程里已经设置的值。"""
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

    async def init_mongodb(self) -> None:
        global mongo_client, mongo_db
        cfg = _mongo_settings()
        logger.info("正在初始化 MongoDB 连接")
        try:
            self.mongo_client = AsyncIOMotorClient(
                cfg["uri"],
                maxPoolSize=cfg["max_pool"],
                minPoolSize=cfg["min_pool"],
                maxIdleTimeMS=30000,
                serverSelectionTimeoutMS=cfg["server_selection_timeout_ms"],
                connectTimeoutMS=cfg["connect_timeout_ms"],
                socketTimeoutMS=cfg["socket_timeout_ms"],
            )
            self.mongo_db = self.mongo_client[cfg["database"]]
            await self.mongo_client.admin.command("ping")
            mongo_client = self.mongo_client
            mongo_db = self.mongo_db
            logger.info("MongoDB 已连接，库 %s，连接池 %s-%s", cfg["database"], cfg["min_pool"], cfg["max_pool"])
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
    """返回已初始化的 MongoDB 数据库。未初始化时抛出 RuntimeError。"""
    if db_manager.mongo_client is None:
        raise RuntimeError("MongoDB客户端未初始化")
    return db_manager.mongo_db


async def init_zstock_database() -> None:
    """初始化 zstock 所需的 MongoDB 连接。"""
    await db_manager.init_mongodb()


async def close_zstock_database() -> None:
    """关闭 zstock 数据库连接。"""
    await close_database()


async def close_database() -> None:
    """关闭 MongoDB 连接。"""
    await db_manager.close_connections()
