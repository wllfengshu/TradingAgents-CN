"""
从 MongoDB 加载因子面板与价格面板，供 FactorEvaluator 使用。

因子来源：
  - M2 板块：zstock_factor_sector  → columns=sector_code
  - M3 龙头：zstock_factor_dragon  → columns=stock_code（跨板块取均值）
  - M4 合力：zstock_factor_force   → columns=stock_code
价格：
  - 个股：zstock_ohlcv close
  - 板块：成分股等权合成 close

P0 对齐策略流水线：
  - 条件宇宙：每日 Top-N 板块 ∩ 主板非 ST
  - 负 IC 因子取反：f32_amount / fcoop4_turnover_quality（与打分侧一致）
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 策略实际用到的默认因子字段
DEFAULT_STOCK_FACTORS: Dict[str, List[str]] = {
    "dragon": [
        "f31_excess_return_5d",
        "f32_amount",
        "f33_consecutive_boards",
        "f34_resonance_pct_5d",
        "f35_bollinger_trend",
    ],
    "force": [
        "fcoop1_main_net_ratio",
        "fcoop2_main_retail_ratio",
        "fcoop3_sustained_days_5d",
        "fcoop4_turnover_quality",
    ],
}

DEFAULT_SECTOR_FACTORS: List[str] = [
    "f21_rps_20d",
    "f22_main_flow",
    "f23_limit_up_density",
    "f24_max_consecutive",
    "f25_volume_slope_5d",
]

# 与打分侧极性一致：测评时乘 -1，使 IC 反映策略面向信号
INVERT_FIELDS_FOR_EVAL: Set[str] = {
    "f32_amount",
    "fcoop4_turnover_quality",
}

# M2 存储字段 → scores_from_raw 键
_SECTOR_RAW_FIELD_MAP: Dict[str, str] = {
    "f21_rps": "f21_rps_20d",
    "f22_main_flow": "f22_main_flow",
    "f23_limit_up_density": "f23_limit_up_density",
    "f24_max_consecutive": "f24_max_consecutive",
    "f25_volume_slope": "f25_volume_slope_5d",
}


class FactorEvalDataLoader:
    """MongoDB → 宽表面板加载器。"""

    def __init__(self) -> None:
        from zstock.data_management.database_service import get_database_service
        from zstock.data_management.query_service import get_data_query_service

        self.db = get_database_service()
        self.qs = get_data_query_service()
        self._universe_cache: Dict[Tuple[str, str, int], Dict[str, Set[str]]] = {}
        self._mainboard_codes: Optional[Set[str]] = None
        self._sector_members: Optional[Dict[str, List[str]]] = None

    async def load_stock_factor_panel(
        self,
        collection: str,
        field: str,
        start_date: str,
        end_date: str,
        code_key: str = "code",
    ) -> pd.DataFrame:
        """
        加载个股因子宽表：index=trade_date, columns=code。
        同日同码多条（多板块）取均值。
        """
        from zstock.common.utils.common_utils import normalize_date

        start, end = normalize_date(start_date), normalize_date(end_date)
        docs = await self.db.query(
            collection,
            {"trade_date": {"$gte": start, "$lte": end}},
            projection={"_id": 0, "trade_date": 1, code_key: 1, field: 1},
        )
        if not docs:
            logger.warning(f"{collection}.{field} {start}~{end} 无数据")
            return pd.DataFrame()

        df = pd.DataFrame(docs)
        if field not in df.columns or code_key not in df.columns:
            return pd.DataFrame()
        df[field] = pd.to_numeric(df[field], errors="coerce")
        panel = (
            df.groupby(["trade_date", code_key], as_index=False)[field]
            .mean()
            .pivot(index="trade_date", columns=code_key, values=field)
            .sort_index()
        )
        logger.info(
            f"加载因子 {collection}.{field}: "
            f"{panel.shape[0]} 日 × {panel.shape[1]} 标的"
        )
        return panel

    async def load_sector_factor_panel(
        self,
        field: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        from zstock.data_management.query_service import COL_FACTOR_SECTOR

        return await self.load_stock_factor_panel(
            COL_FACTOR_SECTOR, field, start_date, end_date, code_key="sector_code"
        )

    async def load_price_panel(
        self,
        codes: Sequence[str],
        start_date: str,
        end_date: str,
        extra_days: int = 40,
    ) -> pd.DataFrame:
        """
        个股收盘价宽表。extra_days 用于 forward return 尾部。
        """
        from datetime import datetime, timedelta

        from zstock.common.utils.common_utils import normalize_date

        if not codes:
            return pd.DataFrame()

        start = normalize_date(start_date)
        end = normalize_date(end_date)
        end_ext = (
            datetime.strptime(end, "%Y-%m-%d") + timedelta(days=extra_days)
        ).strftime("%Y-%m-%d")

        ohlcv_map = await self.qs.get_ohlcv_batch(list(codes), start, end_ext)
        frames = []
        for code, df in ohlcv_map.items():
            if df is None or df.empty or "close" not in df.columns:
                continue
            tmp = df[["trade_date", "close"]].copy()
            tmp["trade_date"] = tmp["trade_date"].astype(str)
            tmp["close"] = pd.to_numeric(tmp["close"], errors="coerce")
            tmp = tmp.rename(columns={"close": code})
            frames.append(tmp.set_index("trade_date")[[code]])

        if not frames:
            return pd.DataFrame()

        price = pd.concat(frames, axis=1).sort_index()
        logger.info(f"加载价格面板: {price.shape[0]} 日 × {price.shape[1]} 股票")
        return price

    async def load_sector_price_panel(
        self,
        start_date: str,
        end_date: str,
        sector_codes: Optional[Sequence[str]] = None,
        extra_days: int = 40,
    ) -> pd.DataFrame:
        """
        板块合成收盘价：成分股等权收益累计。
        """
        from datetime import datetime, timedelta

        from zstock.common.utils.common_utils import normalize_date

        start = normalize_date(start_date)
        end = normalize_date(end_date)
        end_ext = (
            datetime.strptime(end, "%Y-%m-%d") + timedelta(days=extra_days)
        ).strftime("%Y-%m-%d")

        sectors, _ = await self.qs.get_sector_list()
        sectors = sectors or []
        if sector_codes:
            sc_set = set(sector_codes)
            sectors = [s for s in sectors if s.get("sector_code") in sc_set]

        all_codes = sorted(
            {
                c
                for s in sectors
                for c in (s.get("stocks") or [])
                if c
            }
        )
        if not all_codes:
            return pd.DataFrame()

        stock_price = await self.load_price_panel(all_codes, start, end_ext, extra_days=0)
        if stock_price.empty:
            return pd.DataFrame()

        stock_ret = stock_price.pct_change(fill_method=None)
        sector_close: Dict[str, pd.Series] = {}
        for s in sectors:
            sc = s.get("sector_code")
            members = [c for c in (s.get("stocks") or []) if c in stock_ret.columns]
            if not members or not sc:
                continue
            # 等权日收益 → 合成指数
            sec_ret = stock_ret[members].mean(axis=1, skipna=True).fillna(0.0)
            sector_close[sc] = 100.0 * (1.0 + sec_ret).cumprod()

        if not sector_close:
            return pd.DataFrame()
        panel = pd.DataFrame(sector_close).sort_index()
        logger.info(f"加载板块价格: {panel.shape[0]} 日 × {panel.shape[1]} 板块")
        return panel

    async def _ensure_universe_meta(self) -> None:
        if self._mainboard_codes is not None and self._sector_members is not None:
            return
        stocks, _ = await self.qs.get_all_stocks()
        self._mainboard_codes = {
            d["code"]
            for d in (stocks or [])
            if d.get("is_mainboard") and not d.get("is_st") and d.get("code")
        }
        sectors, _ = await self.qs.get_sector_list()
        self._sector_members = {
            s["sector_code"]: list(s.get("stocks") or [])
            for s in (sectors or [])
            if s.get("sector_code")
        }
        logger.info(
            f"宇宙元数据: 主板非ST={len(self._mainboard_codes)}  "
            f"板块={len(self._sector_members)}"
        )

    async def build_conditional_universe(
        self,
        start_date: str,
        end_date: str,
        top_n: int = 3,
    ) -> Dict[str, Set[str]]:
        """
        每日条件宇宙：M2 合成得分 Top-N 板块成分 ∩ 主板非 ST。
        返回 trade_date → {stock_code, ...}
        """
        from zstock.common.utils.common_utils import normalize_date
        from zstock.factor_management.sector_factors import SectorFactors

        start, end = normalize_date(start_date), normalize_date(end_date)
        cache_key = (start, end, int(top_n))
        if cache_key in self._universe_cache:
            return self._universe_cache[cache_key]

        await self._ensure_universe_meta()
        assert self._mainboard_codes is not None
        assert self._sector_members is not None

        panels: Dict[str, pd.DataFrame] = {}
        for raw_key, store_field in _SECTOR_RAW_FIELD_MAP.items():
            panels[raw_key] = await self.load_sector_factor_panel(
                store_field, start, end
            )

        # 取各面板日期并集（有 M2 的交易日）
        all_dates: Set[str] = set()
        for p in panels.values():
            if not p.empty:
                all_dates.update(str(d) for d in p.index)
        dates = sorted(all_dates)

        universe: Dict[str, Set[str]] = {}
        for td in dates:
            raw: Dict[str, Dict[str, float]] = {}
            for raw_key, panel in panels.items():
                if panel.empty or td not in panel.index:
                    raw[raw_key] = {}
                    continue
                row = panel.loc[td]
                raw[raw_key] = {
                    str(sc): float(v)
                    for sc, v in row.items()
                    if v == v  # not nan
                }
            scores = SectorFactors.scores_from_raw(raw)
            if not scores:
                universe[td] = set()
                continue
            top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]
            members: Set[str] = set()
            for sc, _ in top:
                for code in self._sector_members.get(sc, []):
                    if code in self._mainboard_codes:
                        members.add(code)
            universe[td] = members

        avg_n = (
            float(np.mean([len(v) for v in universe.values()])) if universe else 0.0
        )
        logger.info(
            f"条件宇宙 Top{top_n}: {len(universe)} 日, 日均标的≈{avg_n:.0f}"
        )
        self._universe_cache[cache_key] = universe
        return universe

    @staticmethod
    def apply_universe_mask(
        factor: pd.DataFrame,
        universe: Dict[str, Set[str]],
    ) -> pd.DataFrame:
        """非宇宙内标的置 NaN（按日）。"""
        if factor.empty or not universe:
            return factor
        out = factor.copy()
        for td in out.index:
            allowed = universe.get(str(td))
            if not allowed:
                out.loc[td] = np.nan
                continue
            keep = [c for c in out.columns if c in allowed]
            drop = [c for c in out.columns if c not in allowed]
            if drop:
                out.loc[td, drop] = np.nan
            # 无 keep 时该日全 nan（已处理）
            _ = keep
        # 丢掉全程无有效值的列
        out = out.dropna(axis=1, how="all")
        return out

    @staticmethod
    def apply_polarity_invert(
        factor: pd.DataFrame,
        field: str,
        invert: bool,
    ) -> pd.DataFrame:
        if not invert or field not in INVERT_FIELDS_FOR_EVAL or factor.empty:
            return factor
        logger.info(f"极性取反: {field} → -{field}")
        return -factor

    async def load_eval_bundle_stock(
        self,
        layer: str,
        field: str,
        start_date: str,
        end_date: str,
        conditional: bool = False,
        top_sectors: int = 3,
        invert_negative: bool = False,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """返回 (factor_panel, price_panel)。"""
        from zstock.data_management.query_service import (
            COL_FACTOR_DRAGON,
            COL_FACTOR_FORCE,
        )

        col_map = {"dragon": COL_FACTOR_DRAGON, "force": COL_FACTOR_FORCE}
        if layer not in col_map:
            raise ValueError(f"未知个股层: {layer}，可选 dragon/force")

        factor = await self.load_stock_factor_panel(
            col_map[layer], field, start_date, end_date
        )
        if factor.empty:
            return factor, pd.DataFrame()

        if conditional:
            universe = await self.build_conditional_universe(
                start_date, end_date, top_n=top_sectors
            )
            factor = self.apply_universe_mask(factor, universe)

        factor = self.apply_polarity_invert(factor, field, invert_negative)

        if factor.empty or factor.dropna(how="all").empty:
            return pd.DataFrame(), pd.DataFrame()

        # 价格只需有因子覆盖的股票（掩码后仍可能很宽，按非全 nan 列取）
        codes = list(factor.columns)
        price = await self.load_price_panel(codes, start_date, end_date)
        common_cols = factor.columns.intersection(price.columns)
        factor = factor[common_cols]
        price = price[common_cols]
        return factor, price

    async def load_eval_bundle_sector(
        self,
        field: str,
        start_date: str,
        end_date: str,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        factor = await self.load_sector_factor_panel(field, start_date, end_date)
        if factor.empty:
            return factor, pd.DataFrame()
        price = await self.load_sector_price_panel(
            start_date, end_date, sector_codes=list(factor.columns)
        )
        common_cols = factor.columns.intersection(price.columns)
        return factor[common_cols], price[common_cols]
