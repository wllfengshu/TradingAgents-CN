"""
数据查询服务（MongoDB）。

集合：
    zstock_stock_info       — 股票元数据（is_mainboard / is_st）
    zstock_ohlcv            — 行情，period: 'D' / 'W' / 'M' / '5m' / '1m'
    zstock_capital_flow     — 个股资金流（L2_daily / today / ...）
    zstock_sector           — 板块定义 + 成分股内嵌
    zstock_factor_market    — M1 市场因子预计算
    zstock_factor_sector    — M2 板块因子预计算
    zstock_factor_dragon    — M3 龙头因子预计算
    zstock_factor_force     — M4 合力因子预计算

日期约定：库内 trade_date 统一为 YYYY-MM-DD；入参可用 YYYYMMDD，内部 normalize_date。
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Callable, ClassVar, Dict, List, Optional, Tuple

import pandas as pd

from zstock.common.utils.common_utils import normalize_code, normalize_date

from zstock.common.utils.db_utils import get_database


def _default_query_concurrency() -> int:
    return max(4, min(12, (os.cpu_count() or 8) // 2))

logger = logging.getLogger(__name__)

# ===================== MongoDB 集合名常量 =====================
COL_STOCK_INFO = "zstock_stock_info"
COL_OHLCV = "zstock_ohlcv"
COL_CAPITAL_FLOW = "zstock_capital_flow"
COL_SECTOR = "zstock_sector"
COL_LHB = "zstock_lhb"

COL_FACTOR_MARKET = "zstock_factor_market"
COL_FACTOR_SECTOR = "zstock_factor_sector"
COL_FACTOR_DRAGON = "zstock_factor_dragon"
COL_FACTOR_FORCE = "zstock_factor_force"

COL_FUNDAMENTAL_PERSHARE = "zstock_fundamental_pershare"
COL_FUNDAMENTAL_HOLDER = "zstock_fundamental_holder"

# 资金流默认周期：通达信 L2 历史；东财快照为 today/3day/5day/10day
PERIOD_L2_DAILY = "L2_daily"

# period 映射：公开 API → 落库 period
_PERIOD_MAP = {
    "daily": "D",
    "1d": "D",
    "D": "D",
    "weekly": "W",
    "W": "W",
    "monthly": "M",
    "M": "M",
    "5m": "5m",
    "5min": "5m",
    "1m": "1m",
    "1min": "1m",
}

# OHLCV 批量查询默认字段（减少网络传输）
_OHLCV_PROJECTION = {
    "_id": 0,
    "code": 1,
    "trade_date": 1,
    "open": 1,
    "high": 1,
    "low": 1,
    "close": 1,
    "volume": 1,
    "amount": 1,
    "pct_chg": 1,
    "pre_close": 1,
    "turnover_rate": 1,  # F3.8 / fcoop4 依赖，sync_ohlcv 已落库
}


class DataQueryService:
    """统一数据查询服务（只读 MongoDB，未命中抛异常或返回空容器）。"""

    @staticmethod
    def _iter_date_windows(
        start_date: str,
        end_date: str,
        chunk_days: int,
    ) -> List[Tuple[str, str]]:
        """将日期区间切分为若干窗口，避免单次 MongoDB 查询过大（内部静态辅助方法）。"""
        if chunk_days <= 0:
            return [(normalize_date(start_date), normalize_date(end_date))]

        start_str = normalize_date(start_date)
        end_str = normalize_date(end_date)
        if start_str > end_str:
            return []

        start_dt = datetime.strptime(start_str, "%Y-%m-%d")
        end_dt = datetime.strptime(end_str, "%Y-%m-%d")
        windows: List[Tuple[str, str]] = []
        cur = start_dt
        while cur <= end_dt:
            chunk_end = min(cur + timedelta(days=chunk_days - 1), end_dt)
            windows.append((cur.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
            cur = chunk_end + timedelta(days=1)
        return windows

    _instance: ClassVar[Optional["DataQueryService"]] = None

    @classmethod
    def get_data_query_service(cls) -> "DataQueryService":
        """进程内单例。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """清空单例，使下次 get_data_query_service 重新初始化。

        close_connections 后调用，避免单例持有已关闭的 db 引用。
        """
        cls._instance = None

    @staticmethod
    def _finite_float(val: Any) -> Optional[float]:
        try:
            num = float(val)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(num):
            return None
        return num

    @staticmethod
    def _fundamental_asof_map(
        pershare_docs: List[Dict],
        holder_docs: List[Dict],
    ) -> Dict[str, Dict[str, float]]:
        """把已按公告日截断的文档收成每股最新 BPS 与股东户数环比。"""
        out: Dict[str, Dict[str, float]] = {}
        bps_ann: Dict[str, str] = {}
        for doc in pershare_docs:
            code = doc.get("code")
            ann = doc.get("ann_date")
            bps = DataQueryService._finite_float(doc.get("bps"))
            if not code or not ann or bps is None or bps == 0:
                continue
            ann_s = str(ann)
            prev = bps_ann.get(code)
            if prev is not None and ann_s < prev:
                continue
            bps_ann[code] = ann_s
            out.setdefault(code, {})["bps"] = bps

        by_code: Dict[str, List[Tuple[str, str, float]]] = defaultdict(list)
        for doc in holder_docs:
            code = doc.get("code")
            ann = doc.get("ann_date")
            end = doc.get("end_date")
            sh = DataQueryService._finite_float(doc.get("shareholder"))
            if not code or not ann or not end or sh is None or sh <= 0:
                continue
            by_code[code].append((str(end), str(ann), sh))

        for code, records in by_code.items():
            records.sort(key=lambda item: item[0])
            chosen_ann: Optional[str] = None
            chosen_rate: Optional[float] = None
            for i in range(1, len(records)):
                prev_sh = records[i - 1][2]
                curr_sh = records[i][2]
                ann = records[i][1]
                rate = (curr_sh - prev_sh) / prev_sh
                if not math.isfinite(rate):
                    continue
                if chosen_ann is None or ann >= chosen_ann:
                    chosen_ann = ann
                    chosen_rate = rate
            if chosen_rate is not None:
                out.setdefault(code, {})["holder_change"] = chosen_rate
        return out

    def __init__(self):
        # 触发一次 get_database() 验证连接已初始化；
        # 不缓存返回值——close_connections 后旧引用会失效，
        # 每次通过 property 动态获取保证重连后自动生效。
        _ = self.db
        logger.info("查询服务初始化成功")

    @property
    def db(self):
        """动态获取 MongoDB 数据库，避免 close_connections 后持有过期引用。

        测试可通过 ``qs.db = fake_db`` 注入 mock，注入后优先使用注入值。
        """
        override = self.__dict__.get("_db_override")
        if override is not None:
            return override
        db = get_database()
        if db is None:
            raise RuntimeError("MongoDB 客户端未初始化，请先调用 db_manager.init_mongodb()")
        return db

    @db.setter
    def db(self, value):
        """允许测试注入 fake db。"""
        self.__dict__["_db_override"] = value

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
            if result is None:
                result = []
            logger.debug(f"查询 {collection}: 找到 {len(result)} 条记录")
            return result
        except Exception as e:
            logger.error(f"查询失败 {collection}: {e}")
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
            logger.debug(f"查询单条 {collection}: {'找到' if result else '未找到'}")
            return result
        except Exception as e:
            logger.error(f"查询单条失败 {collection}: {e}")
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
            logger.info(f"插入 {collection}: {result.inserted_id}")
            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"插入失败 {collection}: {e}")
            raise

    async def insert_many(
        self,
        collection: str,
        documents: List[Dict],
        ordered: bool = False,
    ) -> List[str]:
        """
        批量插入数据

        Args:
            collection: 集合名称
            documents: 文档列表
            ordered: 是否顺序写入。默认 False——遇到重复键等错误时继续处理剩余文档，
                     避免行情/因子回填因单条冲突静默丢失整批数据。

        Returns:
            插入的文档 ID 列表
        """
        if not documents:
            return []
        try:
            result = await self.db[collection].insert_many(documents, ordered=ordered)
            logger.debug(f"批量插入 {collection}: {len(result.inserted_ids)} 条")
            return [str(_id) for _id in result.inserted_ids]
        except Exception as e:
            # ordered=False 场景下 BulkWriteError 会带 details.nInserted；上层可据此判断是否需要报警。
            logger.error(f"批量插入失败 {collection}: {e}")
            raise

    async def bulk_upsert(
        self,
        collection: str,
        documents: List[Dict],
        key_fields: List[str],
        ordered: bool = False,
    ) -> Dict[str, int]:
        """
        批量 upsert：按 key_fields 组成的组合键 upsert 每条文档。

        用于行情/因子回填这类"允许重复调用、需要幂等"的场景，比逐条 replace_one
        吞吐至少高一个数量级。

        Args:
            collection: 集合名称
            documents: 文档列表
            key_fields: 用于匹配已有文档的字段名（例如 ["code", "trade_date"]）
            ordered: 是否顺序执行（默认 False，一条失败不影响其余）

        Returns:
            {"matched": ..., "modified": ..., "upserted": ...}
        """
        if not documents:
            return {"matched": 0, "modified": 0, "upserted": 0}
        if not key_fields:
            raise ValueError("bulk_upsert 必须指定 key_fields")

        from pymongo import UpdateOne  # 局部导入避免顶部对 pymongo 的硬依赖

        ops = []
        for doc in documents:
            missing = [k for k in key_fields if k not in doc]
            if missing:
                raise ValueError(f"bulk_upsert 文档缺少 key_fields: {missing}")
            flt = {k: doc[k] for k in key_fields}
            ops.append(UpdateOne(flt, {"$set": doc}, upsert=True))

        try:
            result = await self.db[collection].bulk_write(ops, ordered=ordered)
            summary = {
                "matched": int(result.matched_count or 0),
                "modified": int(result.modified_count or 0),
                "upserted": int(len(result.upserted_ids or {})),
            }
            logger.debug(f"bulk_upsert {collection}: {summary}")
            return summary
        except Exception as e:
            logger.error(f"bulk_upsert 失败 {collection}: {e}")
            raise

    # ==================== 数据更新方法 ====================

    def _prepare_update(self, update: Dict) -> Dict:
        """检查更新文档是否已经包涵 Mongo 操作符。"""
        if any(k.startswith('$') for k in update.keys()):
            return update
        return {"$set": update}

    async def update_one(
        self,
        collection: str,
        query: Dict,
        update: Dict,
        upsert: bool = False,
    ) -> int:
        """
        更新单条数据

        Args:
            collection: 集合名称
            query: 查询条件
            update: 更新内容（如果不用操作符会自动包裹 $set）
            upsert: 未匹配到文档时是否插入新文档（Mongo upsert）

        Returns:
            修改的文档数（upsert 插入时也算 1）
        """
        try:
            update_doc = self._prepare_update(update)
            result = await self.db[collection].update_one(query, update_doc, upsert=upsert)
            changed = int(result.modified_count or 0) + (1 if result.upserted_id is not None else 0)
            logger.debug(f"更新 {collection}: {changed} 条 (upsert={upsert})")
            return changed
        except Exception as e:
            logger.error(f"更新失败 {collection}: {e}")
            raise

    async def update_many(self, collection: str, query: Dict, update: Dict) -> int:
        """
        批量更新数据

        Args:
            collection: 集合名称
            query: 查询条件
            update: 更新内容（如果不用操作符会自动包裹 $set）

        Returns:
            修改的文档数
        """
        try:
            update_doc = self._prepare_update(update)
            result = await self.db[collection].update_many(query, update_doc)
            logger.info(f"批量更新 {collection}: {result.modified_count} 条")
            return result.modified_count
        except Exception as e:
            logger.error(f"批量更新失败 {collection}: {e}")
            raise

    async def replace_one(
        self,
        collection: str,
        query: Dict,
        document: Dict,
        upsert: bool = False,
    ) -> int:
        """替换单条数据（可选 upsert），返回修改/插入条数。"""
        try:
            result = await self.db[collection].replace_one(query, document, upsert=upsert)
            changed = int(result.modified_count or 0) + (1 if result.upserted_id is not None else 0)
            # 逐条 upsert 在批量预计算场景会非常嘈杂，默认降为 debug。
            logger.debug(f"替换 {collection}: {changed} 条")
            return changed
        except Exception as e:
            logger.error(f"替换失败 {collection}: {e}")
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
            logger.info(f"删除 {collection}: {result.deleted_count} 条")
            return result.deleted_count
        except Exception as e:
            logger.error(f"删除失败 {collection}: {e}")
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
            logger.info(f"批量删除 {collection}: {result.deleted_count} 条")
            return result.deleted_count
        except Exception as e:
            logger.error(f"批量删除失败 {collection}: {e}")
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
            logger.debug(f"统计 {collection}: {count} 条")
            return count
        except Exception as e:
            logger.error(f"统计失败 {collection}: {e}")
            raise

    # ==================== 集合操作方法 ====================

    async def drop_collection(self, collection: str) -> None:
        """删除集合"""
        try:
            await self.db[collection].drop()
            logger.info(f"删除集合: {collection}")
        except Exception as e:
            logger.error(f"删除集合失败 {collection}: {e}")
            raise

    async def list_collections(self) -> List[str]:
        """获取所有集合名称。查询失败时返回空列表（非关键路径，不抛异常）。"""
        try:
            collections = await self.db.list_collection_names()
            logger.debug(f"集合列表: {collections}")
            return collections
        except Exception as e:
            logger.error(f"获取集合列表失败: {e}")
            return []


    async def ensure_indexes(self) -> None:
        """在所有集合上建立必要索引（幂等，可重复调用）。

        索引按真实查询模式对齐：
        - OHLCV / 资金流：code+$in + period + trade_date 区间
        - 交易日筛选：period + trade_date（无 code）
        - 因子：按日 delete/load、按 code/sector 回查
        """
        from pymongo import ASCENDING, DESCENDING, IndexModel
        from pymongo.errors import OperationFailure

        db = self.db

        async def _safe_drop_index(collection_name: str, index_name: str) -> None:
            """删除旧索引，索引不存在时静默跳过，其他错误记录 warning。"""
            try:
                await db[collection_name].drop_index(index_name)
            except OperationFailure as e:
                # code 27 = IndexNotFound，属预期情况
                if e.code != 27:
                    logger.warning("drop_index %s.%s 失败: %s", collection_name, index_name, e)
            except Exception as e:
                logger.warning("drop_index %s.%s 异常: %s", collection_name, index_name, e)
        # zstock_ohlcv
        await db[COL_OHLCV].create_indexes(
            [
                IndexModel(
                    [
                        ("code", ASCENDING),
                        ("trade_date", ASCENDING),
                        ("period", ASCENDING),
                    ],
                    unique=True,
                    name="code_date_period",
                ),
                # get_ohlcv / get_ohlcv_batch: code(+in) + period + date range
                IndexModel(
                    [
                        ("code", ASCENDING),
                        ("period", ASCENDING),
                        ("trade_date", ASCENDING),
                    ],
                    name="code_period_date",
                ),
                # precompute _gen_trade_dates: period + date range（无 code）
                IndexModel(
                    [("period", ASCENDING), ("trade_date", ASCENDING)],
                    name="period_date",
                ),
            ]
        )
        # zstock_stock_info
        await db[COL_STOCK_INFO].create_indexes(
            [
                IndexModel([("code", ASCENDING)], unique=True, name="code_unique"),
                IndexModel(
                    [("is_mainboard", ASCENDING), ("is_st", ASCENDING)],
                    name="mainboard_st",
                ),
            ]
        )
        # zstock_capital_flow: (code, trade_date, period) 唯一
        await _safe_drop_index(COL_CAPITAL_FLOW, "code_date")
        await db[COL_CAPITAL_FLOW].create_indexes(
            [
                IndexModel(
                    [
                        ("code", ASCENDING),
                        ("trade_date", ASCENDING),
                        ("period", ASCENDING),
                    ],
                    unique=True,
                    name="code_date_period",
                ),
                # get_capital_flow_range / recent_days: code+$in + period + date
                IndexModel(
                    [
                        ("code", ASCENDING),
                        ("period", ASCENDING),
                        ("trade_date", ASCENDING),
                    ],
                    name="code_period_date",
                ),
                # distinct(trade_date) / 按日扫描
                IndexModel(
                    [("trade_date", ASCENDING), ("period", ASCENDING)],
                    name="trade_date_period",
                ),
            ]
        )
        # zstock_sector
        await db[COL_SECTOR].create_indexes(
            [
                IndexModel(
                    [("sector_code", ASCENDING), ("source", ASCENDING)],
                    unique=True,
                    name="sector_source",
                ),
                # get_sector_list: source 过滤 + sector_code 前缀
                IndexModel(
                    [("source", ASCENDING), ("sector_code", ASCENDING)],
                    name="source_sector",
                ),
            ]
        )
        # zstock_lhb（龙虎榜）
        # 兼容历史自动命名索引，避免 create_indexes 因同 key 不同 name 冲突（code 85）
        for legacy_idx in ("code_1_trade_date_-1", "trade_date_-1"):
            await _safe_drop_index(COL_LHB, legacy_idx)
        await db[COL_LHB].create_indexes(
            [
                IndexModel(
                    [("code", ASCENDING), ("trade_date", DESCENDING)],
                    name="code_date_desc",
                ),
                IndexModel(
                    [("trade_date", DESCENDING)],
                    name="date_desc",
                ),
            ]
        )
        # 因子预计算
        await db[COL_FACTOR_MARKET].create_indexes(
            [
                IndexModel(
                    [("trade_date", ASCENDING)],
                    unique=True,
                    name="trade_date_unique",
                ),
            ]
        )
        await db[COL_FACTOR_SECTOR].create_indexes(
            [
                # delete_many / 按日加载面板
                IndexModel(
                    [("trade_date", ASCENDING), ("sector_code", ASCENDING)],
                    unique=True,
                    name="date_sector_unique",
                ),
                # 单板块历史回查
                IndexModel(
                    [("sector_code", ASCENDING), ("trade_date", ASCENDING)],
                    name="sector_date",
                ),
            ]
        )
        await db[COL_FACTOR_DRAGON].create_indexes(
            [
                # delete_many(trade_date) / 按日面板（前缀）；同股可属多板块故不 unique
                IndexModel(
                    [
                        ("trade_date", ASCENDING),
                        ("sector_code", ASCENDING),
                        ("code", ASCENDING),
                    ],
                    name="date_sector_code",
                ),
                # 个股跨板块 / 时序回查
                IndexModel(
                    [("code", ASCENDING), ("trade_date", ASCENDING)],
                    name="code_date",
                ),
            ]
        )
        await db[COL_FACTOR_FORCE].create_indexes(
            [
                IndexModel(
                    [("trade_date", ASCENDING), ("code", ASCENDING)],
                    unique=True,
                    name="date_code_unique",
                ),
                IndexModel(
                    [("code", ASCENDING), ("trade_date", ASCENDING)],
                    name="code_date",
                ),
            ]
        )
        logger.info("MongoDB 索引已就绪（含因子预计算集合）")

    # =========================== OHLCV 行情 ===========================

    async def get_ohlcv(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        period: str = "daily",
    ) -> Tuple[pd.DataFrame, str]:
        """获取单只股票行情（日/周/月线）。无数据抛 ValueError。"""
        code = normalize_code(symbol)
        p = _PERIOD_MAP.get(period, "D")
        norm_start = normalize_date(start_date)
        norm_end = normalize_date(end_date)

        docs = await self.query(
            COL_OHLCV,
            {
                "code": code,
                "period": p,
                "trade_date": {"$gte": norm_start, "$lte": norm_end},
            },
            sort=[("trade_date", 1)],
        )
        if not docs:
            raise ValueError(f"无数据: {symbol} ({start_date} - {end_date})")

        df = pd.DataFrame(docs)
        df.drop(columns=[c for c in ("_id",) if c in df.columns], inplace=True)
        return df, "mongodb"

    async def get_ohlcv_batch(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
        period: str = "daily",
        batch_size: int = 50,
        date_chunk_days: int = 90,
        query_concurrency: Optional[int] = None,
    ) -> Dict[str, pd.DataFrame]:
        """一次性查多只股票的 OHLCV，返回 {code: DataFrame}。无数据返回 {}。

        内部按「股票批次 × 日期窗口」分批查询，子查询 asyncio 并发执行。
        """
        if not codes:
            return {}
        pure_codes = [normalize_code(c) for c in codes]
        p = _PERIOD_MAP.get(period, "D")
        norm_start = normalize_date(start_date)
        norm_end = normalize_date(end_date)
        date_windows = self._iter_date_windows(norm_start, norm_end, date_chunk_days)

        parts_by_code: Dict[str, List[pd.DataFrame]] = defaultdict(list)
        chunks: List[Tuple[List[str], str, str]] = []
        for win_start, win_end in date_windows:
            for i in range(0, len(pure_codes), batch_size):
                chunks.append((pure_codes[i : i + batch_size], win_start, win_end))

        total_queries = len(chunks)
        if total_queries == 0:
            return {}

        if query_concurrency is None:
            query_concurrency = _default_query_concurrency()
        sem = asyncio.Semaphore(max(1, query_concurrency))
        done_queries = 0
        progress_lock = asyncio.Lock()

        async def _fetch_chunk(
            chunk: List[str], win_start: str, win_end: str
        ) -> List[Tuple[str, pd.DataFrame]]:
            nonlocal done_queries
            async with sem:
                try:
                    docs = await self.query(
                        COL_OHLCV,
                        {
                            "code": {"$in": chunk},
                            "period": p,
                            "trade_date": {"$gte": win_start, "$lte": win_end},
                        },
                        projection=_OHLCV_PROJECTION,
                        sort=[("code", 1), ("trade_date", 1)],
                    )
                except Exception as e:
                    logger.error(
                        "OHLCV batch 查询失败 (codes=%d, %s~%s): %s",
                        len(chunk),
                        win_start,
                        win_end,
                        e,
                    )
                    raise ValueError(
                        f"get_ohlcv_batch无法获取数据: codes={len(chunk)} "
                        f"({win_start} - {win_end})"
                    ) from e

                parts: List[Tuple[str, pd.DataFrame]] = []
                if docs:
                    df = pd.DataFrame(docs)
                    for code, g in df.groupby("code"):
                        parts.append((str(code), g))
                async with progress_lock:
                    done_queries += 1
                    if (
                        done_queries % 20 == 0
                        or done_queries == total_queries
                    ):
                        logger.info(
                            "  OHLCV 子查询进度 %d/%d (并发=%d, 窗口 %s~%s, 本批 %d 只)",
                            done_queries,
                            total_queries,
                            query_concurrency,
                            win_start,
                            win_end,
                            len(chunk),
                        )
                return parts

        for parts in await asyncio.gather(
            *[_fetch_chunk(chunk, ws, we) for chunk, ws, we in chunks]
        ):
            for code, g in parts:
                parts_by_code[code].append(g)

        if not parts_by_code:
            return {}

        out: Dict[str, pd.DataFrame] = {}
        for code, parts in parts_by_code.items():
            df = pd.concat(parts, ignore_index=True)
            if "trade_date" in df.columns:
                df = df.sort_values("trade_date").drop_duplicates(
                    subset=["trade_date"], keep="last"
                )
            out[code] = df.reset_index(drop=True)
        return out

    # =========================== 股票元数据 ===========================

    async def get_stock_info(self, symbol: str) -> Tuple[Dict, str]:
        """获取股票基本信息。无数据抛 ValueError。"""
        doc = await self.query_one(
            COL_STOCK_INFO, {"code": normalize_code(symbol)}
        )
        if doc is not None:
            doc.pop("_id", None)
            return doc, "mongodb"

        raise ValueError(f"get_stock_info无法获取数据: {symbol}")

    async def get_all_stocks(self) -> Tuple[List[Dict], str]:
        """获取全 A 股完整信息列表（含创业板/科创板/北交所，含 ST）。"""
        try:
            docs = await self.query(
                COL_STOCK_INFO,
                {},
                projection={"_id": 0},
            )
            if docs:
                return [d for d in docs if d.get("code")], "mongodb"
        except Exception as e:
            logger.error(f"从 MongoDB 获取全市场股票列表失败: {e}")
            raise ValueError("get_all_stocks无法获取数据") from e

        raise ValueError("get_all_stocks无法获取数据")

    # =========================== 资金流 ===========================

    async def get_capital_flow(
        self,
        symbol: str,
        trade_date: str,
        period: str = PERIOD_L2_DAILY,
    ) -> Tuple[Dict, str]:
        """获取股票资金流向。trade_date 接受 YYYY-MM-DD / YYYYMMDD。"""
        code = normalize_code(symbol)
        td_norm = normalize_date(trade_date)

        try:
            doc = await self.query_one(
                COL_CAPITAL_FLOW,
                {"code": code, "trade_date": td_norm, "period": period},
            )
            if doc:
                doc.pop("_id", None)
                return doc, "mongodb"
        except Exception as e:
            logger.error(f"从 MongoDB 获取资金流数据失败: {e}")
            raise ValueError(
                f"get_capital_flow无数据: {symbol} ({trade_date})"
            ) from e

        raise ValueError(f"get_capital_flow无数据: {symbol} ({trade_date})")

    async def get_capital_flow_recent_days(
        self,
        codes: List[str],
        end_date: str,
        days: int = 5,
        period: str = PERIOD_L2_DAILY,
    ) -> Dict[str, List[Dict]]:
        """
        查询多只股票近 N 个交易日的资金流（每只各取最近 days 条）。

        Args:
            codes: 股票代码列表
            end_date: 结束日期（含）
            days: 每只股票回看条数
            period: 资金流周期，默认 L2_daily
        """
        if not codes or days <= 0:
            return {}

        pure_codes = [normalize_code(c) for c in codes]
        td_norm = normalize_date(end_date)
        # 用日历窗口收窄扫描范围（交易日 ≈ 日历日 * 2/3，留足节假日缓冲）
        try:
            end_dt = datetime.strptime(td_norm, "%Y-%m-%d")
        except ValueError as e:
            raise ValueError(f"非法 end_date: {end_date}") from e
        lookback_calendar = max(days * 4, 21)
        start_bound = (end_dt - timedelta(days=lookback_calendar)).strftime("%Y-%m-%d")

        try:
            docs = await self.query(
                COL_CAPITAL_FLOW,
                {
                    "code": {"$in": pure_codes},
                    "period": period,
                    "trade_date": {"$gte": start_bound, "$lte": td_norm},
                },
                sort=[("code", 1), ("trade_date", -1)],
            )
        except Exception as e:
            logger.error(f"capital_flow recent_days 查询失败: {e}")
            raise

        grouped: Dict[str, List[Dict]] = defaultdict(list)
        seen_dates: Dict[str, set] = defaultdict(set)
        for doc in docs or []:
            code = doc.get("code")
            if not code or len(grouped[code]) >= days:
                continue
            td = doc.get("trade_date")
            if td in seen_dates[code]:
                continue
            seen_dates[code].add(td)
            doc.pop("_id", None)
            grouped[code].append(doc)

        # 反转为升序
        return {code: list(reversed(rows)) for code, rows in grouped.items()}

    async def get_capital_flow_range(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
        period: str = PERIOD_L2_DAILY,
        projection: Optional[Dict[str, int]] = None,
        batch_size: int = 50,
        date_chunk_days: int = 90,
        query_concurrency: Optional[int] = None,
    ) -> Dict[str, List[Dict]]:
        """查询多只股票在日期区间内的资金流，按 code 分组并按日期升序返回。

        内部按股票批次 + 日期窗口分批并发查询。
        """
        if not codes:
            return {}

        pure_codes = [normalize_code(c) for c in codes]
        start_norm = normalize_date(start_date)
        end_norm = normalize_date(end_date)
        date_windows = self._iter_date_windows(start_norm, end_norm, date_chunk_days)
        grouped: Dict[str, List[Dict]] = defaultdict(list)

        proj = projection or {
            "_id": 0,
            "code": 1,
            "trade_date": 1,
            "period": 1,
            "main_net": 1,
            "m_net": 1,
            "s_net": 1,
            "xl_net": 1,
            "turnover": 1,
        }

        chunks: List[Tuple[List[str], str, str]] = []
        for win_start, win_end in date_windows:
            for i in range(0, len(pure_codes), batch_size):
                chunks.append((pure_codes[i : i + batch_size], win_start, win_end))

        if not chunks:
            return {}

        if query_concurrency is None:
            query_concurrency = _default_query_concurrency()
        sem = asyncio.Semaphore(max(1, query_concurrency))

        async def _fetch_chunk(
            chunk: List[str], win_start: str, win_end: str
        ) -> List[Dict]:
            async with sem:
                try:
                    return await self.query(
                        COL_CAPITAL_FLOW,
                        {
                            "code": {"$in": chunk},
                            "period": period,
                            "trade_date": {"$gte": win_start, "$lte": win_end},
                        },
                        projection=proj,
                        sort=[("code", 1), ("trade_date", 1)],
                    )
                except Exception as e:
                    logger.error(
                        "capital_flow range 查询失败 (codes=%d, %s~%s): %s",
                        len(chunk),
                        win_start,
                        win_end,
                        e,
                    )
                    raise

        for docs in await asyncio.gather(
            *[_fetch_chunk(chunk, ws, we) for chunk, ws, we in chunks]
        ):
            for doc in docs or []:
                code = doc.get("code")
                if code:
                    grouped[code].append(doc)

        # 去重 + 升序
        out: Dict[str, List[Dict]] = {}
        for code, rows in grouped.items():
            seen = set()
            deduped = []
            for row in sorted(rows, key=lambda x: x.get("trade_date", "")):
                td = row.get("trade_date")
                if td in seen:
                    continue
                seen.add(td)
                deduped.append(row)
            out[code] = deduped
        return out

    # =========================== 龙虎榜 ===========================

    async def get_lhb(self, code: str, trade_date: str) -> Optional[Dict]:
        """获取单只股票单日龙虎榜数据。无数据返回 None。"""
        code_norm = normalize_code(code)
        td_norm = normalize_date(trade_date)
        try:
            doc = await self.query_one(
                COL_LHB,
                {"code": code_norm, "trade_date": td_norm},
                projection={"_id": 0},
            )
            return doc
        except Exception as e:
            logger.debug(f"get_lhb 查询失败: {code} ({trade_date}): {e}")
            return None

    async def get_lhb_recent(self, code: str, end_date: str, days: int = 10) -> List[Dict]:
        """获取近 N 日龙虎榜数据（升序）。无数据返回 []。"""
        code_norm = normalize_code(code)
        td_norm = normalize_date(end_date)
        if days <= 0:
            return []

        try:
            end_dt = datetime.strptime(td_norm, "%Y-%m-%d")
        except ValueError:
            return []

        # 日历窗口回看
        lookback_calendar = max(days * 4, 21)
        start_bound = (end_dt - timedelta(days=lookback_calendar)).strftime("%Y-%m-%d")

        try:
            docs = await self.query(
                COL_LHB,
                {
                    "code": code_norm,
                    "trade_date": {"$gte": start_bound, "$lte": td_norm},
                },
                sort=[("trade_date", -1)],
            )
        except Exception as e:
            logger.debug(f"get_lhb_recent 查询失败: {code}: {e}")
            return []

        result = []
        for doc in (docs or [])[:days]:
            doc.pop("_id", None)
            result.append(doc)
        result.reverse()
        return result

    async def batch_get_lhb(self, codes: List[str], trade_date: str) -> Dict[str, Optional[Dict]]:
        """批量查询多只股票单日龙虎榜。返回 {code: lhb_doc or None}。"""
        if not codes:
            return {}

        codes_norm = [normalize_code(c) for c in codes]
        td_norm = normalize_date(trade_date)

        try:
            docs = await self.query(
                COL_LHB,
                {
                    "code": {"$in": codes_norm},
                    "trade_date": td_norm,
                },
                projection={"_id": 0},
            )
        except Exception as e:
            logger.debug(f"batch_get_lhb 查询失败: {e}")
            return {c: None for c in codes_norm}

        result: Dict[str, Optional[Dict]] = {c: None for c in codes_norm}
        for doc in docs or []:
            code = doc.get("code")
            if code:
                result[code] = doc
        return result

    async def batch_get_lhb_recent(
        self,
        codes: List[str],
        end_date: str,
        days: int = 10,
    ) -> Dict[str, List[Dict]]:
        """批量查询多只股票近 N 日龙虎榜。返回 {code: [docs 升序]}。"""
        if not codes or days <= 0:
            return {}

        codes_norm = [normalize_code(c) for c in codes]
        td_norm = normalize_date(end_date)
        try:
            end_dt = datetime.strptime(td_norm, "%Y-%m-%d")
        except ValueError:
            return {c: [] for c in codes_norm}

        lookback_calendar = max(days * 4, 21)
        start_bound = (end_dt - timedelta(days=lookback_calendar)).strftime("%Y-%m-%d")
        empty = {c: [] for c in codes_norm}
        try:
            docs = await self.query(
                COL_LHB,
                {
                    "code": {"$in": codes_norm},
                    "trade_date": {"$gte": start_bound, "$lte": td_norm},
                },
                projection={"_id": 0},
            )
        except Exception as e:
            logger.debug(f"batch_get_lhb_recent 查询失败: {e}")
            return empty

        by_code: Dict[str, List[Dict]] = {c: [] for c in codes_norm}
        for doc in docs or []:
            code = doc.get("code")
            if code in by_code:
                by_code[code].append(doc)
        for code, rows in by_code.items():
            rows.sort(key=lambda d: str(d.get("trade_date", "")), reverse=True)
            by_code[code] = list(reversed(rows[:days]))
        return by_code

    # =========================== 板块 ===========================

    async def get_sector_list(
        self, prefix: Optional[str] = "SW2"
    ) -> Tuple[List[Dict], str]:
        """获取板块列表（元数据：sector_code / sector_name / sector_type / stocks）。

        Args:
            prefix: 板块前缀过滤，默认 'SW2'。传 None 或 '' 返回全部。
        """
        try:
            query: Dict[str, Any] = {"source": "xtquant"}
            if prefix:
                query["sector_code"] = {"$regex": f"^{re.escape(prefix)}"}
            docs = await self.query(
                COL_SECTOR,
                query,
                projection={
                    "sector_code": 1,
                    "sector_name": 1,
                    "sector_type": 1,
                    "stocks": 1,
                    "_id": 0,
                },
                limit=10000,
            )
            return docs, "mongodb"
        except Exception as e:
            logger.error(f"从 MongoDB 获取板块列表失败: {e}")
            raise ValueError("get_sector_list无法获取数据") from e

    async def get_sector_stocks(self, sector_code: str) -> Tuple[List[str], str]:
        """获取板块成分股代码列表（纯 6 位代码字符串）。"""
        try:
            doc = await self.query_one(
                COL_SECTOR,
                {"sector_code": sector_code, "source": "xtquant"},
                projection={"stocks": 1, "_id": 0},
            )
            if doc and doc.get("stocks"):
                return doc["stocks"], "mongodb"
        except Exception as e:
            logger.error(f"从 MongoDB 获取板块成分股失败: {e}")
            raise ValueError("get_sector_stocks无法获取数据") from e

        raise ValueError("get_sector_stocks无法获取数据")

    async def get_sector_stocks_batch(
        self, sector_codes: List[str]
    ) -> Dict[str, List[str]]:
        """一次性查多个板块的成分股，返回 {sector_code: [codes]}。"""
        if not sector_codes:
            return {}

        try:
            docs = await self.query(
                COL_SECTOR,
                {"sector_code": {"$in": sector_codes}, "source": "xtquant"},
                projection={"sector_code": 1, "stocks": 1, "_id": 0},
            )
            result: Dict[str, List[str]] = {}
            for doc in docs or []:
                if doc.get("stocks"):
                    result[doc["sector_code"]] = doc["stocks"]
            return result
        except Exception as e:
            logger.error(f"sector_stocks batch MongoDB 查询失败: {e}")
            raise ValueError("get_sector_stocks_batch无法获取数据") from e

    # =========================== 因子预计算查询 ===========================

    async def get_factor_market(self, trade_date: str) -> Optional[Dict]:
        """获取 M1 市场因子原始值。无数据返回 None。"""
        return await self.query_one(
            COL_FACTOR_MARKET,
            {"trade_date": normalize_date(trade_date)},
            projection={"_id": 0},
        )

    async def get_factor_sectors(self, trade_date: str) -> List[Dict]:
        """获取 M2 板块因子原始值（全板块）。"""
        docs = await self.query(
            COL_FACTOR_SECTOR,
            {"trade_date": normalize_date(trade_date)},
            projection={"_id": 0},
            sort=[("sector_code", 1)],
        )
        return docs or []

    async def get_factor_dragons(
        self,
        trade_date: str,
        sector_codes: Optional[List[str]] = None,
    ) -> List[Dict]:
        """获取 M3 龙头因子原始值。sector_codes=None → 返回全部。"""
        query: Dict[str, Any] = {"trade_date": normalize_date(trade_date)}
        if sector_codes:
            query["sector_code"] = {"$in": sector_codes}
        docs = await self.query(
            COL_FACTOR_DRAGON,
            query,
            projection={"_id": 0},
            sort=[("sector_code", 1), ("code", 1)],
        )
        return docs or []

    async def get_factor_forces(self, trade_date: str) -> List[Dict]:
        """获取 M4 合力因子原始值（全部候选）。"""
        docs = await self.query(
            COL_FACTOR_FORCE,
            {"trade_date": normalize_date(trade_date)},
            projection={"_id": 0},
            sort=[("code", 1)],
        )
        return docs or []

    # =========================== 因子预计算区间查询 ===========================

    @staticmethod
    def _factor_range_concurrency(cap: int = 8) -> int:
        """因子窗口并发。大集合必须低并发，避免 Mongo 超时 / 连接池打满。

        优先读 ZSTOCK_FACTOR_CONCURRENCY，回退到 ZSTOCK_OHLCV_CONCURRENCY
        （与 backtester / overnight_risk_validate 共用的并发配置）。
        """
        raw = os.environ.get("ZSTOCK_FACTOR_CONCURRENCY") or os.environ.get("ZSTOCK_OHLCV_CONCURRENCY")
        if raw:
            try:
                n = int(raw)
            except ValueError:
                n = _default_query_concurrency()
        else:
            n = _default_query_concurrency()
        return max(2, min(cap, n))

    async def _query_window_with_retry(
        self,
        collection: str,
        win_start: str,
        win_end: str,
        *,
        attempts: int = 5,
    ) -> List[Dict]:
        last_err: Optional[BaseException] = None
        for attempt in range(1, attempts + 1):
            try:
                docs = await self.query(
                    collection,
                    {"trade_date": {"$gte": win_start, "$lte": win_end}},
                    projection={"_id": 0},
                    batch_size=4000,
                )
                return docs or []
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                retryable = "timed out" in msg or "timeout" in msg or "connection" in msg
                if not retryable or attempt >= attempts:
                    raise
                wait = min(30, 2 ** (attempt - 1))
                logger.warning(
                    "  %s 窗口 %s~%s 失败，%ds 后重试 (%d/%d): %s",
                    collection,
                    win_start,
                    win_end,
                    wait,
                    attempt,
                    attempts,
                    e,
                )
                await asyncio.sleep(wait)
        raise last_err or RuntimeError(f"{collection} 窗口查询失败")

    async def _query_docs_by_date_windows(
        self,
        collection: str,
        start_date: str,
        end_date: str,
        *,
        date_chunk_days: int = 60,
        query_concurrency: Optional[int] = None,
        sink: Optional[Callable[[List[Dict]], None]] = None,
    ) -> List[Dict]:
        """按日期窗口并发拉取；sink 用于流式消费，避免 6 年结果在 list 里翻倍占内存。

        不在 Mongo 端 sort：区间扫描走 trade_date 索引，排序改由调用方按日分组。
        """
        start = normalize_date(start_date)
        end = normalize_date(end_date)
        windows = self._iter_date_windows(start, end, date_chunk_days)
        if not windows:
            return []
        if query_concurrency is None:
            query_concurrency = self._factor_range_concurrency()
        sem = asyncio.Semaphore(max(1, query_concurrency))
        total = len(windows)
        done = 0
        n_docs = 0
        progress_lock = asyncio.Lock()

        async def _fetch(win_start: str, win_end: str) -> List[Dict]:
            nonlocal done, n_docs
            async with sem:
                docs = await self._query_window_with_retry(
                    collection, win_start, win_end
                )
                async with progress_lock:
                    done += 1
                    n_docs += len(docs)
                    if sink is not None and docs:
                        sink(docs)
                    if done == 1 or done == total or done % 10 == 0:
                        logger.info(
                            "  %s 窗口进度 %d/%d (并发=%d, %s~%s, 本窗 %d 条)",
                            collection,
                            done,
                            total,
                            query_concurrency,
                            win_start,
                            win_end,
                            len(docs),
                        )
                return [] if sink is not None else docs

        parts = await asyncio.gather(*[_fetch(ws, we) for ws, we in windows])
        out: List[Dict] = []
        if sink is None:
            for part in parts:
                out.extend(part)
            n_docs = len(out)
        logger.info(
            "%s 区间 %s~%s: %d 条 (%d 窗口, 并发=%d)",
            collection,
            start,
            end,
            n_docs,
            total,
            query_concurrency,
        )
        return out

    async def get_factor_market_range(
        self,
        start_date: str,
        end_date: str,
        *,
        sink: Optional[Callable[[List[Dict]], None]] = None,
    ) -> List[Dict]:
        """获取 M1 市场因子预计算文档（区间）。"""
        return await self._query_docs_by_date_windows(
            COL_FACTOR_MARKET,
            start_date,
            end_date,
            date_chunk_days=180,
            query_concurrency=self._factor_range_concurrency(8),
            sink=sink,
        )

    async def get_factor_sectors_range(
        self,
        start_date: str,
        end_date: str,
        *,
        sink: Optional[Callable[[List[Dict]], None]] = None,
    ) -> List[Dict]:
        """获取 M2 板块因子预计算文档（区间）。"""
        return await self._query_docs_by_date_windows(
            COL_FACTOR_SECTOR,
            start_date,
            end_date,
            date_chunk_days=90,
            query_concurrency=self._factor_range_concurrency(8),
            sink=sink,
        )

    async def get_factor_dragons_range(
        self,
        start_date: str,
        end_date: str,
        *,
        sink: Optional[Callable[[List[Dict]], None]] = None,
    ) -> List[Dict]:
        """获取 M3 龙头因子预计算文档（区间）。"""
        return await self._query_docs_by_date_windows(
            COL_FACTOR_DRAGON,
            start_date,
            end_date,
            date_chunk_days=14,
            query_concurrency=self._factor_range_concurrency(4),
            sink=sink,
        )

    async def get_factor_forces_range(
        self,
        start_date: str,
        end_date: str,
        *,
        sink: Optional[Callable[[List[Dict]], None]] = None,
    ) -> List[Dict]:
        """获取 M4 合力因子预计算文档（区间）。"""
        return await self._query_docs_by_date_windows(
            COL_FACTOR_FORCE,
            start_date,
            end_date,
            date_chunk_days=14,
            query_concurrency=self._factor_range_concurrency(4),
            sink=sink,
        )

    # =========================== 基本面财务数据查询 ===========================

    async def get_all_fundamental_pershare(self) -> List[Dict]:
        """获取全量每股指标文档（包含 bps 等字段）。"""
        docs = await self.query(
            COL_FUNDAMENTAL_PERSHARE,
            {"bps": {"$exists": True, "$ne": None}},
            projection={"_id": 0, "code": 1, "ann_date": 1, "bps": 1},
            sort=[("code", 1), ("ann_date", 1)],
        )
        return docs or []

    async def get_all_fundamental_holder(self) -> List[Dict]:
        """获取全量股东数历史文档。"""
        docs = await self.query(
            COL_FUNDAMENTAL_HOLDER,
            {},
            projection={"_id": 0, "code": 1, "ann_date": 1, "end_date": 1, "shareholder": 1},
            sort=[("code", 1), ("end_date", 1)],
        )
        return docs or []

    async def get_fundamental_asof(self, asof_date: str) -> Dict[str, Dict[str, float]]:
        """截至公告日 asof_date（含）的最新 BPS 与股东户数环比。

        返回 ``{code: {"bps": float, "holder_change": float}}``，缺哪项就不写哪项。
        股东户数环比按报告期相邻两期计算，取公告日不晚于 asof 的最近一期。
        """
        asof = normalize_date(asof_date)
        pershare = await self.query(
            COL_FUNDAMENTAL_PERSHARE,
            {"ann_date": {"$lte": asof}, "bps": {"$ne": None}},
            projection={"_id": 0, "code": 1, "ann_date": 1, "bps": 1},
            sort=[("ann_date", 1)],
        )
        holder = await self.query(
            COL_FUNDAMENTAL_HOLDER,
            {"ann_date": {"$lte": asof}},
            projection={"_id": 0, "code": 1, "ann_date": 1, "end_date": 1, "shareholder": 1},
            sort=[("end_date", 1)],
        )
        return self._fundamental_asof_map(pershare or [], holder or [])


# 兼容旧 import：from ...query_service import get_data_query_service
get_data_query_service = DataQueryService.get_data_query_service

