"""
股票池 / 板块宇宙准入过滤（非因子）。

职责：
- 技术过滤：主板、ST/退市、可选布林上升
- 黑名单过滤：板块（支持通配）、个股（支持 until 过期）
"""

from __future__ import annotations

import fnmatch
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import pandas as pd

from zstock.common.utils.common_utils import (
    ensure_ohlcv_sorted,
    is_main_board,
    is_st,
    normalize_code,
)

logger = logging.getLogger(__name__)


class PreFilters:
    """宇宙准入过滤器。"""

    def __init__(self, config_dir: str | Path | None = None):
        if config_dir is None:
            config_dir = Path(__file__).resolve().parent / "config"
        self.config_dir = Path(config_dir)
        self.sector_blacklist: Set[str] = set()
        self.stock_blacklist: Dict[str, Dict] = {}
        self._normalized_stock_blacklist: Set[str] = set()
        self._load_blacklists()

    def _load_blacklists(self):
        try:
            sector_bl_path = self.config_dir / "blacklist_sector.json"
            if sector_bl_path.exists():
                with open(sector_bl_path, "r", encoding="utf-8") as f:
                    sector_data = json.load(f)
                    self.sector_blacklist = set(sector_data.get("sectors", []))
                    logger.info(f"✅ 加载板块黑名单: {len(self.sector_blacklist)} 个")

            stock_bl_path = self.config_dir / "blacklist_stock.json"
            if stock_bl_path.exists():
                with open(stock_bl_path, "r", encoding="utf-8") as f:
                    stock_data = json.load(f)
                    for item in stock_data.get("stocks", []):
                        if isinstance(item, str):
                            self.stock_blacklist[item] = {}
                        else:
                            code = item.get("code")
                            if not code:
                                logger.error(f"⚠️ 个股黑名单条目缺少 code 字段，跳过: {item}")
                                continue
                            self.stock_blacklist[code] = item
                    logger.info(f"✅ 加载个股黑名单: {len(self.stock_blacklist)} 个")
            self._normalized_stock_blacklist = {
                normalize_code(c) for c in self.stock_blacklist
            }
            logger.debug(f"  normalize 黑名单: {len(self._normalized_stock_blacklist)} 个唯一代码")
        except Exception as e:
            logger.error(f"❌ 加载黑名单失败: {e}")
            raise

    def reload_blacklists(self):
        """重新加载黑名单（支持盘中改配置）。"""
        self.sector_blacklist.clear()
        self.stock_blacklist.clear()
        self._normalized_stock_blacklist.clear()
        self._load_blacklists()
        logger.info("✅ 黑名单已重新加载")

    def filter_universe(
        self,
        stocks: List[str],
        stock_infos: Optional[Dict[str, Dict]] = None,
        sectors: Optional[List[Dict]] = None,
        stock_data: Optional[Dict[str, pd.DataFrame]] = None,
        apply_main_board: bool = True,
        apply_bollinger: bool = False,
        bollinger_slope_threshold: float = 0.0,
        as_of: Optional[str] = None,
    ) -> Dict[str, Any]:
        """综合股票池与板块宇宙准入（主板/ST/布林 + 黑名单）。"""
        tech_filtered = self.apply_technical_filters(
            stocks=stocks,
            stock_data=stock_data or {},
            stock_infos=stock_infos,
            apply_main_board=apply_main_board,
            apply_bollinger=apply_bollinger,
            bollinger_slope_threshold=bollinger_slope_threshold,
            as_of=as_of,
        )
        blacklist_result = self.apply_blacklist_filters(
            tech_filtered, sectors=sectors, as_of=as_of
        )
        non_blacklisted = blacklist_result["stocks"]
        allowed_sectors = blacklist_result.get("sectors") if sectors is not None else []
        blacklisted_codes = set(tech_filtered) - set(non_blacklisted)
        tech_out = set(stocks) - set(tech_filtered)

        return {
            "stocks": non_blacklisted,
            "sectors": allowed_sectors,
            "blacklisted_stocks": blacklisted_codes,
            "technically_filtered_out": tech_out,
        }

    def apply_technical_filters(
        self,
        stocks: List[str],
        stock_data: Dict[str, pd.DataFrame],
        stock_infos: Optional[Dict[str, Dict]] = None,
        apply_main_board: bool = True,
        apply_bollinger: bool = False,
        bollinger_slope_threshold: float = 0.0,
        as_of: Optional[str] = None,
    ) -> List[str]:
        """主板 / ST / 可选布林上升过滤。"""
        if apply_main_board:
            stocks = self.apply_main_board_filter(stocks, stock_infos, as_of=as_of)

        if apply_bollinger:
            filtered_data = self.apply_bollinger_filter(
                stock_data,
                slope_threshold=bollinger_slope_threshold,
            )
            stocks = [c for c in stocks if c in filtered_data]
            logger.info(f"✅ 布林过滤完成: {len(stock_data)} → {len(filtered_data)} 只")

        return stocks

    def apply_blacklist_filters(
        self,
        stocks: List[str],
        sectors: Optional[List[Dict]] = None,
        as_of: Optional[str] = None,
    ) -> Dict[str, Any]:
        """个股 + 板块黑名单过滤。"""
        filtered_stocks = self.filter_stocks(stocks, as_of=as_of)
        result: Dict[str, Any] = {"stocks": filtered_stocks}

        if sectors:
            result["sectors"] = self.filter_sectors(sectors)

        return result

    def apply_main_board_filter(
        self,
        stocks: List[str],
        stock_infos: Optional[Dict[str, Dict]] = None,
        as_of: Optional[str] = None,
    ) -> List[str]:
        """主板过滤。历史日期不用今天的 ST 名称，只按上市/退市日截断。"""
        from zstock.common.utils.common_utils import normalize_date

        result = []
        today = datetime.now().strftime("%Y-%m-%d")
        ref = normalize_date(as_of) if as_of else None
        live = ref is None or ref >= today
        for code in stocks:
            normalized = normalize_code(code)
            name = ""
            flagged_st = False
            info: Dict[str, Any] = {}
            if stock_infos:
                raw_info = stock_infos.get(code) or stock_infos.get(normalized) or {}
                info = raw_info if isinstance(raw_info, dict) else {"name": str(raw_info)}
                name = info.get("name", "") or ""
                flagged_st = bool(info.get("is_st", False))
            if not is_main_board(normalized):
                continue
            if ref:
                listed = info.get("list_date")
                delisted = info.get("delist_date")
                try:
                    if listed and normalize_date(str(listed)) > ref:
                        continue
                except (TypeError, ValueError):
                    pass
                try:
                    if delisted and normalize_date(str(delisted)) <= ref:
                        continue
                except (TypeError, ValueError):
                    pass
            if live and (flagged_st or is_st(name) or "退" in name):
                continue
            result.append(code)
        logger.info(f"✅ 主板过滤完成: {len(stocks)} → {len(result)} 只")
        return result

    def apply_bollinger_filter(
        self,
        stock_data: Dict[str, pd.DataFrame],
        slope_threshold: float = 0.0,
    ) -> Dict[str, pd.DataFrame]:
        """布林上升：close > mid 且中轨斜率 > 阈值。"""
        result = {}
        for code, df in stock_data.items():
            df = ensure_ohlcv_sorted(df)
            if df is None or df.empty or len(df) < 25:
                continue
            if self.check_bollinger_condition(df, slope_threshold):
                result[code] = df
                logger.debug(f"✅ {code} 通过布林过滤")
        return result

    def filter_sectors(self, sectors: List[Dict]) -> List[Dict]:
        result = []
        for sector in sectors:
            sector_name = sector.get("sector_name", "")
            if not self.is_sector_blacklisted(sector_name):
                result.append(sector)
        if len(sectors) > len(result):
            logger.info(f"📊 板块黑名单过滤: {len(sectors)} → {len(result)}")
        return result

    def filter_stocks(self, stocks: List[str], as_of: Optional[str] = None) -> List[str]:
        result = [code for code in stocks if not self.is_stock_blacklisted(code, as_of=as_of)]
        filtered_count = len(stocks) - len(result)
        if filtered_count > 0:
            logger.info(
                f"📊 个股黑名单过滤: {len(stocks)} → {len(result)}（排除 {filtered_count} 只）"
            )
        return result

    def is_sector_blacklisted(self, sector_name: str) -> bool:
        for pattern in self.sector_blacklist:
            if fnmatch.fnmatch(sector_name, pattern):
                return True
        return False

    def is_stock_blacklisted(self, code: str, as_of: Optional[str] = None) -> bool:
        from zstock.common.utils.common_utils import normalize_date

        norm = normalize_code(code)
        item = None
        if code in self.stock_blacklist:
            item = self.stock_blacklist[code]
        else:
            for raw, payload in self.stock_blacklist.items():
                if normalize_code(raw) == norm:
                    item = payload
                    break
        if item is None:
            return False
        until = str((item or {}).get("until") or "")
        ref = normalize_date(as_of) if as_of else datetime.now().strftime("%Y-%m-%d")
        if until and until < ref:
            return False
        return True

    @staticmethod
    def calculate_bollinger_bands(
        df: pd.DataFrame,
        window: int = 20,
        std_dev: int = 2,
    ) -> pd.DataFrame:
        if len(df) < window:
            return pd.DataFrame()
        df = df.copy()
        df["mid"] = df["close"].rolling(window=window).mean()
        df["std"] = df["close"].rolling(window=window).std()
        df["upper"] = df["mid"] + std_dev * df["std"]
        df["lower"] = df["mid"] - std_dev * df["std"]
        return df[["mid", "upper", "lower"]].copy()

    @staticmethod
    def calculate_bollinger_slope(
        df: pd.DataFrame,
        slope_window: int = 5,
    ) -> pd.Series:
        if len(df) < slope_window:
            return pd.Series(0, index=df.index)
        return (df["mid"] - df["mid"].shift(slope_window)) / df["mid"].shift(slope_window)

    @staticmethod
    def check_bollinger_condition(
        df: pd.DataFrame,
        slope_threshold: float = 0.0,
        window: int = 20,
        std_dev: int = 2,
        slope_window: int = 5,
    ) -> bool:
        min_bars = window + slope_window
        if df is None or len(df) < min_bars:
            return False
        df = df.copy()
        boll = PreFilters.calculate_bollinger_bands(df, window=window, std_dev=std_dev)
        df["mid"] = boll["mid"]
        df["slope"] = PreFilters.calculate_bollinger_slope(df, slope_window=slope_window)
        mask = (df["close"] > df["mid"]) & (df["slope"] > slope_threshold)
        return bool(mask.iloc[-1]) if len(mask) > 0 else False
