"""Redis 缓存服务"""

import logging
import json
from typing import Any, Optional, Dict
from app.core.redis_client import get_redis_service

logger = logging.getLogger(__name__)


class CacheService:
    """Redis缓存管理器。"""

    def __init__(self):
        """初始化缓存管理器，自动获取 Redis 连接"""
        try:
            redis_service = get_redis_service()
        except RuntimeError:
            # Redis 未初始化，稍后再初始化
            redis_service = None

        self.redis_client = redis_service.redis if redis_service else None
        self.stats = {
            'redis_hit': 0,
            'redis_miss': 0,
        }
        logger.info("✅ 缓存管理器初始化成功")

    async def get(self, cache_key: str) -> Optional[Any]:
        """从 Redis 获取缓存数据。"""
        if not self.redis_client:
            self.stats['redis_miss'] += 1
            return None

        try:
            data = await self.redis_client.get(cache_key)
            if not data:
                self.stats['redis_miss'] += 1
                return None

            self.stats['redis_hit'] += 1
            logger.debug(f"Redis 缓存命中: {cache_key}")

            if isinstance(data, bytes):
                data = data.decode('utf-8')
            return json.loads(data) if isinstance(data, str) else data
        except Exception as e:
            logger.warning(f"Redis 查询失败: {e}")
            self.stats['redis_miss'] += 1
            return None

    async def set(self,
                  cache_key: str,
                  data: Any,
                  ttl_seconds: int = 600) -> bool:
        """写入 Redis 缓存。"""
        if not self.redis_client:
            logger.warning("Redis 客户端未初始化，跳过缓存写入")
            return False

        try:
            await self.redis_client.setex(
                cache_key,
                ttl_seconds,
                json.dumps(data, default=str, ensure_ascii=False),
            )
            logger.debug(f"写入 Redis 缓存: {cache_key} (TTL: {ttl_seconds}s)")
            return True
        except Exception as e:
            logger.warning(f"写入 Redis 失败: {e}")
            return False

    async def delete(self, cache_key: str) -> bool:
        """删除缓存项"""
        if not self.redis_client:
            return False

        try:
            await self.redis_client.delete(cache_key)
            return True
        except Exception as e:
            logger.warning(f"删除 Redis 缓存失败: {e}")
            return False

    def get_stats(self) -> Dict[str, int]:
        """获取缓存统计信息"""
        return {
            **self.stats,
            'total_hits': self.stats['redis_hit'],
            'total_misses': self.stats['redis_miss'],
        }

    def reset_stats(self) -> None:
        """重置统计计数器"""
        for key in self.stats:
            self.stats[key] = 0
        logger.info("📊 缓存统计已重置")


# 全局缓存实例
_cache_service: Optional[CacheService] = None
_cache_service_lock = __import__('threading').Lock()


def get_cache_service() -> CacheService:
    """获取全局缓存实例（单例）。"""
    global _cache_service
    if _cache_service is not None:
        return _cache_service
    with _cache_service_lock:
        if _cache_service is None:
            _cache_service = CacheService()
    return _cache_service
