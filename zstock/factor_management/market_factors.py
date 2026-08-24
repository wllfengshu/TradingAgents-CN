"""
市场因子模块（M1）

职责：通过大盘指数的多维特征评估当日市场情绪，
      输出市场风险等级，决定策略是否允许开新仓/加仓等。

黑盒设计：
- 公开接口：calculate_market_sentiment() 计算
- 公开接口：score_from_raw() 打分
- 所有实现细节都隐藏在私有方法中

五个子因子（均以沪深300指数为主锚）：
  market_trend_strength（30%）：市场趋势强度 - 20日均线斜率，衡量中期趋势方向
  market_bollinger_position（25%）：市场布林位置 - 价格在布林带内的相对位置，衡量超买/超卖
  market_volume_state（20%）：市场成交量状态 - 当日成交量相对20日均量，判断缩量/放量
  market_5day_momentum（15%）：市场5日动量 - 5日涨跌幅，快速反映短期趋势
  market_volatility_suppression（10%）：市场波动率抑制 - ATR比率的倒数，低波动=市场稳定

风险等级（market_risk_level）：
  green  [70, 100]：市场强势，正常开仓
  yellow [40,  70)：市场震荡，减仓系数 0.5（减半仓位）
  red    [ 0,  40)：市场弱势，不开新仓，不加仓

使用示例：
    sentiment = MarketFactors.calculate_market_sentiment(index_ohlcv_df)
    # sentiment = {
    #     'market_composite_score': 72.5,
    #     'market_risk_level': 'green',
    #     'position_scale_factor': 1.0,
    #     'market_trend_strength': 68.0, 'market_bollinger_position': 75.0, ...
    # }
"""

import logging
from typing import Dict, Optional

import numpy as np
import pandas as pd

from zstock.common.utils.common_utils import ensure_ohlcv_sorted, ohlcv_asof

logger = logging.getLogger(__name__)

# 子因子权重
_W_TREND = 0.30        # market_trend_strength（市场趋势强度）
_W_BOLLINGER = 0.25    # market_bollinger_position（市场布林位置）
_W_VOLUME = 0.20       # market_volume_state（市场成交量状态）
_W_MOMENTUM = 0.15     # market_5day_momentum（市场5日动量）
_W_VOLATILITY = 0.10   # market_volatility_suppression（市场波动率抑制）

# 风险等级阈值
_GRADE_GREEN  = 70.0
_GRADE_YELLOW = 40.0

# 各等级对应仓位缩放系数
_SCALE_GREEN  = 1.0
_SCALE_YELLOW = 0.4   # P1：黄灯更保守，压回撤
_SCALE_RED    = 0.0

# 技术参数 - 单窗口（默认用于最终打分）
_MA_WINDOW           = 20
_BOLL_STD            = 2.0
_SLOPE_WINDOW        = 5    # 用近 N 根均线估算斜率
_MOMENTUM_WINDOW     = 5
_ATR_WINDOW          = 20
_VOL_MA_WINDOW       = 20

# Sigmoid 映射参数（用于 slope 和 momentum）
# k=800: slope_pct 变化 0.1% 时从 ~25 分涨到 ~75 分（灵敏度适中）
# k=30: momentum 变化 5% 时从 ~18 分涨到 ~82 分（快速反应）
_SIGMOID_K_SLOPE     = 800.0
_SIGMOID_K_MOMENTUM  = 30.0

# ATR 折线映射分段点（wave volatility suppression）
# 根据 backtest 结果优化，衡量市场波动率相对于中期均线的比例
# 0.01: 极低波动（市场稳定）→ 90分
# 0.03: 正常波动区间（50分）
# 0.06: 高波动区间（10分）
# >0.06: 极端波动（10分）
_ATR_RATIO_SEGMENTS  = [0.01, 0.03, 0.06]
_ATR_RATIO_SCORES    = [90.0, 50.0, 10.0, 10.0]

# 多窗口配置（网格搜索用）- 每个因子独立窗口
_TREND_MA_WINDOWS    = (5, 10, 20)           # MF1 趋势强度：MA窗口
_BOLL_MA_WINDOWS     = (10, 20, 30)          # MF2 布林位置：MA窗口
_VOL_MA_WINDOWS      = (5, 10, 20)           # MF3 成交量：MA窗口
_MOMENTUM_WINDOWS    = (3, 5, 10)            # MF4 动量：日期窗口
_ATR_WINDOWS         = (10, 20, 30)          # MF5 波动率：ATR窗口
_MA_WINDOWS_FOR_ATR  = (10, 20, 30)          # MF5 波动率：MA窗口（与ATR配对）

_MIN_BARS            = 31   # 至少需要的 K 线根数（最大MA30 + 1根计算 True Range）


class MarketFactors:
    """市场因子计算器（M1）。黑盒设计，只负责市场情绪评估"""

    # ===================== 公开接口=====================

    @staticmethod
    def calculate_market_sentiment(
        index_ohlcv: pd.DataFrame,
        index_name: str = '沪深300',
        trade_date: str = None,
    ) -> Dict:
        """
        【公开接口】根据指数 OHLCV 计算市场情绪得分与风险等级。

        Args:
            index_ohlcv: 指数日线 DataFrame，必须含列：
                         close, high, low, volume（按日期升序）
            index_name:  指数名称，仅用于日志标识
            trade_date: 可选，指定交易日（YYYY-MM-DD），若不指定则使用最新日期

        Returns:
            {
                'market_composite_score': float,        # 市场综合得分 0~100
                'market_risk_level':      str,          # 'green'(强势) / 'yellow'(震荡) / 'red'(弱势)
                'position_scale_factor':  float,        # 仓位缩放因子 0.0 / 0.5 / 1.0
                'allow_new_open':         bool,         # 是否允许开新仓
                'market_trend_strength':      float,    # 市场趋势强度得分 0~100
                'market_bollinger_position':  float,    # 市场布林位置得分 0~100
                'market_volume_state':       float,     # 市场成交量状态得分 0~100
                'market_5day_momentum':      float,     # 市场5日动量得分 0~100
                'market_volatility_suppression': float, # 市场波动率抑制得分 0~100
                'detail':                 dict,         # 各因子原始值，便于调试
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

        df = ensure_ohlcv_sorted(index_ohlcv.copy()).reset_index(drop=True)
        if trade_date:
            asof = ohlcv_asof(df, trade_date, require_exact=True)
            if asof is None or len(asof) < _MIN_BARS:
                logger.warning(
                    f"⚠️ [{index_name}] trade_date={trade_date} 无当日指数 bar 或数据不足，"
                    f"返回中性评估"
                )
                return MarketFactors._neutral_result(
                    index_name, reason="asof_mismatch_or_insufficient"
                )
            df = asof.reset_index(drop=True)
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
            _W_TREND * mf1
            + _W_BOLLINGER * mf2
            + _W_VOLUME * mf3
            + _W_MOMENTUM * mf4
            + _W_VOLATILITY * mf5
        )
        score = float(np.clip(score, 0.0, 100.0))

        if score >= _GRADE_GREEN:
            grade = "green"
            scale = _SCALE_GREEN
        elif score >= _GRADE_YELLOW:
            grade = "yellow"
            scale = _SCALE_YELLOW
        else:
            grade = "red"
            scale = _SCALE_RED

        allow_new_open = grade != "red"

        logger.debug(
            f"📊 [{index_name}] 市场得分={score:.1f} 等级={grade} "
            f"MF1={mf1:.0f} MF2={mf2:.0f} MF3={mf3:.0f} MF4={mf4:.0f} MF5={mf5:.0f}"
        )

        # mf5_atr_ratio_inv = 1/ATR比率（波动越低该值越大）；打分仍用 mf5_atr_ratio
        atr_inv = (
            (1.0 / mf5_raw)
            if (mf5_raw is not None and mf5_raw == mf5_raw and mf5_raw > 0)
            else float("nan")
        )
        detail = {
            "mf1_slope_pct": mf1_raw,
            "mf2_boll_pct": mf2_raw,
            "mf3_vol_ratio": mf3_raw,
            "mf4_momentum_5d": mf4_raw,
            "mf5_atr_ratio": mf5_raw,
            "mf5_atr_ratio_inv": atr_inv,
        }

        # 多窗口原始值（网格搜索用）
        # MF1 趋势强度多窗口
        for w in _TREND_MA_WINDOWS:
            if w != _MA_WINDOW:
                _, raw_w = MarketFactors._score_trend_strength(df, ma_window=w)
                detail[f"mf1_slope_pct_{w}d"] = raw_w

        # MF2 布林位置多窗口
        for w in _BOLL_MA_WINDOWS:
            if w != _MA_WINDOW:
                _, raw_w = MarketFactors._score_boll_position(df, ma_window=w)
                detail[f"mf2_boll_pct_{w}d"] = raw_w

        # MF3 成交量多窗口
        for w in _VOL_MA_WINDOWS:
            if w != _VOL_MA_WINDOW:
                _, raw_w = MarketFactors._score_volume_state(df, vol_ma_window=w)
                detail[f"mf3_vol_ratio_{w}d"] = raw_w

        # MF4 动量多窗口（所有窗口，包括默认）
        for w in _MOMENTUM_WINDOWS:
            _, raw_w = MarketFactors._score_momentum(df, window=w)
            detail[f"mf4_momentum_{w}d"] = raw_w

        # MF5 波动率多窗口（ATR 与 MA 同窗口配对，后续可扩展为交叉组合）
        for atr_w, ma_w in zip(_ATR_WINDOWS, _MA_WINDOWS_FOR_ATR):
            if atr_w != _ATR_WINDOW or ma_w != _MA_WINDOW:
                _, raw_w = MarketFactors._score_volatility(df, atr_window=atr_w, ma_window=ma_w)
                detail[f"mf5_atr_ratio_{atr_w}d_{ma_w}d"] = raw_w

        return {
            "market_composite_score": score,
            "market_risk_level": grade,
            "position_scale_factor": scale,
            "allow_new_open": allow_new_open,
            "market_trend_strength": mf1,
            "market_bollinger_position": mf2,
            "market_volume_state": mf3,
            "market_5day_momentum": mf4,
            "market_volatility_suppression": mf5,
            "detail": detail,
        }

    @staticmethod
    def score_from_raw(
        mf1_slope_pct: float,
        mf2_boll_pct: float,
        mf3_vol_ratio: float,
        mf4_momentum_5d: float,
        mf5_atr_ratio: float,
    ) -> Dict:
        """
        【公开接口】从 M1 5个子因子原始值直接打分。

        用于 pipeline.score_signals 从预计算原始值重建市场情绪结果。
        映射逻辑与 calculate_market_sentiment() 完全一致（Single Source of Truth）。

        Args:
            mf1_slope_pct:   20日MA 5日百分比斜率，范围 (-inf, +inf)，通常 [-0.01, 0.01]
                             负值=下降趋势，正值=上升趋势
            mf2_boll_pct:    布林带位置百分比，范围 [0.0, 1.0]
                             0.0=下轨(超卖)，0.5=中轨，1.0=上轨(超买)
            mf3_vol_ratio:   当日成交量 / 20日均量，范围 [0, +inf)，通常 [0.2, 3.0]
                             <1=缩量，=1=平量，>1=放量
            mf4_momentum_5d: 5日涨跌幅百分比，范围 (-inf, +inf)，通常 [-0.05, 0.05]
                             负值=下跌，正值=上涨
            mf5_atr_ratio:   ATR(20) / MA(20)，范围 [0, +inf)，通常 [0.005, 0.1]
                             低=波动平稳，高=波动剧烈

        Returns:
            与 calculate_market_sentiment() 返回结构一致的 dict
        """
        mf1 = MarketFactors._map_slope_to_score(mf1_slope_pct) if not np.isnan(mf1_slope_pct) else 50.0
        mf2 = mf2_boll_pct * 100.0 if not np.isnan(mf2_boll_pct) else 50.0
        mf3 = MarketFactors._map_vol_ratio_to_score(mf3_vol_ratio) if not np.isnan(mf3_vol_ratio) else 50.0
        mf4 = MarketFactors._map_momentum_to_score(mf4_momentum_5d) if not np.isnan(mf4_momentum_5d) else 50.0
        mf5 = MarketFactors._map_atr_ratio_to_score(mf5_atr_ratio) if not np.isnan(mf5_atr_ratio) else 50.0

        score = float(np.clip(
            _W_TREND * mf1 + _W_BOLLINGER * mf2 + _W_VOLUME * mf3 + _W_MOMENTUM * mf4 + _W_VOLATILITY * mf5,
            0.0, 100.0,
        ))

        if score >= _GRADE_GREEN:
            grade, scale = 'green', _SCALE_GREEN
        elif score >= _GRADE_YELLOW:
            grade, scale = 'yellow', _SCALE_YELLOW
        else:
            grade, scale = 'red', _SCALE_RED

        return {
            'market_composite_score':   score,
            'market_risk_level':   grade,
            'position_scale_factor': scale,
            'allow_new_open': grade != 'red',
            'market_trend_strength':      mf1,
            'market_bollinger_position':       mf2,
            'market_volume_state':     mf3,
            'market_5day_momentum':   mf4,
            'market_volatility_suppression': mf5,
            'detail': {
                'mf1_slope_pct':      mf1_slope_pct,
                'mf2_boll_pct':       mf2_boll_pct,
                'mf3_vol_ratio':      mf3_vol_ratio,
                'mf4_momentum_5d':    mf4_momentum_5d,
                'mf5_atr_ratio':      mf5_atr_ratio,
                'mf5_atr_ratio_inv':  (
                    (1.0 / mf5_atr_ratio)
                    if (mf5_atr_ratio is not None
                        and not np.isnan(mf5_atr_ratio)
                        and mf5_atr_ratio > 0)
                    else float('nan')
                ),
            },
        }

    # ===================== 原始值映射函数（供 score_from_raw 和 _score_* 共用）=====================

    @staticmethod
    def _map_slope_to_score(slope_pct: float, k: float = None) -> float:
        """sigmoid 映射：百分比斜率 → 0~100 得分"""
        if k is None:
            k = _SIGMOID_K_SLOPE
        exp_arg = np.clip(-k * slope_pct, -709, 709)
        return float(np.clip(100.0 / (1.0 + np.exp(exp_arg)), 0.0, 100.0))

    @staticmethod
    def _map_vol_ratio_to_score(ratio: float) -> float:
        """折线映射：量比 → 0~100 得分"""
        if ratio < 0.5:
            score = 10.0
        elif ratio <= 1.0:
            score = 10.0 + 80.0 * (ratio - 0.5) / 0.5
        elif ratio <= 1.5:
            score = 90.0 + 10.0 * (ratio - 1.0) / 0.5
        elif ratio <= 3.0:
            score = 100.0 - 80.0 * (ratio - 1.5) / 1.5
        else:
            decay = min((ratio - 3.0) / 2.0, 1.0)
            score = 20.0 - 10.0 * decay
        return float(np.clip(score, 0.0, 100.0))

    @staticmethod
    def _map_momentum_to_score(momentum: float, k: float = None) -> float:
        """sigmoid 映射：5日涨跌幅 → 0~100 得分"""
        if k is None:
            k = _SIGMOID_K_MOMENTUM
        exp_arg = np.clip(-k * momentum, -709, 709)
        return float(np.clip(100.0 / (1.0 + np.exp(exp_arg)), 0.0, 100.0))

    @staticmethod
    def _map_atr_ratio_to_score(atr_ratio: float) -> float:
        """折线映射：ATR/MA 比率 → 0~100 得分（低波动=高分）"""
        if atr_ratio <= _ATR_RATIO_SEGMENTS[0]:      # 0.01
            score = 90.0
        elif atr_ratio <= _ATR_RATIO_SEGMENTS[1]:    # 0.03
            score = 90.0 - 40.0 * (atr_ratio - _ATR_RATIO_SEGMENTS[0]) / (_ATR_RATIO_SEGMENTS[1] - _ATR_RATIO_SEGMENTS[0])
        elif atr_ratio <= _ATR_RATIO_SEGMENTS[2]:    # 0.06
            score = 50.0 - 40.0 * (atr_ratio - _ATR_RATIO_SEGMENTS[1]) / (_ATR_RATIO_SEGMENTS[2] - _ATR_RATIO_SEGMENTS[1])
        else:
            score = 10.0
        return float(np.clip(score, 0.0, 100.0))

    # ===================== 私有方法（实现细节，对外隐藏）=====================

    @staticmethod
    def _score_trend_strength(df: pd.DataFrame, ma_window: int = None) -> tuple:
        """
        【私有】MF1：MA趋势强度（支持多窗口）。

        计算方式：
          1. 计算 ma_window 日 MA（默认20）
          2. 用最近 _SLOPE_WINDOW 根均线做线性回归，得斜率
          3. 将斜率除以最新均线值，得"日均涨跌幅"（百分比斜率）
          4. sigmoid 映射到 0~100
        """
        if ma_window is None:
            ma_window = _MA_WINDOW

        close = df['close'].values
        ma = pd.Series(close).rolling(ma_window, min_periods=ma_window).mean().values

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
        score = MarketFactors._map_slope_to_score(slope_pct)
        return score, slope_pct

    @staticmethod
    def _score_boll_position(df: pd.DataFrame, ma_window: int = None) -> tuple:
        """
        【私有】MF2：布林带位置（支持多窗口）。

        计算当前收盘价在布林带内的相对位置（0~1），线性映射到 0~100。
        """
        if ma_window is None:
            ma_window = _MA_WINDOW

        close = df['close'].values
        ma    = pd.Series(close).rolling(ma_window, min_periods=ma_window).mean()
        std   = pd.Series(close).rolling(ma_window, min_periods=ma_window).std()
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
    def _score_volume_state(df: pd.DataFrame, vol_ma_window: int = None) -> tuple:
        """
        【私有】MF3：量能状态（支持多窗口）。

        计算当日成交量与 vol_ma_window 日均量的比值。
        """
        if vol_ma_window is None:
            vol_ma_window = _VOL_MA_WINDOW

        vol    = df['volume'].values
        vol_ma = pd.Series(vol).rolling(vol_ma_window, min_periods=vol_ma_window).mean().values
        last_vol    = float(vol[-1])
        last_vol_ma = float(vol_ma[-1]) if not np.isnan(vol_ma[-1]) else 0.0

        if last_vol_ma <= 0 or np.isnan(last_vol):
            return 50.0, float('nan')

        ratio = last_vol / last_vol_ma
        score = MarketFactors._map_vol_ratio_to_score(ratio)
        return score, ratio

    @staticmethod
    def _score_momentum(df: pd.DataFrame, window: int = None) -> tuple:
        """
        【私有】MF4：近期动量（N 日涨跌幅，默认 5）。

        sigmoid 映射，参数 k=30：
        - 涨幅 +5%  → 约82分
        - 涨幅  0%  → 50分
        - 跌幅 -5%  → 约18分
        """
        w = _MOMENTUM_WINDOW if window is None else window
        close = df["close"].values
        if len(close) < w + 1:
            return 50.0, float("nan")

        last_close = float(close[-1])
        if last_close <= 0 or np.isnan(last_close):
            return 50.0, float("nan")

        ref_close = float(close[-w - 1])
        if ref_close <= 0 or np.isnan(ref_close):
            return 50.0, float("nan")

        momentum = (last_close - ref_close) / ref_close
        score = MarketFactors._map_momentum_to_score(momentum)
        return score, momentum

    @staticmethod
    def _score_volatility(df: pd.DataFrame, atr_window: int = None, ma_window: int = None) -> tuple:
        """
        【私有】MF5：波动率压制（True Range ATR 比率的倒数，支持多窗口）。

        核心逻辑：
        使用 True Range 而非简单 H-L，能正确捕捉跳空缺口带来的波动：
          TR = max(H-L, |H-prev_close|, |L-prev_close|)
        A股指数频繁出现跳空低开/高开，仅用 H-L 会系统性低估波动率。

        ATR ratio = ATR(atr_window) / MA(ma_window)，衡量相对波动率：
        - 波动率低（市场稳定）→ ATR ratio 小 → 得分高
        - 波动率高（市场恐慌）→ ATR ratio 大 → 得分低

        映射逻辑（折线）：
        atr_ratio <= 0.01  → 90（极低波动，稳定市场）
        0.01~0.03          → 90 → 50（线性衰减，正常波动区间）
        0.03~0.06          → 50 → 10（快速衰减，高波动区间）
        > 0.06             → 10（极端波动）
        """
        if atr_window is None:
            atr_window = _ATR_WINDOW
        if ma_window is None:
            ma_window = _MA_WINDOW

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

        atr_series = pd.Series(tr).rolling(atr_window, min_periods=atr_window).mean()
        ma_series = pd.Series(close).rolling(ma_window, min_periods=ma_window).mean()

        atr = float(atr_series.iloc[-1])
        ma  = float(ma_series.iloc[-1])

        if np.isnan(atr) or np.isnan(ma) or ma <= 0 or atr < 0:
            return 50.0, float('nan')

        atr_ratio = atr / ma

        score = MarketFactors._map_atr_ratio_to_score(atr_ratio)
        return score, atr_ratio

    @staticmethod
    def _neutral_result(index_name: str, reason: str = '') -> Dict:
        """【私有】数据不足时返回黄色中性结果，允许开仓但缩半仓"""
        logger.warning(f"⚠️ [{index_name}] 市场数据缺失 reason={reason}，返回中性评估")
        return {
            'market_composite_score':   50.0,
            'market_risk_level':   'yellow',
            'position_scale_factor': _SCALE_YELLOW,
            'allow_new_open': True,
            'market_trend_strength':      50.0,
            'market_bollinger_position':       50.0,
            'market_volume_state':     50.0,
            'market_5day_momentum':   50.0,
            'market_volatility_suppression': 50.0,
            'detail': {'reason': reason},
        }
