"""
API调用缓存工具类（Redis实现）
"""

import json
import logging
import threading
from typing import Dict, Any, Optional

import pandas as pd

from app.core.redis_client import get_sync_redis

logger = logging.getLogger(__name__)

_DEFAULT_EXPIRE = 8 * 3600  # 默认8小时


class ApiCache:
    """API调用缓存，基于Redis，支持按key设置过期时间

    - expire > 0: 写入Redis并设置对应秒数的TTL
    - expire = 0: 不缓存（实时数据，如股价），直接调用原函数
    - expire < 0: 不缓存（同0），直接调用原函数
    """

    def __init__(self):
        self._local_cache: Dict[str, Any] = {}
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()
        self._redis = get_sync_redis()
        self._redis_available = self._check_redis()

    def _check_redis(self) -> bool:
        """检测Redis是否可用"""
        try:
            self._redis.ping()
            logger.info("ApiCache Redis连接成功")
            return True
        except Exception as e:
            logger.warning(f"ApiCache Redis不可用，降级为本地内存缓存: {e}")
            return False

    @staticmethod
    def _redis_key(cache_key: str) -> str:
        return f"api_cache:{cache_key}"

    def _serialize(self, value: Any) -> str:
        """将缓存值序列化为JSON字符串"""
        if isinstance(value, pd.DataFrame):
            return json.dumps({
                "__type__": "DataFrame",
                "data": value.to_json(orient="records", force_ascii=False),
                "columns": list(value.columns),
                "dtypes": {col: str(dt) for col, dt in value.dtypes.items()},
            }, ensure_ascii=False)
        return json.dumps({
            "__type__": "plain",
            "data": value,
        }, ensure_ascii=False, default=str)

    def _deserialize(self, raw: str) -> Any:
        """从JSON字符串反序列化缓存值"""
        obj = json.loads(raw)
        type_tag = obj.get("__type__")

        if type_tag == "DataFrame":
            import io
            df = pd.read_json(io.StringIO(obj["data"]), orient="records")
            return df

        return obj.get("data")

    def call(self, cache_key: str, func, *args, expire: int = _DEFAULT_EXPIRE, **kwargs):
        """调用API并缓存结果

        Args:
            cache_key: 缓存键
            func: 待调用的函数
            expire: 过期时间（秒），默认8小时。0表示不缓存（实时数据）
            *args, **kwargs: 传给func的参数
        """
        # expire <= 0 表示实时数据，不缓存，直接调用
        if expire <= 0:
            result = func(*args, **kwargs)
            self._log_result(cache_key, result)
            return result

        redis_key = self._redis_key(cache_key)

        # 1. 先查本地内存缓存（同一运行周期内最快）
        with self._lock:
            if cache_key in self._local_cache:
                self._hits += 1
                logger.info(f"API缓存命中(本地): {cache_key}")
                return self._local_cache[cache_key]

        # 2. 查Redis缓存
        if self._redis_available:
            try:
                raw = self._redis.get(redis_key)
                if raw is not None:
                    with self._lock:
                        self._hits += 1
                    result = self._deserialize(raw)
                    # 回填本地缓存，加速同周期后续访问
                    with self._lock:
                        self._local_cache[cache_key] = result
                    logger.info(f"API缓存命中(Redis): {cache_key}")
                    return result
            except Exception as e:
                logger.warning(f"API缓存Redis读取异常: {cache_key}, {e}")

        # 3. 缓存未命中，调用原函数
        with self._lock:
            if cache_key in self._local_cache:
                self._hits += 1
                logger.info(f"API缓存命中(本地二次): {cache_key}")
                return self._local_cache[cache_key]
            result = func(*args, **kwargs)
            self._local_cache[cache_key] = result
            self._misses += 1

        # 4. 写入Redis
        if self._redis_available:
            try:
                serialized = self._serialize(result)
                self._redis.setex(redis_key, expire, serialized)
                logger.info(f"API缓存写入Redis: {cache_key}, TTL={expire}s")
            except Exception as e:
                logger.warning(f"API缓存Redis写入异常: {cache_key}, {e}")

        self._log_result(cache_key, result)
        return result

    def _log_result(self, cache_key: str, result: Any):
        """日志记录在锁外，避免长时间持锁"""
        try:
            if isinstance(result, pd.DataFrame):
                logger.info(
                    f"[底层接口数据] {cache_key} -> "
                    f"shape={result.shape}, columns={list(result.columns)}\n"
                    f"{result.head(5).to_string(index=False)}"
                )
            else:
                logger.info(f"[底层接口数据] {cache_key} -> {result}")
        except Exception as _log_err:
            logger.error(f"[底层接口数据] {cache_key} -> 日志记录失败: {_log_err}")
