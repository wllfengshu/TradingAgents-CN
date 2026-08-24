"""zstock 数据库初始化工具（仅 MongoDB，不依赖 Redis）。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def init_zstock_database() -> None:
    """初始化 zstock 所需数据库连接（仅 MongoDB）。"""
    import app.core.database as db_module
    from app.core.database import db_manager

    await db_manager.init_mongodb()
    db_module.mongo_client = db_manager.mongo_client
    db_module.mongo_db = db_manager.mongo_db


async def close_zstock_database() -> None:
    """关闭 zstock 数据库连接。"""
    from app.core.database import close_database

    await close_database()
