"""
API调用缓存工具类（Redis实现）
"""
import json
import logging
import threading
from typing import Dict, Any
from redis import Redis
from app.core.config import settings
import pandas as pd

logger = logging.getLogger(__name__)

_DEFAULT_EXPIRE = 8 * 3600  # 默认8小时
_redis = None
_local_cache: Dict[str, Any] = {}
_stats = {"hits": 0, "misses": 0}
_lock = threading.Lock()
_redis_unavailable_logged = False


def call(cache_key: str, func, *args, expire: int = _DEFAULT_EXPIRE, **kwargs):
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
        _log_result(cache_key, result)
        return result

    redis_key = _redis_key(cache_key)
    redis_client = _get_redis()

    # 1. 先查本地内存缓存（同一运行周期内最快）
    with _lock:
        if cache_key in _local_cache:
            _stats["hits"] += 1
            logger.info(f"API缓存命中(本地): {cache_key}")
            return _local_cache[cache_key]

    # 2. 查Redis缓存
    if redis_client is not None:
        try:
            raw = redis_client.get(redis_key)
            if raw is not None:
                with _lock:
                    _stats["hits"] += 1
                result = _deserialize(raw)
                # 回填本地缓存，加速同周期后续访问
                with _lock:
                    _local_cache[cache_key] = result
                logger.info(f"API缓存命中(Redis): {cache_key}")
                return result
        except Exception as e:
            logger.warning(f"API缓存Redis读取异常: {cache_key}, {e}")

    # 3. 缓存未命中，调用原函数
    with _lock:
        if cache_key in _local_cache:
            _stats["hits"] += 1
            logger.info(f"API缓存命中(本地二次): {cache_key}")
            return _local_cache[cache_key]
        result = func(*args, **kwargs)
        _log_result(cache_key, result)
        _local_cache[cache_key] = result
        _stats["misses"] += 1

    # 4. 写入Redis
    if redis_client is not None:
        try:
            serialized = _serialize(result)
            redis_client.setex(redis_key, expire, serialized)
            logger.info(f"API缓存写入Redis: {cache_key}, TTL={expire}s")
        except Exception as e:
            logger.error(f"API缓存Redis写入异常: {cache_key}, {e}")

    _log_result(cache_key, result)
    return result


def _get_redis():
    global _redis, _redis_unavailable_logged
    if _redis is not None:
        return _redis
    try:
        # api_cache 为同步调用链，使用同步 Redis 客户端避免事件循环不匹配
        _redis = Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=10,
        )
        _redis.ping()
    except Exception as e:
        if not _redis_unavailable_logged:
            logger.error(f"API缓存Redis不可用，将降级为仅本地缓存: {e}")
            _redis_unavailable_logged = True
        return None
    return _redis

def _redis_key(cache_key: str) -> str:
    return f"api_cache:{cache_key}"

def _serialize(value: Any) -> str:
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

def _deserialize(raw: Any) -> Any:
    """从JSON字符串反序列化缓存值"""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    obj = json.loads(raw)
    type_tag = obj.get("__type__")

    if type_tag == "DataFrame":
        import io
        df = pd.read_json(io.StringIO(obj["data"]), orient="records")
        return df

    return obj.get("data")

def _log_result(cache_key: str, result: Any):
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
