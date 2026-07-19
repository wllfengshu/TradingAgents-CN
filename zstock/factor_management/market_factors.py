"""
市场因子模块（M1）

职责：通过大盘指数的多维特征评估当日市场情绪，
      输出市场风险等级，决定策略是否允许开新仓/加仓。

黑盒设计：
- 公开接口：calculate_market_sentiment()
- 所有实现细节都隐藏在私有方法中

五个子因子（均以沪深300指数为主锚）：
  MF1 趋势强度（30%）：20日均线斜率，衡量中期趋势方向
  MF2 布林带位置（25%）：价格在布林带内的相对位置，衡量超买/超卖
  MF3 量能状态（20%）：当日成交量相对20日均量，判断缩量/放量
  MF4 近期动量（15%）：5日涨跌幅，快速反映短期趋势
  MF5 波动率压制（10%）：ATR比率的倒数，低波动=市场稳定

风险等级（market_grade）：
  green  [70, 100]：市场强势，正常开仓
  yellow [40,  70)：市场震荡，减仓系数 0.5（减半仓位）
  red    [ 0,  40)：市场弱势，不开新仓，不加仓

使用示例：
    sentiment = MarketFactors.calculate_market_sentiment(index_ohlcv_df)
    # sentiment = {
    #     'market_score': 72.5,
    #     'market_grade': 'green',
    #     'position_scale': 1.0,
    #     'mf1_trend': 68.0, 'mf2_boll': 75.0, ...
    # }
"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# 子因子权重
_W_MF1 = 0.30   # 趋势强度（均线斜率）
_W_MF2 = 0.25   # 布林带位置
_W_MF3 = 0.20   # 量能状态（成交量比率）
_W_MF4 = 0.15   # 近期动量（5日涨跌幅）
_W_MF5 = 0.10   # 波动率压制

# 风险等级阈值
_GRADE_GREEN  = 70.0
_GRADE_YELLOW = 40.0

# 各等级对应仓位缩放系数
_SCALE_GREEN  = 1.0
_SCALE_YELLOW = 0.5
_SCALE_RED    = 0.0

# 技术参数
_MA_WINDOW       = 20
_BOLL_STD        = 2.0
_SLOPE_WINDOW    = 5    # 用近 N 根均线估算斜率
_MOMENTUM_WINDOW = 5
_ATR_WINDOW      = 20
_VOL_MA_WINDOW   = 20
_MIN_BARS        = 21   # 至少需要的 K 线根数（ATR/MA 需要20根 + 1根计算 True Range）


class MarketFactors:
    """市场因子计算器（M1）。黑盒设计，只负责市场情绪评估"""

    # ===================== 公开接口（唯一入口）=====================

    @staticmethod
    def calculate_market_sentiment(
        index_ohlcv: pd.DataFrame,
        index_name: str = '沪深300',
    ) -> Dict:
        """
        【公开接口】根据指数 OHLCV 计算市场情绪得分与风险等级。

        Args:
            index_ohlcv: 指数日线 DataFrame，必须含列：
                         close, high, low, volume（按日期升序）
            index_name:  指数名称，仅用于日志标识

        Returns:
            {
                'market_score':   float,   # 综合市场得分 0~100
                'market_grade':   str,     # 'green' / 'yellow' / 'red'
                'position_scale': float,   # 仓位缩放系数 0.0 / 0.5 / 1.0
                'allow_new_open': bool,    # 是否允许开新仓
                'mf1_trend':      float,   # MF1 趋势强度得分 0~100
                'mf2_boll':       float,   # MF2 布林带位置得分 0~100
                'mf3_volume':     float,   # MF3 量能状态得分 0~100
                'mf4_momentum':   float,   # MF4 近期动量得分 0~100
                'mf5_volatility': float,   # MF5 波动率压制得分 0~100
                'detail':         dict,    # 各因子原始值，便于调试
            }

        若数据不足（< _MIN_BARS 根），返回 grade='yellow'，score=50（中性），
        避免因数据缺失而误判关闭开仓。
        """
        if index_ohlcv is None or len(index_ohlcv) < _MIN_BARS:
            logger.warning(
                f"⚠️ [{index_name}] 数据不足（{len(index_ohlcv) if index_ohlcv is not None else 0} 根 "
                f"< {_MIN_BARS}），返回中性评估"
            )
            return MarketFactors._neutral_result(index_name, reason='data_insufficient')

        df = index_ohlcv.copy().reset_index(drop=True)
        for col in ('close', 'high', 'low', 'volume'):
            if col not in df.columns:
                logger.warning(f"⚠️ [{index_name}] 缺少列 '{col}'，返回中性评估")
                return MarketFactors._neutral_result(index_name, reason=f'missing_col_{col}')
            df[col] = pd.to_numeric(df[col], errors='coerce')

        df = df.dropna(subset=['close', 'high', 'low', 'volume'])
        if len(df) < _MIN_BARS:
            return MarketFactors._neutral_result(index_name, reason='data_insufficient_after_clean')

        mf1, mf1_raw = MarketFactors._score_trend_strength(df)
        mf2, mf2_raw = MarketFactors._score_boll_position(df)
        mf3, mf3_raw = MarketFactors._score_volume_state(df)
        mf4, mf4_raw = MarketFactors._score_momentum(df)
        mf5, mf5_raw = MarketFactors._score_volatility(df)

        score = (
            _W_MF1 * mf1
            + _W_MF2 * mf2
            + _W_MF3 * mf3
            + _W_MF4 * mf4
            + _W_MF5 * mf5
        )
        score = float(np.clip(score, 0.0, 100.0))

        if score >= _GRADE_GREEN:
            grade = 'green'
            scale = _SCALE_GREEN
        elif score >= _GRADE_YELLOW:
            grade = 'yellow'
            scale = _SCALE_YELLOW
        else:
            grade = 'red'
            scale = _SCALE_RED

        allow_new_open = grade != 'red'

        logger.info(
            f"📊 [{index_name}] 市场得分={score:.1f} 等级={grade} "
            f"MF1={mf1:.0f} MF2={mf2:.0f} MF3={mf3:.0f} MF4={mf4:.0f} MF5={mf5:.0f}"
        )

        return {
            'market_score':   score,
            'market_grade':   grade,
            'position_scale': scale,
            'allow_new_open': allow_new_open,
            'mf1_trend':      mf1,
            'mf2_boll':       mf2,
            'mf3_volume':     mf3,
            'mf4_momentum':   mf4,
            'mf5_volatility': mf5,
            'detail': {
                'mf1_slope_pct':      mf1_raw,
                'mf2_boll_pct':       mf2_raw,
                'mf3_vol_ratio':      mf3_raw,
                'mf4_momentum_5d':    mf4_raw,
                'mf5_atr_ratio_inv':  mf5_raw,
            },
        }

    # ===================== 私有方法（实现细节，对外隐藏）=====================

    @staticmethod
    def _score_trend_strength(df: pd.DataFrame) -> tuple:
        """
        【私有】MF1：20日均线趋势强度。

        计算方式：
          1. 计算 20日 MA
          2. 用最近 _SLOPE_WINDOW 根均线做线性回归，得斜率
          3. 将斜率除以最新均线值，得"日均涨跌幅"（百分比斜率），消除绝对点位影响
          4. sigmoid 映射到 0~100（斜率=0 → 50，明显上升 → 接近100，明显下降 → 接近0）

        Sigmoid 参数 k=800：日均 0.12% 涨幅 → 约60分，日均 -0.12% → 约40分，
        覆盖正常指数波动区间，避免极端市场将得分压到边界。
        """
        close = df['close'].values
        ma = pd.Series(close).rolling(_MA_WINDOW, min_periods=_MA_WINDOW).mean().values

        # 取最近 _SLOPE_WINDOW 根有效均线
        valid_ma = ma[~np.isnan(ma)][-_SLOPE_WINDOW:]
        if len(valid_ma) < 2:
            return 50.0, float('nan')

        x = np.arange(len(valid_ma), dtype=float)
        slope = float(np.polyfit(x, valid_ma, 1)[0])
        if valid_ma[-1] <= 0 or np.isnan(valid_ma[-1]):
            return 50.0, float('nan')
        slope_pct = slope / valid_ma[-1]   # 相对斜率，消除绝对值影响

        # sigmoid 映射：s = 100 / (1 + exp(-k * slope_pct))
        k = 800.0
        exp_arg = np.clip(-k * slope_pct, -709, 709)
        score = 100.0 / (1.0 + np.exp(exp_arg))
        return float(np.clip(score, 0.0, 100.0)), slope_pct

    @staticmethod
    def _score_boll_position(df: pd.DataFrame) -> tuple:
        """
        【私有】MF2：布林带位置。

        计算当前收盘价在布林带 [lower, upper] 内的相对位置（0~1），
        线性映射到 0~100。
        - 价格在上轨附近 → 强势区域 → 接近100
        - 价格在中轨 → 中性 → 约50
        - 价格在下轨附近 → 弱势区域 → 接近0
        超出布林带的情况用 clip 限制在 [0, 1]，避免超买超卖的极端情况反向加分。
        """
        close = df['close'].values
        ma    = pd.Series(close).rolling(_MA_WINDOW, min_periods=_MA_WINDOW).mean()
        std   = pd.Series(close).rolling(_MA_WINDOW, min_periods=_MA_WINDOW).std()
        upper = ma + _BOLL_STD * std
        lower = ma - _BOLL_STD * std

        last_close = float(close[-1])
        last_upper = float(upper.iloc[-1])
        last_lower = float(lower.iloc[-1])

        if np.isnan(last_upper) or np.isnan(last_lower):
            return 50.0, float('nan')

        band_width = last_upper - last_lower

        if band_width <= 0:
            return 50.0, float('nan')

        boll_pct = (last_close - last_lower) / band_width
        boll_pct = float(np.clip(boll_pct, 0.0, 1.0))
        score = boll_pct * 100.0
        return score, boll_pct

    @staticmethod
    def _score_volume_state(df: pd.DataFrame) -> tuple:
        """
        【私有】MF3：量能状态。

        计算当日成交量与 20日均量的比值（vol_ratio）：
        - vol_ratio ∈ [0.5, 1.5] 是健康区间（正常量能）
        - < 0.5：严重缩量，市场冷淡，得分低
        - [0.5, 1.0]：量能略偏低，线性过渡
        - [1.0, 1.5]：量能正常偏高，得分高
        - [1.5, 3.0]：放量过大，可能是恐慌抛售或顶部信号，得分回落
        - > 3.0：极端放量（天量），恐慌/狂热信号，得分极低

        折线函数：
        ratio < 0.5  → 10
        0.5~1.0      → 10 + 80 * (ratio-0.5)/0.5
        1.0~1.5      → 90 + 10 * (ratio-1.0)/0.5
        1.5~3.0      → 100 - 80 * (ratio-1.5)/1.5
        > 3.0        → 20 - 10 * min((ratio-3.0)/2.0, 1.0)  → 最低10
        """
        vol    = df['volume'].values
        vol_ma = pd.Series(vol).rolling(_VOL_MA_WINDOW, min_periods=_VOL_MA_WINDOW).mean().values
        last_vol    = float(vol[-1])
        last_vol_ma = float(vol_ma[-1]) if not np.isnan(vol_ma[-1]) else 0.0

        if last_vol_ma <= 0:
            return 50.0, float('nan')

        ratio = last_vol / last_vol_ma

        if ratio < 0.5:
            score = 10.0
        elif ratio <= 1.0:
            score = 10.0 + 80.0 * (ratio - 0.5) / 0.5
        elif ratio <= 1.5:
            score = 90.0 + 10.0 * (ratio - 1.0) / 0.5
        elif ratio <= 3.0:
            # 放量过大区间：从100线性衰减到20
            score = 100.0 - 80.0 * (ratio - 1.5) / 1.5
        else:
            # 极端放量（天量）：恐慌/狂热信号，继续衰减到最低10
            decay = min((ratio - 3.0) / 2.0, 1.0)
            score = 20.0 - 10.0 * decay

        return float(np.clip(score, 0.0, 100.0)), ratio

    @staticmethod
    def _score_momentum(df: pd.DataFrame) -> tuple:
        """
        【私有】MF4：近期动量（5日涨跌幅）。

        sigmoid 映射，参数 k=30：
        - 涨幅 +5%  → 约82分
        - 涨幅  0%  → 50分
        - 跌幅 -5%  → 约18分
        覆盖指数正常波动区间（±5%）。
        """
        close = df['close'].values
        if len(close) <= _MOMENTUM_WINDOW:
            return 50.0, float('nan')

        ref_close = float(close[-_MOMENTUM_WINDOW - 1])
        if ref_close <= 0:
            return 50.0, float('nan')

        momentum = (float(close[-1]) - ref_close) / ref_close
        k = 30.0
        exp_arg = np.clip(-k * momentum, -709, 709)
        score = 100.0 / (1.0 + np.exp(exp_arg))
        return float(np.clip(score, 0.0, 100.0)), momentum

    @staticmethod
    def _score_volatility(df: pd.DataFrame) -> tuple:
        """
        【私有】MF5：波动率压制（True Range ATR 比率的倒数）。

        核心逻辑：
        使用 True Range 而非简单 H-L，能正确捕捉跳空缺口带来的波动：
          TR = max(H-L, |H-prev_close|, |L-prev_close|)
        A股指数频繁出现跳空低开/高开，仅用 H-L 会系统性低估波动率。

        ATR ratio = ATR(20) / MA(20)，衡量相对波动率：
        - 波动率低（市场稳定）→ ATR ratio 小 → 得分高
        - 波动率高（市场恐慌）→ ATR ratio 大 → 得分低

        映射逻辑（折线）：
        atr_ratio <= 0.01  → 90（极低波动，稳定市场）
        0.01~0.03          → 90 → 50（线性衰减，正常波动区间）
        0.03~0.06          → 50 → 10（快速衰减，高波动区间）
        > 0.06             → 10（极端波动）
        """
        high  = df['high'].values
        low   = df['low'].values
        close = df['close'].values

        # True Range：考虑跳空缺口
        # TR[i] = max(H[i]-L[i], |H[i]-C[i-1]|, |L[i]-C[i-1]|)
        # 第一根K线无前日收盘，用 H-L 替代
        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]  # 第一根用自身收盘替代，TR = H-L

        tr = np.maximum(
            high - low,
            np.maximum(np.abs(high - prev_close), np.abs(low - prev_close))
        )

        atr_series = pd.Series(tr).rolling(_ATR_WINDOW, min_periods=_ATR_WINDOW).mean()
        ma_series = pd.Series(close).rolling(_MA_WINDOW, min_periods=_MA_WINDOW).mean()

        atr = float(atr_series.iloc[-1])
        ma  = float(ma_series.iloc[-1])

        if ma <= 0 or np.isnan(atr) or np.isnan(ma):
            return 50.0, float('nan')

        atr_ratio = atr / ma

        if atr_ratio <= 0.01:
            score = 90.0
        elif atr_ratio <= 0.03:
            score = 90.0 - 40.0 * (atr_ratio - 0.01) / 0.02
        elif atr_ratio <= 0.06:
            score = 50.0 - 40.0 * (atr_ratio - 0.03) / 0.03
        else:
            score = 10.0

        return float(np.clip(score, 0.0, 100.0)), atr_ratio

    @staticmethod
    def _neutral_result(index_name: str, reason: str = '') -> Dict:
        """【私有】数据不足时返回黄色中性结果，允许开仓但缩半仓"""
        logger.warning(f"⚠️ [{index_name}] 市场数据缺失 reason={reason}，返回中性评估")
        return {
            'market_score':   50.0,
            'market_grade':   'yellow',
            'position_scale': _SCALE_YELLOW,
            'allow_new_open': True,
            'mf1_trend':      50.0,
            'mf2_boll':       50.0,
            'mf3_volume':     50.0,
            'mf4_momentum':   50.0,
            'mf5_volatility': 50.0,
            'detail': {'reason': reason},
        }
