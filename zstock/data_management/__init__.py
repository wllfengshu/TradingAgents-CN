"""
zstock 数据管理模块

连接在 common.utils.db_utils。查询和落库走 DataQueryService。
"""

from .query_service import DataQueryService

__all__ = [
    "DataQueryService",
]
