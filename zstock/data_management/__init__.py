"""
zstock 数据管理模块

DataQueryService 提供统一接口，优先从 MongoDB 获取数据，若 MongoDB 无数据则回源到 xtquant 并写回 MongoDB。
DatabaseService 负责管理 MongoDB 的访问。
"""

# 核心类
from .query_service import DataQueryService
from .database_service import DatabaseService

__all__ = [
    # 核心类
    'DataQueryService',
    'DatabaseService',
]
