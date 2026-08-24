"""
数据查询服务（MongoDB）。

集合：
    zstock_stock_info       — 股票元数据（is_mainboard / is_st）
    zstock_ohlcv            — 行情，period: 'D' / 'W' / 'M'
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
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from zstock.common.utils.common_utils import normalize_code, normalize_date
from zstock.common.utils.resource_budget import default_query_concurrency

from .database_service import get_database_service

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

# 资金流默认周期：通达信 L2 历史；东财快照为 today/3day/5day/10day
PERIOD_L2_DAILY = "L2_daily"

# period 映射：公开 API 用 daily/weekly/monthly，落库用 D/W/M
_PERIOD_MAP = {"daily": "D", "weekly": "W", "monthly": "M"}

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


def _iter_date_windows(
    start_date: str,
    end_date: str,
    chunk_days: int,
) -> List[Tuple[str, str]]:
    """将日期区间切分为若干窗口，避免单次 MongoDB 查询过大。"""
    if chunk_days <= 0:
        return [(normalize_date(start_date), normalize_date(end_date))]
    start_dt = datetime.strptime(normalize_date(start_date), "%Y-%m-%d")
    end_dt = datetime.strptime(normalize_date(end_date), "%Y-%m-%d")
    windows: List[Tuple[str, str]] = []
    cur = start_dt
    while cur <= end_dt:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), end_dt)
        windows.append((cur.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
        cur = chunk_end + timedelta(days=1)
    return windows


class DataQueryService:
    """统一数据查询服务（只读 MongoDB，未命中抛异常或返回空容器）。"""

    def __init__(self):
        self.database_service = get_database_service()
        logger.info("✅ 查询服务初始化成功")

    async def ensure_indexes(self) -> None:
        """在所有集合上建立必要索引（幂等，可重复调用）。

        索引按真实查询模式对齐：
        - OHLCV / 资金流：code+$in + period + trade_date 区间
        - 交易日筛选：period + trade_date（无 code）
        - 因子：按日 delete/load、按 code/sector 回查
        """
        from pymongo import ASCENDING,DESCENDING, IndexModel

        db = self.database_service.db
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
        try:
            await db[COL_CAPITAL_FLOW].drop_index("code_date")
        except Exception:
            pass
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
            try:
                await db[COL_LHB].drop_index(legacy_idx)
            except Exception:
                pass
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
        logger.info("✅ MongoDB 索引已就绪（含因子预计算集合）")

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

        docs = await self.database_service.query(
            COL_OHLCV,
            {
                "code": code,
                "period": p,
                "trade_date": {"$gte": norm_start, "$lte": norm_end},
            },
            sort=[("trade_date", 1)],
        )
        if not docs:
            raise ValueError(f"❌ 无数据: {symbol} ({start_date} - {end_date})")

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
        date_windows = _iter_date_windows(norm_start, norm_end, date_chunk_days)

        parts_by_code: Dict[str, List[pd.DataFrame]] = defaultdict(list)
        chunks: List[Tuple[List[str], str, str]] = []
        for win_start, win_end in date_windows:
            for i in range(0, len(pure_codes), batch_size):
                chunks.append((pure_codes[i : i + batch_size], win_start, win_end))

        total_queries = len(chunks)
        if total_queries == 0:
            return {}

        if query_concurrency is None:
            query_concurrency = default_query_concurrency()
        sem = asyncio.Semaphore(max(1, query_concurrency))
        done_queries = 0
        progress_lock = asyncio.Lock()

        async def _fetch_chunk(
            chunk: List[str], win_start: str, win_end: str
        ) -> List[Tuple[str, pd.DataFrame]]:
            nonlocal done_queries
            async with sem:
                try:
                    docs = await self.database_service.query(
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
                        f"❌ get_ohlcv_batch无法获取数据: codes={len(chunk)} "
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
        doc = await self.database_service.query_one(
            COL_STOCK_INFO, {"code": normalize_code(symbol)}
        )
        if doc:
            doc.pop("_id", None)
            return doc, "mongodb"

        raise ValueError(f"❌ get_stock_info无法获取数据: {symbol}")

    async def get_all_stocks(self) -> Tuple[List[Dict], str]:
        """获取全 A 股完整信息列表（含创业板/科创板/北交所，含 ST）。"""
        try:
            docs = await self.database_service.query(
                COL_STOCK_INFO,
                {},
                projection={"_id": 0},
            )
            if docs:
                return [d for d in docs if d.get("code")], "mongodb"
        except Exception as e:
            logger.error(f"从 MongoDB 获取全市场股票列表失败: {e}")
            raise ValueError("❌ get_all_stocks无法获取数据") from e

        raise ValueError("❌ get_all_stocks无法获取数据")

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
            doc = await self.database_service.query_one(
                COL_CAPITAL_FLOW,
                {"code": code, "trade_date": td_norm, "period": period},
            )
            if doc:
                doc.pop("_id", None)
                return doc, "mongodb"
        except Exception as e:
            logger.error(f"从 MongoDB 获取资金流数据失败: {e}")
            raise ValueError(
                f"❌ get_capital_flow无数据: {symbol} ({trade_date})"
            ) from e

        raise ValueError(f"❌ get_capital_flow无数据: {symbol} ({trade_date})")

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
            docs = await self.database_service.query(
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
        for doc in docs or []:
            code = doc.get("code")
            if not code or len(grouped[code]) >= days:
                continue
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
        date_windows = _iter_date_windows(start_norm, end_norm, date_chunk_days)
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
            query_concurrency = default_query_concurrency()
        sem = asyncio.Semaphore(max(1, query_concurrency))

        async def _fetch_chunk(
            chunk: List[str], win_start: str, win_end: str
        ) -> List[Dict]:
            async with sem:
                try:
                    return await self.database_service.query(
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
            doc = await self.database_service.query_one(
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
            docs = await self.database_service.query(
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
            docs = await self.database_service.query(
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
            docs = await self.database_service.query(
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
            if docs:
                return docs, "mongodb"
        except Exception as e:
            logger.error(f"从 MongoDB 获取板块列表失败: {e}")
            raise ValueError("❌ get_sector_list无法获取数据") from e

        raise ValueError("❌ get_sector_list无法获取数据")

    async def get_sector_stocks(self, sector_code: str) -> Tuple[List[str], str]:
        """获取板块成分股代码列表（纯 6 位代码字符串）。"""
        try:
            doc = await self.database_service.query_one(
                COL_SECTOR,
                {"sector_code": sector_code, "source": "xtquant"},
                projection={"stocks": 1, "_id": 0},
            )
            if doc and doc.get("stocks"):
                return doc["stocks"], "mongodb"
        except Exception as e:
            logger.error(f"从 MongoDB 获取板块成分股失败: {e}")
            raise ValueError("❌ get_sector_stocks无法获取数据") from e

        raise ValueError("❌ get_sector_stocks无法获取数据")

    async def get_sector_stocks_batch(
        self, sector_codes: List[str]
    ) -> Dict[str, List[str]]:
        """一次性查多个板块的成分股，返回 {sector_code: [codes]}。"""
        if not sector_codes:
            return {}

        try:
            docs = await self.database_service.query(
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
            raise ValueError("❌ get_sector_stocks_batch无法获取数据") from e

    # =========================== 因子预计算查询 ===========================

    async def get_factor_market(self, trade_date: str) -> Optional[Dict]:
        """获取 M1 市场因子原始值。无数据返回 None。"""
        return await self.database_service.query_one(
            COL_FACTOR_MARKET,
            {"trade_date": normalize_date(trade_date)},
            projection={"_id": 0},
        )

    async def get_factor_sectors(self, trade_date: str) -> List[Dict]:
        """获取 M2 板块因子原始值（全板块）。"""
        docs = await self.database_service.query(
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
        docs = await self.database_service.query(
            COL_FACTOR_DRAGON,
            query,
            projection={"_id": 0},
            sort=[("sector_code", 1), ("code", 1)],
        )
        return docs or []

    async def get_factor_forces(self, trade_date: str) -> List[Dict]:
        """获取 M4 合力因子原始值（全部候选）。"""
        docs = await self.database_service.query(
            COL_FACTOR_FORCE,
            {"trade_date": normalize_date(trade_date)},
            projection={"_id": 0},
            sort=[("code", 1)],
        )
        return docs or []


# ==========================================================================

_data_query_service: Optional[DataQueryService] = None


def get_data_query_service() -> DataQueryService:
    global _data_query_service
    if _data_query_service is None:
        _data_query_service = DataQueryService()
    return _data_query_service
