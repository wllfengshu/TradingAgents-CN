"""
数据查询服务

数据源优先级：
    MongoDB（持久存储）

表结构（4 张）：
    zstock_stock_info    — 股票元数据（is_mainboard / is_st）
    zstock_ohlcv         — 全周期行情，period: 'D'/'W'/'M'
    zstock_capital_flow  — 个股资金流时间序列
    zstock_sector        — 板块定义 + 成分股内嵌
"""

import logging
import re
from typing import Any, Optional, Dict, List, Tuple

import pandas as pd

from zstock.common.utils.common_utils import normalize_code, normalize_date, to_yyyymmdd
from .database_service import get_database_service

logger = logging.getLogger(__name__)

# ===================== MongoDB 集合名常量 =====================
COL_STOCK_INFO   = 'zstock_stock_info'
COL_OHLCV        = 'zstock_ohlcv'
COL_CAPITAL_FLOW = 'zstock_capital_flow'
COL_SECTOR       = 'zstock_sector'

# period 映射：query_service 公开 API 用 'daily'/'weekly'/'monthly'，落库用 'D'/'W'/'M'
_PERIOD_MAP = {'daily': 'D', 'weekly': 'W', 'monthly': 'M'}


class DataQueryService:
    """统一数据查询服务：MongoDB 优先，未命中时回源并写回。"""

    def __init__(self):
        self.database_service = get_database_service()
        logger.info("✅ 查询服务初始化成功")

    async def ensure_indexes(self) -> None:
        """在所有集合上建立必要索引（幂等，可重复调用）。"""
        from pymongo import ASCENDING, IndexModel
        db = self.database_service.db
        # zstock_ohlcv: upsert filter key + 按 code+日期范围查询
        await db[COL_OHLCV].create_indexes([
            IndexModel([('code', ASCENDING), ('trade_date', ASCENDING), ('period', ASCENDING)], unique=True, name='code_date_period'),
            IndexModel([('code', ASCENDING), ('period', ASCENDING), ('trade_date', ASCENDING)], name='code_period_date'),
        ])
        # zstock_stock_info: 主键 + 主板过滤
        await db[COL_STOCK_INFO].create_indexes([
            IndexModel([('code', ASCENDING)], unique=True, name='code_unique'),
            IndexModel([('is_mainboard', ASCENDING), ('is_st', ASCENDING)], name='mainboard_st'),
        ])
        # zstock_capital_flow: code+date+period 唯一（同一股票同一天可有 today/3day/5day/10day 四条）
        try:
            await db[COL_CAPITAL_FLOW].drop_index('code_date')
        except Exception:
            pass  # 旧索引不存在则跳过
        await db[COL_CAPITAL_FLOW].create_indexes([
            IndexModel([('code', ASCENDING), ('trade_date', ASCENDING), ('period', ASCENDING)], unique=True, name='code_date_period'),
        ])
        # zstock_sector
        await db[COL_SECTOR].create_indexes([
            IndexModel([('sector_code', ASCENDING), ('source', ASCENDING)], unique=True, name='sector_source'),
        ])
        logger.info("✅ MongoDB 索引已就绪")

    # =========================== OHLCV 行情 ===========================

    async def get_ohlcv(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        period: str = 'daily',
    ) -> Tuple[Optional[pd.DataFrame], str]:
        """获取市场行情数据（日/周/月线）。
        """
        code = normalize_code(symbol)
        p = _PERIOD_MAP.get(period, 'D')
        norm_start = normalize_date(start_date)
        norm_end   = normalize_date(end_date)

        docs = await self.database_service.query(
            COL_OHLCV,
            {'code': code, 'period': p,
             'trade_date': {'$gte': norm_start, '$lte': norm_end}},
            sort=[('trade_date', 1)],
        )
        if not docs:
            raise ValueError(f"❌ 无数据: {symbol} ({start_date} - {end_date})")

        df = pd.DataFrame(docs)
        df.drop(columns=[c for c in ('_id',) if c in df.columns], inplace=True)
        return df, 'mongodb'


    async def get_ohlcv_batch(self, codes: List[str], start_date: str, end_date: str, period: str = 'daily') -> Dict[str, pd.DataFrame]:
        """一次性查多只股票的 OHLCV，返回 {code: DataFrame}。"""
        if not codes:
            return {}
        p = _PERIOD_MAP.get(period, 'D')
        norm_start = normalize_date(start_date)
        norm_end = normalize_date(end_date)
        try:
            docs = await self.database_service.query(
                COL_OHLCV,
                {'code': {'$in': codes}, 'period': p,
                 'trade_date': {'$gte': norm_start, '$lte': norm_end}},
                sort=[('code', 1), ('trade_date', 1)],
            )
            if not docs:
                return {}
            df = pd.DataFrame(docs)
            df.drop(columns=[c for c in ('_id', 'period') if c in df.columns], inplace=True)
            return {str(code): g.reset_index(drop=True) for code, g in df.groupby('code')}
        except Exception as e:
            logger.error(f"OHLCV batch 查询失败: {e}")

        raise ValueError(f"❌ get_ohlcv_batch无法获取数据: {codes} ({start_date} - {end_date})")

    # =========================== 股票元数据 ===========================

    async def get_stock_info(self,symbol: str,) -> Tuple[Optional[Dict], str]:
        """获取股票基本信息。"""
        doc = await self.database_service.query_one(
            COL_STOCK_INFO, {'code': normalize_code(symbol)}
        )
        if doc:
            doc.pop('_id', None)
            return doc, 'mongodb'

        raise ValueError(f"❌ get_stock_info无法获取数据: {symbol}")

    async def get_all_stocks(self) -> Tuple[Optional[List[Dict]], str]:
        """获取全 A 股完整信息列表（含创业板/科创板/北交所，含 ST）。

        返回 zstock_stock_info 集合的完整文档列表，包含：
            code, name, is_mainboard, is_st 等全部字段。
        调用方可从中直接提取 codes / stock_infos，无需再单独查询。
        """
        try:
            docs = await self.database_service.query(
                COL_STOCK_INFO,
                {},
                projection={'_id': 0}
            )
            if docs:
                return [d for d in docs if d.get('code')], 'mongodb'
        except Exception as e:
            logger.error(f"从 MongoDB 获取全市场股票列表失败: {e}")

        raise ValueError(f"❌ get_all_stocks无法获取数据")


    # =========================== 资金流 ===========================

    async def get_capital_flow(self,symbol: str,trade_date: str) -> Tuple[Optional[Dict], str]:
        """获取股票资金流向数据。"""
        code    = normalize_code(symbol)
        td_norm = to_yyyymmdd(trade_date)

        try:
            doc = await self.database_service.query_one(
                COL_CAPITAL_FLOW, {'code': code, 'trade_date': td_norm}
            )
            if doc:
                doc.pop('_id', None)
                return doc, 'mongodb'
        except Exception as e:
            logger.error(f"从 MongoDB 获取资金流数据失败: {e}")

        raise ValueError(f"❌ get_capital_flow无数据: {symbol} ({trade_date})")

    async def get_capital_flow_batch(self, codes: List[str], trade_date: str) -> Dict[str, Dict]:
        """一次性查多只股票的资金流，返回 {code: dict}。MongoDB取。"""
        if not codes:
            return {}
        td_norm = to_yyyymmdd(trade_date)

        # MongoDB 查询
        result: Dict[str, Dict] = {}
        try:
            docs = await self.database_service.query(
                COL_CAPITAL_FLOW,
                {'code': {'$in': codes}, 'trade_date': td_norm},
            )
            for doc in (docs or []):
                code = doc.get('code')
                if code:
                    doc.pop('_id', None)
                    result[code] = doc
            return result
        except Exception as e:
            logger.error(f"capital_flow batch MongoDB 查询失败: {e}")

        raise ValueError(f"❌ get_capital_flow_batch无法获取数据")

    async def get_capital_flow_recent_days(self, codes: List[str], end_date: str, days: int = 5) -> Dict[str, List[Dict]]:
        """
        查询多只股票近 N 个交易日的资金流数据。

        Args:
            codes: 股票代码列表
            end_date: 结束日期（含）
            days: 回看天数（取最近 N 条记录）

        Returns:
            {code: [day1_doc, day2_doc, ...]}，按 trade_date 升序排列
        """
        if not codes:
            return {}
        td_norm = to_yyyymmdd(end_date)

        result: Dict[str, List[Dict]] = {}
        try:
            docs = await self.database_service.query(
                COL_CAPITAL_FLOW,
                {'code': {'$in': codes}, 'trade_date': {'$lte': td_norm}},
                sort=[('trade_date', -1)],
                limit=len(codes) * days,
            )
            # 按 code 分组，每只取最近 days 条
            from collections import defaultdict
            grouped: Dict[str, List[Dict]] = defaultdict(list)
            for doc in (docs or []):
                code = doc.get('code')
                if code and len(grouped[code]) < days:
                    doc.pop('_id', None)
                    grouped[code].append(doc)

            # 反转为升序
            for code, doc_list in grouped.items():
                result[code] = list(reversed(doc_list))
            return result
        except Exception as e:
            logger.error(f"capital_flow recent_days 查询失败: {e}")
            raise


    # =========================== 板块 ===========================

    async def get_sector_list(self, prefix: str = 'SW2') -> Tuple[Optional[List[Dict]], str]:
        """获取板块列表（元数据：sector_code / sector_name / sector_type / stocks）。含成分股

        Args:
            prefix: 板块前缀过滤，默认 'SW2'（申万二级行业，券商APP标准分类）。
                    常用值: 'SW1'(申万一级) / 'SW2'(申万二级) / 'SW3'(申万三级)
                           'THY2'(同花顺二级) / 'THY3'(同花顺三级)
                    传 None 或 '' 则返回全部板块。
        """
        try:
            query: Dict[str, Any] = {'source': 'xtquant'}
            if prefix:
                query['sector_code'] = {'$regex': f'^{re.escape(prefix)}'}
            docs = await self.database_service.query(
                COL_SECTOR, query,
                projection={'sector_code': 1, 'sector_name': 1, 'sector_type': 1, 'stocks': 1, '_id': 0},
                limit=10000,
            )
            if docs:
                return docs, 'mongodb'
        except Exception as e:
            logger.error(f"从 MongoDB 获取板块列表失败: {e}")

        raise ValueError(f"❌ get_sector_list无法获取数据")

    async def get_sector_stocks(self,sector_code: str) -> Tuple[Optional[List[str]], str]:
        """获取板块成分股代码列表（纯 6 位代码字符串）。
        同一 sector_code 在不同来源下成分不同，用 source 隔离。

        Args:
            sector_code: 板块代码
        """
        try:
            doc = await self.database_service.query_one(
                COL_SECTOR, {'sector_code': sector_code, 'source': 'xtquant'},
                projection={'stocks': 1, '_id': 0},
            )
            if doc and doc.get('stocks'):
                return doc['stocks'], 'mongodb'
        except Exception as e:
            logger.error(f"从 MongoDB 获取板块成分股失败: {e}")

        raise ValueError(f"❌ get_sector_stocks无法获取数据")

    async def get_sector_stocks_batch(self, sector_codes: List[str]) -> Dict[str, List[str]]:
        """一次性查多个板块的成分股，返回 {sector_code: [codes]}。MongoDB 优先，未命中批量回源。

        Args:
            sector_codes: 板块代码列表
        """
        if not sector_codes:
            return {}

        # MongoDB 查询
        result: Dict[str, List[str]] = {}
        try:
            docs = await self.database_service.query(
                COL_SECTOR,
                {'sector_code': {'$in': sector_codes}, 'source': 'xtquant'},
                projection={'sector_code': 1, 'stocks': 1, '_id': 0},
            )
            for doc in (docs or []):
                if doc.get('stocks'):
                    result[doc['sector_code']] = doc['stocks']
            return result
        except Exception as e:
            logger.error(f"sector_stocks batch MongoDB 查询失败: {e}")

        raise ValueError(f"❌ get_sector_stocks_batch无法获取数据")


# ==========================================================================

# 全局单例
_data_query_service: Optional[DataQueryService] = None


def get_data_query_service() -> DataQueryService:
    global _data_query_service
    if _data_query_service is None:
        _data_query_service = DataQueryService()
    return _data_query_service
