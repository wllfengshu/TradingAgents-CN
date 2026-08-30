"""基本面因子提供者 (sync → MongoDB → 缓存)

数据流程:
  1. sync_fundamental.py: xtdata → MongoDB
     - zstock_fundamental_pershare: {code, ann_date, bps, eps, roe, rev_growth, profit_growth, debt_ratio, gross_margin}
     - zstock_fundamental_holder: {code, ann_date, end_date, shareholder}
  2. FundamentalDataProvider.load_from_mongodb(): MongoDB → 内存缓存
  3. PIT-safe 查询: get_bps() / get_holder_change() / compute_pb()

关键约束:
  - PIT-safe: 用 ann_date (披露日) 做 asof 查询, 不用报告截止日
  - HolderChange: 按 end_date (报告期) 排序计算变化率, 用 ann_date 做 PIT-safe
"""
from __future__ import annotations

import logging
from bisect import bisect_right
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class FundamentalDataProvider:
    """财务数据提供者 -- MongoDB 缓存 + PIT-safe 查询

    Usage:
        provider = FundamentalDataProvider()
        provider.load_from_mongodb()                # 从 MongoDB 加载 (推荐)
        pb = provider.compute_pb("000001", 10.5, "2024-06-15")
        hc = provider.get_holder_change("000001", "2024-06-15")
    """

    def __init__(self):
        # code -> [(ann_date_int, value), ...] sorted by ann_date
        self._bps_cache: Dict[str, List[Tuple[int, float]]] = {}
        self._holder_cache: Dict[str, List[Tuple[int, float]]] = {}
        self._loaded = False

    # ───── 加载 ─────

    def load_from_mongodb(self) -> None:
        """从 MongoDB 加载基本面数据 (需先运行 sync_fundamental.py)

        读取:
          - zstock_fundamental_pershare: {code, ann_date("YYYY-MM-DD"), bps, eps, roe, ...}
          - zstock_fundamental_holder: {code, ann_date("YYYY-MM-DD"), end_date("YYYY-MM-DD"), shareholder}
        """
        from pymongo import MongoClient
        from app.core.config import settings

        client = MongoClient(
            settings.MONGO_URI,
            serverSelectionTimeoutMS=5000,
        )
        db = client[settings.MONGO_DB]

        # ── Pershareindex (BPS/EPS/ROE/增长/负债率) ──
        COL_PERSHARE = "zstock_fundamental_pershare"
        ps_count = db[COL_PERSHARE].count_documents({})
        if ps_count == 0:
            logger.warning(f"FundamentalDataProvider: {COL_PERSHARE} 为空, 请先运行 sync_fundamental.py")
            return
        logger.info(f"FundamentalDataProvider: 从 MongoDB 加载 Pershareindex ({ps_count} 条)...")

        # 按 code 分组, 构建 BPS 缓存 (用于 PB 计算)
        bps_by_code: Dict[str, List[Tuple[int, float]]] = {}
        for doc in db[COL_PERSHARE].find(
            {"bps": {"$exists": True, "$ne": None}},
            {"code": 1, "ann_date": 1, "bps": 1},
        ).sort([("code", 1), ("ann_date", 1)]):
            code = doc.get("code")
            ann_str = doc.get("ann_date")
            bps = doc.get("bps")
            if not code or not ann_str or bps is None:
                continue
            ann_int = _date_str_to_int(ann_str)
            if ann_int is None:
                continue
            try:
                bps = float(bps)
            except (TypeError, ValueError):
                continue
            if bps == 0 or not np.isfinite(bps):
                continue
            bps_by_code.setdefault(code, []).append((ann_int, bps))

        for code, entries in bps_by_code.items():
            entries.sort(key=lambda x: x[0])
        self._bps_cache = bps_by_code
        logger.info(f"  BPS cache: {len(self._bps_cache)} stocks")

        # ── HolderChange ──
        COL_HOLDER = "zstock_fundamental_holder"
        holder_count = db[COL_HOLDER].count_documents({})
        if holder_count == 0:
            logger.warning(f"FundamentalDataProvider: {COL_HOLDER} 为空, 请先运行 sync_fundamental.py")
            self._loaded = True
            return
        logger.info(f"FundamentalDataProvider: 从 MongoDB 加载 Holdernum ({holder_count} 条)...")

        holder_by_code: Dict[str, List[Tuple[int, int, float]]] = {}  # code -> [(end_date_int, ann_date_int, shareholder)]
        for doc in db[COL_HOLDER].find(
            {}, {"code": 1, "ann_date": 1, "end_date": 1, "shareholder": 1},
        ).sort([("code", 1), ("end_date", 1)]):
            code = doc.get("code")
            ann_str = doc.get("ann_date")
            end_str = doc.get("end_date")
            sh = doc.get("shareholder")
            if not code or not ann_str or not end_str or sh is None:
                continue
            ann_int = _date_str_to_int(ann_str)
            end_int = _date_str_to_int(end_str)
            if ann_int is None or end_int is None:
                continue
            try:
                sh_val = float(sh)
            except (TypeError, ValueError):
                continue
            if sh_val <= 0:
                continue
            holder_by_code.setdefault(code, []).append((end_int, ann_int, sh_val))

        # 计算 holder_change: 按 end_date 排序后算 pct_change, 再按 ann_date 排序做 PIT-safe
        holder_cache: Dict[str, List[Tuple[int, float]]] = {}
        for code, period_rows in holder_by_code.items():
            if len(period_rows) < 2:
                continue
            period_rows.sort(key=lambda x: x[0])  # 按 end_date 排序
            entries: List[Tuple[int, float]] = []
            for i in range(1, len(period_rows)):
                prev_sh = period_rows[i - 1][2]
                curr_sh = period_rows[i][2]
                change = (curr_sh - prev_sh) / prev_sh
                entries.append((period_rows[i][1], change))  # (ann_date_int, change)
            if entries:
                entries.sort(key=lambda x: x[0])  # 按 ann_date 排序 (PIT-safe)
                holder_cache[code] = entries

        self._holder_cache = holder_cache
        logger.info(f"  HolderChange cache: {len(self._holder_cache)} stocks")

        client.close()
        self._loaded = True
        logger.info("FundamentalDataProvider: MongoDB 加载完成")

    def load_from_xtdata(self, xt_codes: List[str], start_time: str = "20230101", end_time: str = "20261231"):
        """从 QMT xtdata 下载并解析财务数据，构建缓存

        Args:
            xt_codes: xtquant 格式代码列表, e.g. ["000001.SZ", "600000.SH"]
            start_time / end_time: 下载时间范围
        """
        import os
        import sys

        xtdata = None
        # 方式1: 直接导入 (pip安装 / QMT内置Python)
        try:
            from xtquant import xtdata as _xtdata
            xtdata = _xtdata
            logger.info("FundamentalDataProvider: xtquant 直接导入成功")
        except ImportError:
            pass

        # 方式2: 手动注入路径后导入
        if xtdata is None:
            qmt_path = r"D:\GJZQqmt\国金证券QMT交易端"
            for sub in ["userdata_mini", "userdata", ""]:
                candidate = os.path.join(qmt_path, sub) if sub else qmt_path
                xtq_dir = os.path.join(candidate, "xtquant")
                if os.path.isdir(xtq_dir) and candidate not in sys.path:
                    sys.path.insert(0, candidate)
                    logger.info(f"FundamentalDataProvider: 注入路径 {candidate}")
                    break
            try:
                from xtquant import xtdata as _xtdata
                xtdata = _xtdata
                logger.info("FundamentalDataProvider: xtquant 路径注入后导入成功")
            except ImportError:
                pass

        # 方式3: 通过 _get_xtdata (兜底)
        if xtdata is None:
            from zstock.common.utils.xtquant_data_utils import _get_xtdata
            xtdata = _get_xtdata()

        if xtdata is None:
            raise RuntimeError("xtquant 无法加载，请确认 miniQMT 已启动且 xtquant 可用")

        # --- Pershareindex -> BPS ---
        logger.info(f"FundamentalDataProvider: 下载 Pershareindex ({len(xt_codes)} stocks)...")
        xtdata.download_financial_data2(
            xt_codes, table_list=["Pershareindex"],
            start_time=start_time, end_time=end_time,
        )
        fin_data = xtdata.get_financial_data(
            xt_codes, table_list=["Pershareindex"],
            start_time=start_time, end_time=end_time,
            report_type="announce_time",
        )
        for xt_code, tables in fin_data.items():
            psh = tables.get("Pershareindex")
            if psh is None or len(psh) == 0:
                continue
            code = _from_xt_code(xt_code)
            entries: List[Tuple[int, float]] = []
            for _, row in psh.iterrows():
                ann_int = _parse_date_int(row.get("m_anntime", ""))
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
        logger.info(f"  BPS cache: {len(self._bps_cache)} stocks")

        # --- Holdernum -> HolderChange ---
        logger.info(f"FundamentalDataProvider: 下载 Holdernum ({len(xt_codes)} stocks)...")
        xtdata.download_financial_data2(
            xt_codes, table_list=["Holdernum"],
            start_time=start_time, end_time=end_time,
        )
        holder_data = xtdata.get_financial_data(
            xt_codes, table_list=["Holdernum"],
            start_time=start_time, end_time=end_time,
            report_type="announce_time",
        )
        for xt_code, tables in holder_data.items():
            hld = tables.get("Holdernum")
            if hld is None or len(hld) == 0:
                continue
            code = _from_xt_code(xt_code)
            # 解析股东数时间序列, 计算变化率
            # 关键: 按 endDate (报告期) 排序计算变化率, 用 declareDate (公告日) 做 PIT-safe
            period_rows: List[Tuple[int, int, float]] = []  # (end_date_int, ann_date_int, shareholder_count)
            for _, row in hld.iterrows():
                decl = _parse_date_int(row.get("declareDate", ""))
                end_int = _parse_date_int(row.get("endDate", ""))
                if decl is None or end_int is None:
                    continue
                sh = row.get("shareholder", None)
                try:
                    sh_val = float(sh)
                except (TypeError, ValueError):
                    continue
                if sh_val <= 0:
                    continue
                period_rows.append((end_int, decl, sh_val))
            if len(period_rows) < 2:
                continue
            # 按报告期 (endDate) 排序, 确保变化率计算在连续报告期之间
            period_rows.sort(key=lambda x: x[0])
            # 计算 holder_change = (current - prev) / prev
            entries: List[Tuple[int, float]] = []
            for i in range(1, len(period_rows)):
                prev_sh = period_rows[i - 1][2]
                curr_sh = period_rows[i][2]
                change = (curr_sh - prev_sh) / prev_sh
                # 用当前期的公告日做 PIT-safe 索引
                entries.append((period_rows[i][1], change))
            if entries:
                # 必须按公告日排序, 保证 bisect_right 正确工作
                entries.sort(key=lambda x: x[0])
                self._holder_cache[code] = entries
        logger.info(f"  HolderChange cache: {len(self._holder_cache)} stocks")
        self._loaded = True

    # ───── PIT-safe 查询 ─────

    def get_bps(self, code: str, asof_date: str) -> Optional[float]:
        """返回 asof_date 之前最新披露的 BPS (PIT-safe)

        Args:
            code: 6位股票代码
            asof_date: 日期字符串 "YYYY-MM-DD" 或 "YYYYMMDD"
        """
        entries = self._bps_cache.get(code)
        if not entries:
            return None
        asof_int = _date_to_int(asof_date)
        # bisect_right: 找到最后一个 ann_date <= asof_int 的位置
        idx = bisect_right(entries, (asof_int, float("inf"))) - 1
        if idx < 0:
            return None
        return entries[idx][1]

    def get_holder_change(self, code: str, asof_date: str) -> Optional[float]:
        """返回 asof_date 之前最新披露的股东数变化率 (PIT-safe)

        变化率 = (current - prev) / prev, 正数表示股东增加(筹码分散)
        """
        entries = self._holder_cache.get(code)
        if not entries:
            return None
        asof_int = _date_to_int(asof_date)
        idx = bisect_right(entries, (asof_int, float("inf"))) - 1
        if idx < 0:
            return None
        return entries[idx][1]

    def compute_pb(self, code: str, close: float, asof_date: str) -> Optional[float]:
        """PB = close / BPS

        **重要口径约定**：
        - BPS 来自定期报告披露的"每股净资产"，是绝对金额，不随除权除息调整。
        - 因此 `close` 必须是**未复权价（raw close）**——如果传入前复权价，
          除权除息日之后价格会被等比缩小而 BPS 不变，PB 将被系统性低估，
          导致 f39 因子方向反向。
        - 上游若只有前复权价，请先按累计除权因子还原为 raw close 再传入；
          `sync_ohlcv` 已在 MongoDB 中保留原始价，务必从"未复权"字段取值。
        """
        bps = self.get_bps(code, asof_date)
        if bps is None or bps == 0:
            return None
        pb = close / bps
        if not np.isfinite(pb) or pb <= 0:
            return None
        return pb

    # ───── 属性 ─────

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def codes_with_pb(self) -> int:
        return len(self._bps_cache)

    def codes_with_holder(self) -> int:
        return len(self._holder_cache)


# ───── 工具函数 ─────

def _from_xt_code(xt_code: str) -> str:
    return xt_code.split(".")[0]


def _date_to_int(d: str) -> int:
    """将日期字符串转为整数 YYYYMMDD"""
    return int(d.replace("-", "")[:8])


def _date_str_to_int(d) -> int | None:
    """将 MongoDB 中的日期 ("YYYY-MM-DD" 或 "YYYYMMDD") 转为 int。
    兼容 int 输入（旧数据）。返回 None 表示无效。"""
    if d is None:
        return None
    if isinstance(d, int):
        return d
    s = str(d).replace("-", "").replace("/", "")[:8]
    if len(s) < 8:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _parse_date_int(val) -> int | None:
    """稳健日期解析: 支持 '20240331', '2024-03-31', '20240331000000000',
    pandas Timestamp 等格式。返回 YYYYMMDD 整数或 None。"""
    if val is None:
        return None
    s = str(val).strip()
    if not s or len(s) < 8:
        return None
    # 去掉连字符/斜杠后取前8位数字
    digits = s.replace("-", "").replace("/", "").replace(" ", "")[:8]
    if len(digits) < 8:
        return None
    try:
        return int(digits)
    except ValueError:
        return None
