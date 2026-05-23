"""
API调用缓存工具类
"""

import logging
import threading
from typing import Dict, Any

import pandas as pd

logger = logging.getLogger(__name__)

class ApiCache:
    """API调用缓存，在单次AI选股运行期间缓存akshare接口调用结果，运行结束后清空
    """

    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()  # Fix: 保证线程安全

    def call(self, cache_key: str, func, *args, **kwargs):
        """调用API并缓存结果，如果已有缓存则直接返回（线程安全）"""
        with self._lock:
            if cache_key in self._cache:
                self._hits += 1
                logger.info(f"API缓存命中: {cache_key}")
                return self._cache[cache_key]
            # 在锁内调用 API，防止并发时同一 key 被重复请求（TOCTOU）
            result = func(*args, **kwargs)
            self._cache[cache_key] = result
            self._misses += 1

        # 日志记录在锁外，避免长时间持锁
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
        return result

    def clear(self):
        if self._hits > 0 or self._misses > 0:
            logger.info(f"API缓存清空，命中{self._hits}次，未命中{self._misses}次")
        self._cache.clear()
        self._hits = 0
        self._misses = 0
