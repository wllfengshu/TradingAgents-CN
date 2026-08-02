"""
预过滤模块

黑盒设计：
- 公开接口：apply_main_board_filter()、apply_bollinger_filter()、filter_sectors()、filter_stocks()
- 所有实现细节都隐藏在私有方法中
- 调用方只需提供数据，获得过滤结果
"""

import logging
import pandas as pd
from typing import List, Dict, Set, Optional
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
    """预过滤器。黑盒设计，只暴露清晰的公开接口"""

    def __init__(self, config_dir: str = None):
        """初始化预过滤器，加载黑名单"""
        if config_dir is None:
            config_dir = Path(__file__).parent.parent / "common/config"
        self.config_dir = Path(config_dir)
        self.sector_blacklist: Set[str] = set()
        self.stock_blacklist: Dict[str, Dict] = {}
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
        except Exception as e:
            logger.error(f"❌ 加载黑名单失败: {e}")
            raise

    def reload_blacklists(self):
        """重新加载黑名单（支持盘中修改）"""
        self.sector_blacklist.clear()
        self.stock_blacklist.clear()
        self._load_blacklists()
        logger.info("✅ 黑名单已重新加载")

    # ===================== 公开接口1：主板过滤 =====================

    async def apply_main_board_filter(self, stocks: List[str], stock_infos: Optional[Dict[str, Dict]] = None) -> List[str]:
        """
        【公开接口1】主板过滤

        入参：
          - stocks: 股票代码列表
          - stock_infos: 股票信息字典（可选，用于检查ST标志）

        出参：
          List[str]，通过主板过滤的股票代码列表

        过滤规则（内部自动处理）：
        1. 代码前缀必须是 60 (沪) 或 00 (深)
        2. 如果是 00 开头，第3位必须是 0/1/2/3
        3. 排除 ST、*ST、退市股票
        """
        result = []
        for code in stocks:
            normalized = normalize_code(code)
            # stock_infos 的 key 可能是原始代码或规范化代码，两种都尝试匹配
            name = ""
            flagged_st = False
            if stock_infos:
                info = stock_infos.get(code) or stock_infos.get(normalized) or {}
                name = info.get("name", "") or ""
                flagged_st = bool(info.get("is_st", False))
            # 主板且非 ST（优先 is_st 字段，名称兜底）且非退市
            if (
                is_main_board(normalized)
                and not flagged_st
                and not is_st(name)
                and "退" not in name
            ):
                result.append(code)
        logger.info(f"✅ M2.2 主板过滤完成: {len(stocks)} → {len(result)} 只")
        return result

    # ===================== 公开接口2：布林过滤 =====================

    async def apply_bollinger_filter(self, stock_data: Dict[str, pd.DataFrame], slope_threshold: float = 0.0) -> Dict[str, pd.DataFrame]:
        """
        【公开接口2】布林上升滤

        入参：
          - stock_data: Dict[stock_code] → pd.DataFrame(OHLCV)，必须包含 close 列
          - slope_threshold: 斜率阈值（默认0，可调整为0.002等）

        出参：
          Dict[stock_code] → pd.DataFrame，通过布林过滤的股票及其OHLCV数据

        过滤规则（内部自动处理）：
        1. 计算布林带（20日均线 ± 2倍标准差）
        2. 计算布林中轨的5日斜率
        3. 条件：close > mid AND slope_5 > slope_threshold
        """
        result = {}
        for code, df in stock_data.items():
            df = ensure_ohlcv_sorted(df)
            if df is None or df.empty or len(df) < 25:  # window(20)+slope(5)
                continue
            if self._apply_bollinger_filter_check(df, slope_threshold):
                result[code] = df
                logger.debug(f"✅ {code} 通过 M3.3 布林过滤")
        logger.info(f"✅ M3.3 布林过滤完成: {len(stock_data)} → {len(result)} 只")
        return result

    # ===================== 公开接口3：板块黑名单过滤 =====================

    def filter_sectors(self, sectors: List[Dict]) -> List[Dict]:
        """
        【公开接口3】板块黑名单过滤

        入参：
          - sectors: 板块列表，每个元素包含 sector_name 字段

        出参：
          List[Dict]，排除黑名单后的板块列表

        过滤规则（内部自动处理）：
        1. 逐个检查板块名称
        2. 支持 * 通配符（例如"*房地产*"匹配"中国房地产"）
        3. 排除匹配的板块
        """
        result = []
        for sector in sectors:
            sector_name = sector.get('sector_name', '')
            if not self._is_sector_blacklisted(sector_name):
                result.append(sector)
        logger.info(f"📊 M2.1板块黑名单过滤: {len(sectors)} → {len(result)}")
        return result

    # ===================== 公开接口4：个股黑名单过滤 =====================

    def filter_stocks(self, stocks: List[str]) -> List[str]:
        """
        【公开接口4b】个股黑名单批量过滤

        入参：
          - stocks: 股票代码列表

        出参：
          List[str]，排除黑名单后的股票代码列表

        过滤规则（内部自动处理）：
        1. 逐个检查股票代码是否在黑名单中
        2. 已过期的黑名单条目（until < today）在加载时已自动剔除
        """
        result = [code for code in stocks if not self._is_stock_blacklisted(code)]
        filtered_count = len(stocks) - len(result)
        if filtered_count > 0:
            logger.info(f"📊 个股黑名单过滤: {len(stocks)} → {len(result)}（排除 {filtered_count} 只）")
        return result

    # ===================== 私有方法（实现细节，对外隐藏）=====================


    def _is_sector_blacklisted(self, sector_name: str) -> bool:
        """【私有】判断板块是否在黑名单中（支持fnmatch通配）"""
        for pattern in self.sector_blacklist:
            if fnmatch.fnmatch(sector_name, pattern):
                return True
        return False

    def _is_stock_blacklisted(self, code: str) -> bool:
        """【私有】判断个股是否在黑名单中（兼容带后缀/不带后缀格式）"""
        # 直接匹配
        if code in self.stock_blacklist:
            return True
        # 归一化后匹配（黑名单可能是 000981.SZ，code 是 000981）
        normalized = normalize_code(code)
        for bl_code in self.stock_blacklist:
            if normalize_code(bl_code) == normalized:
                return True
        return False

    @staticmethod
    def _calculate_bollinger_bands(df: pd.DataFrame, window: int = 20, std_dev: int = 2) -> pd.DataFrame:
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
    def _calculate_bollinger_slope(df: pd.DataFrame, slope_window: int = 5) -> pd.Series:
        """【私有】计算布林中轨斜率"""
        if len(df) < slope_window:
            return pd.Series(0, index=df.index)
        slope = (df['mid'] - df['mid'].shift(slope_window)) / df['mid'].shift(slope_window)
        return slope

    @staticmethod
    def _apply_bollinger_filter_check(df: pd.DataFrame, slope_threshold: float = 0.0, window: int = 20, std_dev: int = 2, slope_window: int = 5) -> bool:
        """
        【私有】检查单只股票是否通过布林上升滤

        条件：
        1. close > mid（价在中轨上）
        2. slope_5 > slope_threshold（中轨斜率为正或超过阈值）

        最少需要 window + slope_window 根 K 线：
        rolling(window) 需要 window 根产生首个有效 mid，
        shift(slope_window) 再需要 slope_window 根才能计算最后一根的斜率。
        """
        min_bars = window + slope_window
        df = ensure_ohlcv_sorted(df)
        if df is None or len(df) < min_bars:
            return False
        df = df.copy()
        boll = PreFilters._calculate_bollinger_bands(df, window=window, std_dev=std_dev)
        df['mid'] = boll['mid']
        df['slope'] = PreFilters._calculate_bollinger_slope(df, slope_window=slope_window)
        mask = (df['close'] > df['mid']) & (df['slope'] > slope_threshold)
        return bool(mask.iloc[-1]) if len(mask) > 0 else False
