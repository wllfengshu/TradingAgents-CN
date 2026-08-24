"""
预计算因子区间缓存 —— 回测/网格搜索专用。

将 score_signals 逐日的多次 Mongo 查询，合并为区间 4 次批量查询 + 内存命中。
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from zstock.common.utils.common_utils import normalize_date
from zstock.data_management.query_service import (
    COL_FACTOR_DRAGON,
    COL_FACTOR_FORCE,
    COL_FACTOR_MARKET,
    COL_FACTOR_SECTOR,
)

logger = logging.getLogger(__name__)


class PrecomputedFactorCache:
    """按交易日索引的 M1~M4 预计算因子内存缓存。"""

    def __init__(self) -> None:
        self._market: Dict[str, Dict[str, Any]] = {}
        self._sectors: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._dragons: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._forces: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._range: Optional[Tuple[str, str]] = None

    @property
    def loaded(self) -> bool:
        return self._range is not None

    @property
    def date_range(self) -> Optional[Tuple[str, str]]:
        return self._range

    async def preload(self, qs: Any, start_date: str, end_date: str) -> None:
        """一次性加载区间内全部预计算因子文档。"""
        start = normalize_date(start_date)
        end = normalize_date(end_date)
        date_filter = {"$gte": start, "$lte": end}
        db = qs.database_service

        market_docs, sector_docs, dragon_docs, force_docs = await asyncio.gather(
            db.query(
                COL_FACTOR_MARKET,
                {"trade_date": date_filter},
                projection={"_id": 0},
            ),
            db.query(
                COL_FACTOR_SECTOR,
                {"trade_date": date_filter},
                projection={"_id": 0},
                sort=[("trade_date", 1), ("sector_code", 1)],
            ),
            db.query(
                COL_FACTOR_DRAGON,
                {"trade_date": date_filter},
                projection={"_id": 0},
                sort=[("trade_date", 1), ("sector_code", 1), ("code", 1)],
            ),
            db.query(
                COL_FACTOR_FORCE,
                {"trade_date": date_filter},
                projection={"_id": 0},
                sort=[("trade_date", 1), ("code", 1)],
            ),
        )

        self._market.clear()
        self._sectors.clear()
        self._dragons.clear()
        self._forces.clear()

        for doc in market_docs or []:
            td = normalize_date(doc.get("trade_date", ""))
            if td:
                self._market[td] = doc

        for doc in sector_docs or []:
            td = normalize_date(doc.get("trade_date", ""))
            if td:
                self._sectors[td].append(doc)

        for doc in dragon_docs or []:
            td = normalize_date(doc.get("trade_date", ""))
            if td:
                self._dragons[td].append(doc)

        for doc in force_docs or []:
            td = normalize_date(doc.get("trade_date", ""))
            if td:
                self._forces[td].append(doc)

        self._range = (start, end)
        logger.info(
            "📦 因子区间预加载 %s~%s: M1=%d 天, M2=%d 天, M3=%d 天, M4=%d 天",
            start,
            end,
            len(self._market),
            len(self._sectors),
            len(self._dragons),
            len(self._forces),
        )

    def get_factor_market(self, trade_date: str) -> Optional[Dict[str, Any]]:
        return self._market.get(normalize_date(trade_date))

    def get_factor_sectors(self, trade_date: str) -> List[Dict[str, Any]]:
        return list(self._sectors.get(normalize_date(trade_date), []))

    def get_factor_dragons(
        self,
        trade_date: str,
        sector_codes: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        docs = self._dragons.get(normalize_date(trade_date), [])
        if not sector_codes:
            return list(docs)
        allow = set(sector_codes)
        return [d for d in docs if d.get("sector_code") in allow]

    def get_factor_forces(self, trade_date: str) -> List[Dict[str, Any]]:
        return list(self._forces.get(normalize_date(trade_date), []))
