"""
MongoDB 的数据服务

# 必须要先初始化数据库
from app.core.database import init_database
await init_database()

"""

import logging
from typing import Optional, Dict, List
from app.core.database import get_database, init_database

logger = logging.getLogger(__name__)


class DatabaseService:
    """
    数据源管理器

    职责：
    - 直接操作 MongoDB
    - 提供数据查询、存储接口
    """

    def __init__(self):
        """初始化数据源管理器"""
        # 不在这里调用 init_database()，因为它是异步函数
        # 而构造函数必须是同步的
        # 数据库需要在外部（如在 FastAPI 应用启动或测试 fixture 中）调用 init_database()
        # 这里只检查是否已初始化
        self.db = get_database()
        if self.db is None:
            raise Exception("❌ 数据源管理器初始化失败")
        logger.info("✅ 数据源管理器初始化成功")

    # ==================== 数据查询方法 ====================

    async def query(self, collection: str, query: Dict = None, **kwargs) -> List[Dict]:
        """
        查询数据

        Args:
            collection: 集合名称
            query: 查询条件
            **kwargs: 其他查询参数（skip, limit, sort 等）

        Returns:
            查询结果列表
        """
        if query is None:
            query = {}
        try:
            result = await self.db[collection].find(query, **kwargs).to_list(None)
            logger.debug(f"📊 查询 {collection}: 找到 {len(result)} 条记录")
            return result
        except Exception as e:
            logger.error(f"❌ 查询失败 {collection}: {e}")
            raise

    async def query_one(self, collection: str, query: Dict = None, **kwargs) -> Optional[Dict]:
        """
        查询单条数据

        Args:
            collection: 集合名称
            query: 查询条件

        Returns:
            查询结果或 None
        """
        if query is None:
            query = {}
        try:
            result = await self.db[collection].find_one(query, **kwargs)
            logger.debug(f"📊 查询单条 {collection}: {'找到' if result else '未找到'}")
            return result
        except Exception as e:
            logger.error(f"❌ 查询单条失败 {collection}: {e}")
            raise

    # ==================== 数据插入方法 ====================

    async def insert_one(self, collection: str, document: Dict) -> str:
        """
        插入单条数据

        Args:
            collection: 集合名称
            document: 文档

        Returns:
            插入的文档 ID
        """
        try:
            result = await self.db[collection].insert_one(document)
            logger.info(f"✅ 插入 {collection}: {result.inserted_id}")
            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"❌ 插入失败 {collection}: {e}")
            raise

    async def insert_many(self, collection: str, documents: List[Dict]) -> List[str]:
        """
        批量插入数据

        Args:
            collection: 集合名称
            documents: 文档列表

        Returns:
            插入的文档 ID 列表
        """
        if not documents:
            return []
        try:
            result = await self.db[collection].insert_many(documents)
            logger.info(f"✅ 批量插入 {collection}: {len(result.inserted_ids)} 条")
            return [str(_id) for _id in result.inserted_ids]
        except Exception as e:
            logger.error(f"❌ 批量插入失败 {collection}: {e}")
            raise

    # ==================== 数据更新方法 ====================

    async def update_one(self, collection: str, query: Dict, update: Dict) -> int:
        """
        更新单条数据

        Args:
            collection: 集合名称
            query: 查询条件
            update: 更新内容

        Returns:
            修改的文档数
        """
        try:
            result = await self.db[collection].update_one(query, {"$set": update})
            logger.info(f"✅ 更新 {collection}: {result.modified_count} 条")
            return result.modified_count
        except Exception as e:
            logger.error(f"❌ 更新失败 {collection}: {e}")
            raise

    async def update_many(self, collection: str, query: Dict, update: Dict) -> int:
        """
        批量更新数据

        Args:
            collection: 集合名称
            query: 查询条件
            update: 更新内容

        Returns:
            修改的文档数
        """
        try:
            result = await self.db[collection].update_many(query, {"$set": update})
            logger.info(f"✅ 批量更新 {collection}: {result.modified_count} 条")
            return result.modified_count
        except Exception as e:
            logger.error(f"❌ 批量更新失败 {collection}: {e}")
            raise

    # ==================== 数据删除方法 ====================

    async def delete_one(self, collection: str, query: Dict) -> int:
        """
        删除单条数据

        Args:
            collection: 集合名称
            query: 查询条件

        Returns:
            删除的文档数
        """
        try:
            result = await self.db[collection].delete_one(query)
            logger.info(f"✅ 删除 {collection}: {result.deleted_count} 条")
            return result.deleted_count
        except Exception as e:
            logger.error(f"❌ 删除失败 {collection}: {e}")
            raise

    async def delete_many(self, collection: str, query: Dict) -> int:
        """
        批量删除数据

        Args:
            collection: 集合名称
            query: 查询条件

        Returns:
            删除的文档数
        """
        try:
            result = await self.db[collection].delete_many(query)
            logger.info(f"✅ 批量删除 {collection}: {result.deleted_count} 条")
            return result.deleted_count
        except Exception as e:
            logger.error(f"❌ 批量删除失败 {collection}: {e}")
            raise

    # ==================== 统计方法 ====================

    async def count(self, collection: str, query: Dict = None) -> int:
        """
        统计文档数

        Args:
            collection: 集合名称
            query: 查询条件

        Returns:
            文档数
        """
        if query is None:
            query = {}
        try:
            count = await self.db[collection].count_documents(query)
            logger.debug(f"📊 统计 {collection}: {count} 条")
            return count
        except Exception as e:
            logger.error(f"❌ 统计失败 {collection}: {e}")
            raise

    # ==================== 集合操作方法 ====================

    async def drop_collection(self, collection: str) -> None:
        """删除集合"""
        try:
            await self.db[collection].drop()
            logger.info(f"✅ 删除集合: {collection}")
        except Exception as e:
            logger.error(f"❌ 删除集合失败 {collection}: {e}")
            raise

    async def list_collections(self) -> List[str]:
        """获取所有集合名称"""
        try:
            collections = await self.db.list_collection_names()
            logger.debug(f"📋 集合列表: {collections}")
            return collections
        except Exception as e:
            logger.error(f"❌ 获取集合列表失败: {e}")
            return []


# 全局数据源管理器实例
_database_service: Optional[DatabaseService] = None
_database_service_lock = __import__('threading').Lock()


def get_database_service() -> DatabaseService:
    """获取全局数据源管理器实例。"""
    global _database_service
    if _database_service is not None:
        return _database_service
    with _database_service_lock:
        if _database_service is None:
            _database_service = DatabaseService()
    return _database_service
