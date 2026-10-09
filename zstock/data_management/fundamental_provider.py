"""PIT-safe 基本面缓存：每股净资产 / 股东户数变动。

由调用方装载数据（query_service 或 xtdata），因子层只做 asof 查询。
"""

from __future__ import annotations

import logging
from bisect import bisect_right
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from zstock.common.utils.common_utils import normalize_code

logger = logging.getLogger(__name__)


def parse_date_int(val) -> int | None:
    """把日期解析成 YYYYMMDD 整数。

    接受 20240102、'2024-01-02'、'20240102000000000'、datetime 可转字符串的值。
    """
    if val is None:
        return None
    if isinstance(val, int):
        s = str(val)
    else:
        s = str(val).strip()
        if not s:
            return None
        digits: List[str] = []
        for ch in s:
            if ch.isdigit():
                digits.append(ch)
                if len(digits) == 8:
                    break
        s = "".join(digits)
    if len(s) < 8:
        return None
    try:
        return int(s[:8])
    except ValueError:
        return None


class FundamentalDataProvider:
    """财务数据缓存：BPS 与股东户数环比，按公告日 asof 查询。"""

    def __init__(self) -> None:
        self._bps_cache: Dict[str, List[Tuple[int, float]]] = {}
        self._holder_cache: Dict[str, List[Tuple[int, float]]] = {}
        self._loaded = False

    async def load_from_query_service(self, query_service: Any) -> None:
        """从 DataQueryService 装载全量 pershare / holder 文档。"""
        pershare = await query_service.get_all_fundamental_pershare()
        holder = await query_service.get_all_fundamental_holder()
        self.load_from_docs(pershare, holder)

    def load_from_docs(
        self,
        pershare_docs: Optional[List[Dict[str, Any]]],
        holder_docs: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """从已取出的 Mongo 文档构建 PIT 缓存。"""
        bps_by_code: Dict[str, List[Tuple[int, float]]] = {}
        for doc in pershare_docs or []:
            code = doc.get("code")
            ann_int = parse_date_int(doc.get("ann_date"))
            bps = doc.get("bps")
            if not code or ann_int is None or bps is None:
                continue
            try:
                bps = float(bps)
            except (TypeError, ValueError):
                continue
            if bps == 0 or not np.isfinite(bps):
                continue
            bps_by_code.setdefault(code, []).append((ann_int, bps))
        for entries in bps_by_code.values():
            entries.sort(key=lambda x: x[0])
        self._bps_cache = bps_by_code

        holder_by_code: Dict[str, List[Tuple[int, int, float]]] = {}
        for doc in holder_docs or []:
            code = doc.get("code")
            ann_int = parse_date_int(doc.get("ann_date"))
            end_int = parse_date_int(doc.get("end_date"))
            sh = doc.get("shareholder")
            if not code or ann_int is None or end_int is None or sh is None:
                continue
            try:
                sh_val = float(sh)
            except (TypeError, ValueError):
                continue
            if sh_val <= 0:
                continue
            holder_by_code.setdefault(code, []).append((end_int, ann_int, sh_val))

        processed: Dict[str, List[Tuple[int, float]]] = {}
        for code, records in holder_by_code.items():
            records.sort(key=lambda x: x[0])
            rates: List[Tuple[int, float]] = []
            for i in range(1, len(records)):
                prev_sh = records[i - 1][2]
                curr_sh = records[i][2]
                ann_int = records[i][1]
                rate = (curr_sh - prev_sh) / prev_sh
                if np.isfinite(rate):
                    rates.append((ann_int, rate))
            rates.sort(key=lambda x: x[0])
            processed[code] = rates
        self._holder_cache = processed
        self._loaded = True
        logger.info(
            "FundamentalDataProvider: BPS=%d HolderChange=%d",
            len(self._bps_cache),
            len(self._holder_cache),
        )

    def load_from_xtdata(
        self,
        xt_codes: List[str],
        start_time: str = "20230101",
        end_time: str = "20261231",
    ) -> None:
        """从 QMT xtdata 下载 Pershareindex / Holdernum 并构建缓存。"""
        try:
            from xtquant import xtdata
        except ImportError:
            from zstock.common.utils.xtquant_data_utils import _get_xtdata

            xtdata = _get_xtdata()
        if xtdata is None:
            raise RuntimeError("xtquant 无法加载，请确认 miniQMT 已启动且 xtquant 可用")

        xtdata.download_financial_data2(
            xt_codes, table_list=["Pershareindex"],
            start_time=start_time, end_time=end_time,
        )
        fin_data = xtdata.get_financial_data(
            xt_codes, table_list=["Pershareindex"],
            start_time=start_time, end_time=end_time,
            report_type="announce_time",
        )
        for xt_code, tables in (fin_data or {}).items():
            psh = tables.get("Pershareindex")
            if psh is None or len(psh) == 0:
                continue
            code = normalize_code(xt_code)
            entries: List[Tuple[int, float]] = []
            for _, row in psh.iterrows():
                ann_int = parse_date_int(row.get("m_anntime", ""))
                if ann_int is None:
                    continue
                bps = row.get("s_fa_bps", np.nan)
                try:
                    bps = float(bps)
                except (TypeError, ValueError):
                    continue
                if np.isnan(bps) or bps == 0:
                    continue
                entries.append((ann_int, bps))
            if entries:
                entries.sort(key=lambda x: x[0])
                self._bps_cache[code] = entries

        xtdata.download_financial_data2(
            xt_codes, table_list=["Holdernum"],
            start_time=start_time, end_time=end_time,
        )
        holder_data = xtdata.get_financial_data(
            xt_codes, table_list=["Holdernum"],
            start_time=start_time, end_time=end_time,
            report_type="announce_time",
        )
        for xt_code, tables in (holder_data or {}).items():
            hld = tables.get("Holdernum")
            if hld is None or len(hld) == 0:
                continue
            code = normalize_code(xt_code)
            period_rows: List[Tuple[int, int, float]] = []
            for _, row in hld.iterrows():
                decl = parse_date_int(row.get("declareDate", ""))
                end_int = parse_date_int(row.get("endDate", ""))
                if decl is None or end_int is None:
                    continue
                try:
                    sh_val = float(row.get("shareholder", None))
                except (TypeError, ValueError):
                    continue
                if sh_val <= 0:
                    continue
                period_rows.append((end_int, decl, sh_val))
            if len(period_rows) < 2:
                continue
            period_rows.sort(key=lambda x: x[0])
            entries = []
            for i in range(1, len(period_rows)):
                prev_sh = period_rows[i - 1][2]
                curr_sh = period_rows[i][2]
                change = (curr_sh - prev_sh) / prev_sh
                entries.append((period_rows[i][1], change))
            if entries:
                entries.sort(key=lambda x: x[0])
                self._holder_cache[code] = entries
        self._loaded = True

    def get_bps(self, code: str, asof_date: str) -> Optional[float]:
        entries = self._bps_cache.get(code)
        if not entries:
            return None
        asof_int = parse_date_int(asof_date)
        if asof_int is None:
            return None
        idx = bisect_right(entries, (asof_int, float("inf"))) - 1
        if idx < 0:
            return None
        return entries[idx][1]

    def get_holder_change(self, code: str, asof_date: str) -> Optional[float]:
        entries = self._holder_cache.get(code)
        if not entries:
            return None
        asof_int = parse_date_int(asof_date)
        if asof_int is None:
            return None
        idx = bisect_right(entries, (asof_int, float("inf"))) - 1
        if idx < 0:
            return None
        return entries[idx][1]

    def compute_pb(self, code: str, close: float, asof_date: str) -> Optional[float]:
        bps = self.get_bps(code, asof_date)
        if bps is None or bps == 0:
            return None
        pb = close / bps
        if not np.isfinite(pb) or pb <= 0:
            return None
        return pb

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def codes_with_pb(self) -> int:
        return len(self._bps_cache)

    def codes_with_holder(self) -> int:
        return len(self._holder_cache)
