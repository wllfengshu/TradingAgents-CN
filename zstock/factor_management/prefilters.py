"""
预过滤模块

黑盒设计：
- 公开接口：apply_technical_filters() / apply_blacklist_filters()
- 所有实现细节都隐藏在私有方法中
- 职责1：技术过滤（主板、布林等）
- 职责2：黑名单过滤（板块、个股）
"""

import logging
import pandas as pd
from typing import Any, List, Dict, Set, Optional
from datetime import datetime
import json
from pathlib import Path
import fnmatch

from zstock.common.utils.common_utils import (
    ensure_ohlcv_sorted,
    is_main_board,
    is_st,
    normalize_code,
)

logger = logging.getLogger(__name__)


class PreFilters:
    """预过滤器。黑盒设计，只暴露 2 个清晰的公开接口"""

    def __init__(self, config_dir: str = None):
        """初始化预过滤器，加载黑名单"""
        if config_dir is None:
            config_dir = Path(__file__).parent.parent / "common/config"
        self.config_dir = Path(config_dir)
        self.sector_blacklist: Set[str] = set()
        self.stock_blacklist: Dict[str, Dict] = {}
        self._normalized_stock_blacklist: Set[str] = set()  # 预建 normalize 后的集合，O(1) 查找
        self._load_blacklists()

    def _load_blacklists(self):
        """加载板块和个股黑名单"""
        try:
            # 加载板块黑名单
            sector_bl_path = self.config_dir / "blacklist_sector.json"
            if sector_bl_path.exists():
                with open(sector_bl_path, 'r', encoding='utf-8') as f:
                    sector_data = json.load(f)
                    self.sector_blacklist = set(sector_data.get("sectors", []))
                    logger.info(f"✅ 加载板块黑名单: {len(self.sector_blacklist)} 个")

            # 加载个股黑名单
            stock_bl_path = self.config_dir / "blacklist_stock.json"
            if stock_bl_path.exists():
                with open(stock_bl_path, 'r', encoding='utf-8') as f:
                    stock_data = json.load(f)
                    for item in stock_data.get("stocks", []):
                        if isinstance(item, str):
                            self.stock_blacklist[item] = {}
                        else:
                            code = item.get("code")
                            if not code:
                                logger.error(f"⚠️ 个股黑名单条目缺少 code 字段，跳过: {item}")
                                continue
                            until = item.get("until", "")
                            if until and until < datetime.now().strftime("%Y-%m-%d"):
                                continue  # 已过期，不加入黑名单
                            self.stock_blacklist[code] = item
                    logger.info(f"✅ 加载个股黑名单: {len(self.stock_blacklist)} 个")
            # 预建 normalize 后的集合，避免 O(n) 扫描
            self._normalized_stock_blacklist = {
                normalize_code(c) for c in self.stock_blacklist
            }
            logger.debug(f"  normalize 黑名单: {len(self._normalized_stock_blacklist)} 个唯一代码")
        except Exception as e:
            logger.error(f"❌ 加载黑名单失败: {e}")
            raise

    def _reload_blacklists(self):
        """重新加载黑名单（支持盘中修改，仅供内部使用）"""
        self.sector_blacklist.clear()
        self.stock_blacklist.clear()
        self._normalized_stock_blacklist.clear()
        self._load_blacklists()
        logger.info("✅ 黑名单已重新加载")

    # ===================== 公开接口1：技术过滤 =====================

    def apply_technical_filters(
        self,
        stocks: List[str],  # 股票代码列表
        stock_data: Dict[str, pd.DataFrame],  # {code: DataFrame} OHLCV 数据
        stock_infos: Optional[Dict[str, Dict]] = None,  # {code: info} 含 is_st 标志（可选）
        apply_main_board: bool = True,  # 是否应用主板过滤
        apply_bollinger: bool = False,  # 是否应用布林过滤
        bollinger_slope_threshold: float = 0.0,  # 布林斜率阈值
    ) -> List[str]:
        """
        【公开接口1】综合技术过滤（主板、布林等）

        入参：
          - stocks: 股票代码列表
          - stock_data: {code: DataFrame} OHLCV 数据，用于布林计算
          - stock_infos: 股票信息字典，含 is_st/name 字段，用于 ST 判断
          - apply_main_board: 是否应用主板过滤
          - apply_bollinger: 是否应用布林过滤
          - bollinger_slope_threshold: 布林斜率阈值（默认0，代表中轨斜率 > 0）

        出参：
          List[str]，通过技术过滤的股票代码列表

        过滤规则（内部自动处理）：
        1. 主板过滤：60(沪) 或 00(深) + 非 ST
        2. 布林过滤：close > mid AND slope_5 > threshold
        """
        if apply_main_board:
            stocks = self._apply_main_board_filter(stocks, stock_infos)

        if apply_bollinger:
            filtered_data = self._apply_bollinger_filter(
                stock_data,
                slope_threshold=bollinger_slope_threshold,
            )
            stocks = [c for c in stocks if c in filtered_data]
            logger.info(f"✅ M3.3 布林过滤完成: {len(stock_data)} → {len(filtered_data)} 只")

        return stocks

    # ===================== 公开接口2：黑名单过滤 =====================

    def apply_blacklist_filters(
        self,
        stocks: List[str],  # 股票代码列表
        sectors: Optional[List[Dict]] = None,  # 板块列表（可选）
    ) -> Dict[str, Any]:
        """
        【公开接口2】综合黑名单过滤（板块、个股）

        入参：
          - stocks: 股票代码列表
          - sectors: 板块列表（可选），每个元素含 sector_name 字段

        出参：
          {
              "stocks": 过滤后的股票代码列表,
              "sectors": 过滤后的板块列表（如果输入了 sectors）
          }

        过滤规则（内部自动处理）：
        1. 个股黑名单：排除已过期及黑名单中的股票
        2. 板块黑名单：支持通配符（例如"*房地产*"）
        """
        filtered_stocks = self._filter_stocks(stocks)
        result: Dict[str, Any] = {"stocks": filtered_stocks}

        if sectors:
            filtered_sectors = self._filter_sectors(sectors)
            result["sectors"] = filtered_sectors

        return result

    # ===================== 私有方法（实现细节，对外隐藏）=====================

    def _apply_main_board_filter(
        self,
        stocks: List[str],  # 股票代码列表
        stock_infos: Optional[Dict[str, Dict]] = None,  # 股票信息字典（可选）
    ) -> List[str]:
        """【私有】主板过滤实现（改为同步方法）"""
        result = []
        for code in stocks:
            normalized = normalize_code(code)
            name = ""
            flagged_st = False
            if stock_infos:
                info = stock_infos.get(code) or stock_infos.get(normalized) or {}
                name = info.get("name", "") or ""
                flagged_st = bool(info.get("is_st", False))
            if (
                is_main_board(normalized)
                and not flagged_st
                and not is_st(name)
                and "退" not in name
            ):
                result.append(code)
        logger.info(f"✅ 主板过滤完成: {len(stocks)} → {len(result)} 只")
        return result

    def _apply_bollinger_filter(
        self,
        stock_data: Dict[str, pd.DataFrame],  # {code: DataFrame} OHLCV
        slope_threshold: float = 0.0,  # 斜率阈值
    ) -> Dict[str, pd.DataFrame]:
        """【私有】布林上升滤实现（改为同步方法）"""
        result = {}
        for code, df in stock_data.items():
            df = ensure_ohlcv_sorted(df)
            if df is None or df.empty or len(df) < 25:
                continue
            if self._check_bollinger_condition(df, slope_threshold):
                result[code] = df
                logger.debug(f"✅ {code} 通过布林过滤")
        return result

    def _filter_sectors(self, sectors: List[Dict]) -> List[Dict]:
        """【私有】板块黑名单过滤实现"""
        result = []
        for sector in sectors:
            sector_name = sector.get('sector_name', '')
            if not self._is_sector_blacklisted(sector_name):
                result.append(sector)
        if len(sectors) > len(result):
            logger.info(f"📊 板块黑名单过滤: {len(sectors)} → {len(result)}")
        return result

    def _filter_stocks(self, stocks: List[str]) -> List[str]:
        """【私有】个股黑名单过滤实现"""
        result = [code for code in stocks if not self._is_stock_blacklisted(code)]
        filtered_count = len(stocks) - len(result)
        if filtered_count > 0:
            logger.info(f"📊 个股黑名单过滤: {len(stocks)} → {len(result)}（排除 {filtered_count} 只）")
        return result

    def _is_sector_blacklisted(self, sector_name: str) -> bool:
        """【私有】判断板块是否在黑名单中（支持fnmatch通配）"""
        for pattern in self.sector_blacklist:
            if fnmatch.fnmatch(sector_name, pattern):
                return True
        return False

    def _is_stock_blacklisted(self, code: str) -> bool:
        """【私有】判断个股是否在黑名单中（O(1) 预建集合查找，兼容带后缀/不带后缀格式）。"""
        if code in self.stock_blacklist:
            return True
        return normalize_code(code) in self._normalized_stock_blacklist

    @staticmethod
    def _calculate_bollinger_bands(
        df: pd.DataFrame,
        window: int = 20,
        std_dev: int = 2,
    ) -> pd.DataFrame:
        """【私有】计算布林带"""
        if len(df) < window:
            return pd.DataFrame()
        df = df.copy()
        df['mid'] = df['close'].rolling(window=window).mean()
        df['std'] = df['close'].rolling(window=window).std()
        df['upper'] = df['mid'] + std_dev * df['std']
        df['lower'] = df['mid'] - std_dev * df['std']
        return df[['mid', 'upper', 'lower']].copy()

    @staticmethod
    def _calculate_bollinger_slope(
        df: pd.DataFrame,
        slope_window: int = 5,
    ) -> pd.Series:
        """【私有】计算布林中轨斜率"""
        if len(df) < slope_window:
            return pd.Series(0, index=df.index)
        slope = (df['mid'] - df['mid'].shift(slope_window)) / df['mid'].shift(slope_window)
        return slope

    @staticmethod
    def _check_bollinger_condition(
        df: pd.DataFrame,
        slope_threshold: float = 0.0,
        window: int = 20,
        std_dev: int = 2,
        slope_window: int = 5,
    ) -> bool:
        """
        【私有】检查单只股票是否通过布林上升滤

        条件：
        1. close > mid（价在中轨上）
        2. slope_5 > slope_threshold（中轨斜率为正或超过阈值）

        注意：调用方（_apply_bollinger_filter）已确保 df 排序，此处不重复排序。
        """
        min_bars = window + slope_window
        if df is None or len(df) < min_bars:
            return False
        df = df.copy()
        boll = PreFilters._calculate_bollinger_bands(df, window=window, std_dev=std_dev)
        df['mid'] = boll['mid']
        df['slope'] = PreFilters._calculate_bollinger_slope(df, slope_window=slope_window)
        mask = (df['close'] > df['mid']) & (df['slope'] > slope_threshold)
        return bool(mask.iloc[-1]) if len(mask) > 0 else False

