"""
龙头层因子计算模块（M3）

黑盒设计：
- 公开接口：calculate_all_dragon_factors_in_sector()
- 所有实现细节都隐藏在私有方法中
- 职责：只计算单个板块内的M3龙头因子（4个因子的rank和合成）
"""

import logging
import pandas as pd
import numpy as np
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 龙头因子权重：连板高度占主导，量价共振作为价格行为质量校验
_W_F31 = 0.18   # F3.1 超额收益（相对板块中位数，增加区分度）
_W_F32 = 0.12   # F3.2 人气（当日成交额），与合力层解耦，降权
_W_F33 = 0.40   # F3.3 高度（连板天数），龙头最核心特征
_W_F34 = 0.18   # F3.4 量价共振度（主升放量/回调缩量，真龙头价格行为质量）
_W_F35 = 0.12   # F3.5 布林趋势（价格在中轨上方且中轨斜率为正）

# 涨停判定阈值（A股主板10%，创业板/科创板20%）
_LIMIT_UP_THRESHOLD = 0.095  # 涨幅 >= 9.5% 视为涨停（兼容四舍五入误差）


class DragonFactors:
    """龙头层因子计算器（M3）。黑盒设计，只负责龙头因子计算"""

    # ===================== 公开接口（唯一入口）=====================

    @staticmethod
    def calculate_all_dragon_factors_in_sector(
        sector_stocks: List[str],
        stock_ohlcv: Dict[str, pd.DataFrame],
    ) -> Dict[str, float]:
        """
        【公开接口】完整计算单个板块内M3龙头因子，返回0-100的龙头综合得分
        入参：
        sector_stocks: 板块内的股票列表
        stock_ohlcv: 股票的OHLCV数据字典，键为股票代码，值为DataFrame

        逻辑：接受原始 stock_ohlcv，内部自动计算：
        - F3.1 超额收益（5日收益 - 板块中位数）
        - F3.2 人气（当日成交额）
        - F3.3 高度（从 OHLCV 计算连续涨停天数）
        - F3.4 量价共振度

        返回：Dict[stock_code] → float (0-100)
        """
        # 内部从 OHLCV 计算 5d 收益 / 当日成交量 / 连板天数 / 布林趋势
        stock_5d_returns = DragonFactors._compute_5d_returns(stock_ohlcv, sector_stocks)
        stock_daily_volumes = DragonFactors._compute_daily_volumes(stock_ohlcv, sector_stocks)
        stock_consecutive_boards = DragonFactors._compute_consecutive_boards(stock_ohlcv, sector_stocks)
        stock_bollinger_trend = DragonFactors._compute_bollinger_trend(stock_ohlcv, sector_stocks)

        sector_returns = {s: stock_5d_returns[s] for s in sector_stocks if s in stock_5d_returns}
        sector_volumes = {s: stock_daily_volumes[s] for s in sector_stocks if s in stock_daily_volumes}
        sector_boards = {s: stock_consecutive_boards[s] for s in sector_stocks if s in stock_consecutive_boards}
        sector_bollinger = {s: stock_bollinger_trend[s] for s in sector_stocks if s in stock_bollinger_trend}

        f31_raw = DragonFactors._calculate_leading_performance_raw(sector_returns)
        f32_raw = DragonFactors._calculate_popularity_raw(sector_volumes)
        f33_raw = DragonFactors._calculate_height_raw(sector_boards)
        f34_raw = DragonFactors._calculate_volume_price_resonance_raw(sector_stocks, stock_ohlcv)
        f35_raw = sector_bollinger  # 布林趋势已是 0-100 分

        f31_norm = DragonFactors._minmax_normalize(f31_raw)
        f32_norm = DragonFactors._minmax_normalize(f32_raw)
        f33_norm = DragonFactors._minmax_normalize(f33_raw)
        f34_norm = DragonFactors._minmax_normalize(f34_raw)
        f35_norm = DragonFactors._minmax_normalize(f35_raw)

        m3_scores = DragonFactors._combine_five_factors(f31_norm, f32_norm, f33_norm, f34_norm, f35_norm)

        logger.info(f"✅ M3 龙头因子计算完成: {len(m3_scores)} 只")
        return m3_scores

    # ===================== 私有方法（实现细节，对外隐藏）=====================

    @staticmethod
    def _calculate_leading_performance_raw(stock_returns: Dict[str, float]) -> Dict[str, float]:
        """
        【私有】F3.1：超额收益（个股5日收益 - 板块中位数）

        核心逻辑：
        - 用中位数替代均值作为板块基准，避免极端个股（如妖股连续涨停）
          拉高均值导致大部分正常股票超额收益为负
        - 板块内横向比较，消除板块整体涨跌影响
        """
        if not stock_returns:
            return {}
        sector_median = float(np.median(list(stock_returns.values())))
        return {k: v - sector_median for k, v in stock_returns.items()}

    @staticmethod
    def _calculate_popularity_raw(stock_volumes: Dict[str, float]) -> Dict[str, float]:
        """【私有】F3.2：人气（当日成交额）"""
        return dict(stock_volumes)

    @staticmethod
    def _calculate_height_raw(consecutive_boards: Dict[str, int]) -> Dict[str, float]:
        """【私有】F3.3：高度（连板天数）"""
        return {k: float(v) for k, v in consecutive_boards.items()}

    # ---------- 从 OHLCV 衍生基础指标 ----------

    @staticmethod
    def _compute_5d_returns(stock_ohlcv: Dict[str, pd.DataFrame], codes: List[str]) -> Dict[str, float]:
        """【私有】从 OHLCV 计算 5 日收益率"""
        result = {}
        for code in codes:
            if code not in stock_ohlcv:
                continue
            df = stock_ohlcv[code]
            if df.empty or 'close' not in df.columns or len(df) < 6:
                continue
            closes = df['close'].astype(float).tolist()
            if closes[-6] > 0:
                result[code] = (closes[-1] - closes[-6]) / closes[-6]
        return result

    @staticmethod
    def _compute_daily_volumes(stock_ohlcv: Dict[str, pd.DataFrame], codes: List[str]) -> Dict[str, float]:
        """【私有】从 OHLCV 提取当日成交额（amount 末行）"""
        result = {}
        for code in codes:
            if code not in stock_ohlcv:
                continue
            df = stock_ohlcv[code]
            if df.empty:
                continue
            if 'amount' in df.columns:
                result[code] = float(df['amount'].iloc[-1])
            elif 'volume' in df.columns:
                result[code] = float(df['volume'].iloc[-1])
        return result

    @staticmethod
    def _compute_consecutive_boards(stock_ohlcv: Dict[str, pd.DataFrame], codes: List[str]) -> Dict[str, int]:
        """【私有】从 OHLCV 计算最近连续涨停天数"""
        result = {}
        for code in codes:
            if code not in stock_ohlcv:
                continue
            df = stock_ohlcv[code]
            if len(df) < 2 or 'close' not in df.columns:
                continue
            closes = df['close'].astype(float).values
            count = 0
            for i in range(len(closes) - 1, 0, -1):
                prev = closes[i - 1]
                if prev <= 0:
                    break
                if closes[i] / prev - 1.0 >= _LIMIT_UP_THRESHOLD:
                    count += 1
                else:
                    break
            result[code] = count
        return result

    @staticmethod
    def _compute_bollinger_trend(
        stock_ohlcv: Dict[str, pd.DataFrame],
        codes: List[str],
        window: int = 20,
        std_dev: float = 2.0,
        slope_window: int = 5,
    ) -> Dict[str, float]:
        """
        【私有】F3.5：布林趋势得分（0-100）

        计算逻辑：
        1. 计算 20 日布林带中轨（MA20）
        2. 计算中轨的 5 日斜率（线性回归）
        3. 判断当前价格相对中轨位置：
           - close > mid 且 slope > 0 → 强势（80-100分）
           - close > mid 但 slope <= 0 → 中性偏强（50-70分）
           - close < mid 且 slope < 0 → 弱势（0-30分）
           - close < mid 但 slope >= 0 → 中性偏弱（30-50分）

        得分 = 位置分（0-60） + 斜率分（0-40）
        - 位置分：close vs mid，上方=60，下方=0，线性插值
        - 斜率分：slope 归一化到 0-40，正斜率得分高
        """
        result = {}
        for code in codes:
            if code not in stock_ohlcv:
                continue
            df = stock_ohlcv[code]
            min_bars = window + slope_window
            if len(df) < min_bars or 'close' not in df.columns:
                continue

            closes = df['close'].astype(float).values

            # 计算布林中轨（MA20）
            mid = pd.Series(closes).rolling(window=window, min_periods=window).mean().values

            # 取最近 slope_window 根有效中轨计算斜率
            valid_mid = mid[~np.isnan(mid)]
            if len(valid_mid) < slope_window:
                continue

            recent_mid = valid_mid[-slope_window:]
            x = np.arange(slope_window)
            slope = float(np.polyfit(x, recent_mid, 1)[0])

            # 当前价格和中轨
            current_close = float(closes[-1])
            current_mid = float(valid_mid[-1])

            if current_mid <= 0:
                continue

            # 位置分（0-60）：close vs mid
            # close > mid → 60分，close < mid → 0分，线性插值
            position_ratio = (current_close - current_mid) / current_mid
            # 限制在 [-0.1, 0.1] 范围内映射到 [0, 60]
            position_ratio = np.clip(position_ratio, -0.1, 0.1)
            position_score = 30 + position_ratio * 300  # -0.1→0, 0→30, 0.1→60
            position_score = np.clip(position_score, 0, 60)

            # 斜率分（0-40）：slope 归一化
            # slope > 0 → 高分，slope < 0 → 低分
            # 假设斜率范围 [-0.5, 0.5]（相对中轨的日均变化率）
            slope_normalized = slope / current_mid  # 相对斜率
            slope_normalized = np.clip(slope_normalized, -0.005, 0.005)
            slope_score = 20 + slope_normalized * 4000  # -0.005→0, 0→20, 0.005→40
            slope_score = np.clip(slope_score, 0, 40)

            # 综合得分
            total_score = float(position_score + slope_score)
            result[code] = total_score

        return result


    @staticmethod
    def _calculate_volume_price_resonance_raw(
        sector_stocks: List[str],
        stock_ohlcv: Optional[Dict[str, pd.DataFrame]],
        window: int = 5,
    ) -> Dict[str, float]:
        """
        【私有】F3.4：量价共振度

        核心逻辑：
        统计近 window 日内 "量价健康" 天数占比（0~1），三种情况计为健康：
          1. 价涨 + 量涨（主升放量，有资金跟进）
          2. 价跌 + 量缩（回调缩量，无人恐慌抛售）
          3. 涨停日（涨幅 >= 9.5%），无论量能方向均视为正面信号
             ——涨停缩量说明封板坚定（卖盘枯竭），涨停放量说明强势突破

        量价背离 = 虚龙头拉高出货特征（放量滞涨/缩量硬拉）。
        若无 OHLCV 数据则不加入 result dict，由 _combine_four_factors 用中性值 50 填充，
        避免默认值与真实计算值混合后归一化扭曲无数据股票的排名。
        """
        result = {}
        for code in sector_stocks:
            if stock_ohlcv is None or code not in stock_ohlcv:
                continue
            df = stock_ohlcv[code]
            if len(df) < window + 1:
                continue
            if 'close' not in df.columns:
                continue
            tail = df.tail(window + 1)
            close_arr = tail['close'].values

            if 'amount' in df.columns:
                vol_arr = tail['amount'].values
            elif 'volume' in df.columns:
                vol_arr = tail['volume'].values
            else:
                continue

            # 计算每日变化量（从第2根K线开始，共 window 个交易日）
            price_chg = np.diff(close_arr)   # shape: (window,)
            vol_chg = np.diff(vol_arr)       # shape: (window,)

            # 计算每日涨幅，用于判断是否涨停
            # prev_close 为前一日收盘价，从 close_arr[0] 到 close_arr[-2]
            prev_close = close_arr[:-1]
            daily_return = np.where(prev_close > 0, price_chg / prev_close, 0.0)

            # 统计健康天数：量价同向 OR 涨停日
            is_sync = ((price_chg > 0) & (vol_chg > 0)) | ((price_chg < 0) & (vol_chg < 0))
            is_limit_up = daily_return >= _LIMIT_UP_THRESHOLD
            healthy_days = int((is_sync | is_limit_up).sum())

            result[code] = float(healthy_days / window)
        return result

    @staticmethod
    def _minmax_normalize(values_dict: Dict[str, float]) -> Dict[str, float]:
        """【私有】min-max归一化转0-100"""
        if not values_dict:
            return {}
        min_val = min(values_dict.values())
        max_val = max(values_dict.values())
        if max_val == min_val:
            return {k: 50.0 for k in values_dict.keys()}
        return {k: 100 * (v - min_val) / (max_val - min_val) for k, v in values_dict.items()}

    @staticmethod
    def _combine_five_factors(
        f31: Dict[str, float],
        f32: Dict[str, float],
        f33: Dict[str, float],
        f34: Dict[str, float],
        f35: Dict[str, float],
    ) -> Dict[str, float]:
        """【私有】加权合成5个因子为M3得分（F3.3连板权重最高）。

        若某只票只在部分因子里出现，缺失因子按中性值 50 补齐，避免被静默丢弃。
        """
        all_stocks = set(f31) | set(f32) | set(f33) | set(f34) | set(f35)
        return {
            s: (
                _W_F31 * f31.get(s, 50.0)
                + _W_F32 * f32.get(s, 50.0)
                + _W_F33 * f33.get(s, 50.0)
                + _W_F34 * f34.get(s, 50.0)
                + _W_F35 * f35.get(s, 50.0)
            )
            for s in all_stocks
        }
