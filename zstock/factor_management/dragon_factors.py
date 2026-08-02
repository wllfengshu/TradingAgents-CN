"""
龙头层因子计算模块（M3）

黑盒设计：
- 公开接口：calculate_all_dragon_factors_in_sector() / *_raw() / scores_from_raw()
- 所有实现细节都隐藏在私有方法中
- 职责：只计算单个板块内的M3龙头因子（5个因子的合成）
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from zstock.common.utils.common_utils import (
    ensure_ohlcv_sorted,
    limit_up_threshold,
    ohlcv_asof,
)

logger = logging.getLogger(__name__)

# 龙头因子权重：连板高度占主导；F3.5 布林趋势为软因子（绝对分，不再二次 min-max）
_W_F31 = 0.18   # F3.1 超额收益
_W_F32 = 0.12   # F3.2 成交额（打分取反：反拥挤，低额相对高分）
_W_F33 = 0.40   # F3.3 高度（连板天数）
_W_F34 = 0.18   # F3.4 量价共振度
_W_F35 = 0.12   # F3.5 布林趋势（0-100 绝对分）

# 测评 RankIC 显著为负：打分侧对 f32 取反后再 min-max
_INVERT_F32_FOR_SCORE = True

# 预计算多窗口（网格搜索用）；默认窗口与线上打分一致
_RETURN_WINDOWS = (5, 10, 15, 20)
_RESONANCE_WINDOWS = (3, 5, 10)
_DEFAULT_RETURN_WINDOW = 5
_DEFAULT_RESONANCE_WINDOW = 5


class DragonFactors:
    """龙头层因子计算器（M3）。黑盒设计，只负责龙头因子计算"""

    # ===================== 公开接口 =====================

    @staticmethod
    def precompute_stock_features(
        stock_ohlcv: Dict[str, pd.DataFrame],
        codes: List[str],
        assume_sorted: bool = False,
        trade_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        【公开接口】一次性计算个股级 M3 特征（与板块无关）。

        预计算路径应先对本日全部候选股票调用本方法，再按板块
        assemble_sector_raw_from_features，避免每个板块重复扫 OHLCV。

        trade_date 提供时要求末行恰好为该日，停牌/缺日股票不计入当日截面。
        """
        if not assume_sorted:
            subset = {
                c: stock_ohlcv[c]
                for c in codes
                if c in (stock_ohlcv or {})
            }
            stock_ohlcv = DragonFactors._sorted_ohlcv_map(subset)
        else:
            stock_ohlcv = {
                c: stock_ohlcv[c]
                for c in codes
                if c in (stock_ohlcv or {})
            }
        if trade_date:
            stock_ohlcv = {
                c: adf
                for c, df in stock_ohlcv.items()
                if (adf := ohlcv_asof(df, trade_date, require_exact=True)) is not None
            }
            codes = [c for c in codes if c in stock_ohlcv]

        volumes = DragonFactors._compute_daily_volumes(stock_ohlcv, codes)
        boards = DragonFactors._compute_consecutive_boards(stock_ohlcv, codes)
        boll_trend, boll_pass = DragonFactors._compute_bollinger_pair(
            stock_ohlcv, codes
        )

        returns_by_window = {
            w: DragonFactors._compute_nd_returns(stock_ohlcv, codes, w)
            for w in _RETURN_WINDOWS
        }
        # 共振多窗口：一次扫最大窗口，再切分子窗口
        resonance_by_window = DragonFactors._calculate_volume_price_resonance_multi(
            codes, stock_ohlcv, windows=_RESONANCE_WINDOWS
        )
        return {
            "volumes": volumes,
            "boards": boards,
            "boll_trend": boll_trend,
            "boll_pass": boll_pass,
            "returns_by_window": returns_by_window,
            "resonance_by_window": resonance_by_window,
        }

    @staticmethod
    def assemble_sector_raw_from_features(
        sector_stocks: List[str],
        features: Dict[str, Any],
    ) -> Dict[str, Dict[str, Any]]:
        """【公开接口】用预计算个股特征组装单板块 M3 raw（仅做板块相对超额收益）。"""
        volumes: Dict[str, float] = features["volumes"]
        boards: Dict[str, int] = features["boards"]
        boll_trend: Dict[str, float] = features["boll_trend"]
        boll_pass: Dict[str, float] = features["boll_pass"]
        returns_by_window: Dict[int, Dict[str, float]] = features["returns_by_window"]
        resonance_by_window: Dict[int, Dict[str, float]] = features[
            "resonance_by_window"
        ]

        sector_set = set(sector_stocks)
        f32_raw = {
            s: volumes[s] for s in sector_stocks if s in volumes
        }
        f33_raw = {s: float(boards[s]) for s in sector_stocks if s in boards}
        f35_raw = {s: boll_trend[s] for s in sector_stocks if s in boll_trend}
        f35_pass = {s: boll_pass.get(s, 0.0) for s in sector_stocks if s in boll_pass}

        f31_by_window: Dict[int, Dict[str, float]] = {}
        for w, returns in returns_by_window.items():
            sector_returns = {s: returns[s] for s in sector_stocks if s in returns}
            f31_by_window[w] = DragonFactors._calculate_leading_performance_raw(
                sector_returns
            )

        f34_by_window: Dict[int, Dict[str, float]] = {
            w: {s: mp[s] for s in sector_stocks if s in mp}
            for w, mp in resonance_by_window.items()
        }

        all_codes: set = set(f32_raw) | set(f33_raw) | set(f35_raw) | set(f35_pass)
        for d in f31_by_window.values():
            all_codes |= set(d)
        for d in f34_by_window.values():
            all_codes |= set(d)
        # 仅保留本板块股票（features 可能是全市场）
        all_codes &= sector_set

        result: Dict[str, Dict[str, Any]] = {}
        for code in all_codes:
            row: Dict[str, Any] = {
                "f32_amount": f32_raw.get(code, float("nan")),
                "f33_consecutive_boards": int(boards.get(code, 0)),
                "f35_bollinger_trend": f35_raw.get(code, float("nan")),
                "f35_bollinger_pass": float(f35_pass.get(code, 0.0)),
            }
            for w, mp in f31_by_window.items():
                row[f"f31_excess_return_{w}d"] = mp.get(code, float("nan"))
            for w, mp in f34_by_window.items():
                row[f"f34_resonance_pct_{w}d"] = mp.get(code, float("nan"))
            row["f31_excess_return"] = row[
                f"f31_excess_return_{_DEFAULT_RETURN_WINDOW}d"
            ]
            row["f34_resonance_pct"] = row[
                f"f34_resonance_pct_{_DEFAULT_RESONANCE_WINDOW}d"
            ]
            result[code] = row

        logger.debug(f"✅ M3 原始因子收集完成: {len(result)} 只")
        return result

    @staticmethod
    def calculate_all_dragon_factors_in_sector_raw(
        sector_stocks: List[str],
        stock_ohlcv: Dict[str, pd.DataFrame],
        assume_sorted: bool = False,
        features: Optional[Dict[str, Any]] = None,
        trade_date: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        【公开接口】返回 M3 子因子原始值（归一化前），供预计算存储。

        含多窗口变体便于网格搜索；无后缀字段为默认窗口别名。
        f35_bollinger_trend 已是 0-100 绝对分（打分时不再做板块内 min-max）。

        Args:
            assume_sorted: True 时跳过 OHLCV 排序（调用方已保证按 trade_date 升序）
            features: 可选，precompute_stock_features 的结果；传入则跳过个股特征重算
            trade_date: 截面日；features 为空时传给 precompute_stock_features
        """
        if features is None:
            features = DragonFactors.precompute_stock_features(
                stock_ohlcv,
                sector_stocks,
                assume_sorted=assume_sorted,
                trade_date=trade_date,
            )
        return DragonFactors.assemble_sector_raw_from_features(sector_stocks, features)

    @staticmethod
    def scores_from_raw(raw: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
        """从 raw 原始值打分（F3.1~F3.4 板块内 min-max；F3.5 保持绝对 0-100）。

        关键字段 F3.1/F3.2/F3.4 任一为 nan 的股票直接剔除，避免薄数据被中性 50 抬进候选。
        """
        if not raw:
            return {}

        usable = {}
        for c, r in raw.items():
            f31 = float(r.get("f31_excess_return", float("nan")))
            f32 = float(r.get("f32_amount", float("nan")))
            f34 = float(r.get("f34_resonance_pct", float("nan")))
            if f31 != f31 or f32 != f32 or f34 != f34:
                continue
            if f32 <= 0:
                continue
            usable[c] = r
        if not usable:
            return {}

        f31_raw = {c: float(r["f31_excess_return"]) for c, r in usable.items()}
        f32_raw = {c: float(r["f32_amount"]) for c, r in usable.items()}
        f33_raw = {
            c: float(r.get("f33_consecutive_boards", 0.0) or 0.0) for c, r in usable.items()
        }
        f34_raw = {c: float(r["f34_resonance_pct"]) for c, r in usable.items()}
        f35_raw = {
            c: float(r.get("f35_bollinger_trend", float("nan"))) for c, r in usable.items()
        }

        f35_norm = {
            c: 50.0 if (v != v) else float(np.clip(v, 0.0, 100.0))
            for c, v in f35_raw.items()
        }

        f32_for_score = (
            {c: -v for c, v in f32_raw.items()}
            if _INVERT_F32_FOR_SCORE
            else f32_raw
        )

        return DragonFactors._combine_five_factors(
            DragonFactors._minmax_normalize(f31_raw),
            DragonFactors._minmax_normalize(f32_for_score),
            DragonFactors._minmax_normalize(f33_raw),
            DragonFactors._minmax_normalize(f34_raw),
            f35_norm,
        )

    @staticmethod
    def calculate_all_dragon_factors_in_sector(
        sector_stocks: List[str],
        stock_ohlcv: Dict[str, pd.DataFrame],
        trade_date: Optional[str] = None,
    ) -> Dict[str, float]:
        """
        【公开接口】完整计算单个板块内M3龙头因子，返回0-100的龙头综合得分。

        内部先算 raw，再 scores_from_raw，避免重复计算。
        """
        raw = DragonFactors.calculate_all_dragon_factors_in_sector_raw(
            sector_stocks, stock_ohlcv, trade_date=trade_date
        )
        m3_scores = DragonFactors.scores_from_raw(raw)
        logger.info(f"✅ M3 龙头因子计算完成: {len(m3_scores)} 只")
        return m3_scores

    # ===================== 私有方法 =====================

    @staticmethod
    def _sorted_ohlcv_map(
        stock_ohlcv: Dict[str, pd.DataFrame],
    ) -> Dict[str, pd.DataFrame]:
        return {c: ensure_ohlcv_sorted(df) for c, df in (stock_ohlcv or {}).items()}

    @staticmethod
    def _calculate_leading_performance_raw(
        stock_returns: Dict[str, float],
    ) -> Dict[str, float]:
        """【私有】F3.1：超额收益（个股N日收益 - 板块中位数）"""
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

    @staticmethod
    def _compute_nd_returns(
        stock_ohlcv: Dict[str, pd.DataFrame],
        codes: List[str],
        window: int,
    ) -> Dict[str, float]:
        """【私有】从 OHLCV 计算 N 日收益率。"""
        need = window + 1
        result = {}
        for code in codes:
            df = stock_ohlcv.get(code)
            if df is None or df.empty or "close" not in df.columns or len(df) < need:
                continue
            closes = df["close"].to_numpy(dtype=float, copy=False)
            base = closes[-need]
            if base > 0:
                result[code] = (closes[-1] - base) / base
        return result

    @staticmethod
    def _compute_5d_returns(
        stock_ohlcv: Dict[str, pd.DataFrame], codes: List[str]
    ) -> Dict[str, float]:
        """【私有】兼容旧调用：等价于 5 日收益。"""
        return DragonFactors._compute_nd_returns(stock_ohlcv, codes, 5)

    @staticmethod
    def _compute_daily_volumes(
        stock_ohlcv: Dict[str, pd.DataFrame], codes: List[str]
    ) -> Dict[str, float]:
        """【私有】从 OHLCV 提取当日成交额（amount 末行）"""
        result = {}
        for code in codes:
            df = stock_ohlcv.get(code)
            if df is None or df.empty:
                continue
            if "amount" in df.columns:
                result[code] = float(df["amount"].iloc[-1])
            elif "volume" in df.columns:
                result[code] = float(df["volume"].iloc[-1])
        return result

    @staticmethod
    def _compute_consecutive_boards(
        stock_ohlcv: Dict[str, pd.DataFrame], codes: List[str]
    ) -> Dict[str, int]:
        """【私有】从 OHLCV 计算最近连续涨停天数（按板块阈值）。"""
        result = {}
        for code in codes:
            df = stock_ohlcv.get(code)
            if df is None or len(df) < 2 or "close" not in df.columns:
                continue
            closes = df["close"].to_numpy(dtype=float, copy=False)
            thr = limit_up_threshold(code)
            count = 0
            for i in range(len(closes) - 1, 0, -1):
                prev = closes[i - 1]
                if prev <= 0:
                    break
                if closes[i] / prev - 1.0 >= thr:
                    count += 1
                else:
                    break
            result[code] = count
        return result

    @staticmethod
    def _compute_bollinger_pair(
        stock_ohlcv: Dict[str, pd.DataFrame],
        codes: List[str],
        window: int = 20,
        slope_window: int = 5,
        slope_threshold: float = 0.0,
    ) -> tuple:
        """【私有】一次算布林中轨，同时产出 F3.5 趋势分与硬过滤 pass。"""
        trend: Dict[str, float] = {}
        passed: Dict[str, float] = {}
        min_bars = window + slope_window
        x = np.arange(slope_window, dtype=float)
        x_mean = x.mean()
        x_var = float(((x - x_mean) ** 2).sum()) or 1.0

        for code in codes:
            df = stock_ohlcv.get(code)
            if df is None or len(df) < min_bars or "close" not in df.columns:
                passed[code] = 0.0
                continue

            closes = df["close"].to_numpy(dtype=float, copy=False)
            # 仅需末尾 window+slope_window 根即可
            use = closes[-(window + slope_window) :]
            # 滚动均线：对 use 逐点算 window 均值（长度=slope_window+1 个有效 mid）
            csum = np.cumsum(use)
            # mid[i] 对应 use[i-window+1:i+1]，i 从 window-1 开始
            mids = []
            for i in range(window - 1, len(use)):
                prev = csum[i - window] if i >= window else 0.0
                mids.append((csum[i] - prev) / window)
            mid = np.asarray(mids, dtype=float)
            if len(mid) < slope_window:
                passed[code] = 0.0
                continue

            recent_mid = mid[-slope_window:]
            # 线性斜率（等价 polyfit 一阶，避免每次 polyfit 开销）
            y_mean = recent_mid.mean()
            slope = float(((x - x_mean) * (recent_mid - y_mean)).sum() / x_var)

            current_close = float(closes[-1])
            current_mid = float(mid[-1])
            if current_mid <= 0:
                passed[code] = 0.0
                continue

            position_ratio = np.clip(
                (current_close - current_mid) / current_mid, -0.1, 0.1
            )
            position_score = np.clip(30 + position_ratio * 300, 0, 60)
            slope_normalized = np.clip(slope / current_mid, -0.005, 0.005)
            slope_score = np.clip(20 + slope_normalized * 4000, 0, 40)
            trend[code] = float(position_score + slope_score)

            if len(mid) <= slope_window:
                passed[code] = 0.0
                continue
            mid_prev = float(mid[-1 - slope_window])
            if mid_prev <= 0 or mid_prev != mid_prev:
                passed[code] = 0.0
                continue
            rel_slope = (current_mid - mid_prev) / mid_prev
            passed[code] = (
                1.0
                if (current_close > current_mid and rel_slope > slope_threshold)
                else 0.0
            )
        return trend, passed

    @staticmethod
    def _compute_bollinger_trend(
        stock_ohlcv: Dict[str, pd.DataFrame],
        codes: List[str],
        window: int = 20,
        std_dev: float = 2.0,
        slope_window: int = 5,
    ) -> Dict[str, float]:
        """【私有】F3.5：布林趋势得分（0-100 绝对分，仅研究明细）。"""
        del std_dev
        trend, _ = DragonFactors._compute_bollinger_pair(
            stock_ohlcv, codes, window=window, slope_window=slope_window
        )
        return trend

    @staticmethod
    def _compute_bollinger_pass(
        stock_ohlcv: Dict[str, pd.DataFrame],
        codes: List[str],
        window: int = 20,
        slope_window: int = 5,
        slope_threshold: float = 0.0,
    ) -> Dict[str, float]:
        """【私有】M3.3 布林上升硬过滤：close>mid 且中轨相对斜率>阈值 → 1/0。"""
        _, passed = DragonFactors._compute_bollinger_pair(
            stock_ohlcv,
            codes,
            window=window,
            slope_window=slope_window,
            slope_threshold=slope_threshold,
        )
        return passed

    @staticmethod
    def _calculate_volume_price_resonance_raw(
        sector_stocks: List[str],
        stock_ohlcv: Optional[Dict[str, pd.DataFrame]],
        window: int = 5,
    ) -> Dict[str, float]:
        """【私有】F3.4：量价共振度（单窗口）。"""
        multi = DragonFactors._calculate_volume_price_resonance_multi(
            sector_stocks, stock_ohlcv, windows=(window,)
        )
        return multi.get(window, {})

    @staticmethod
    def _calculate_volume_price_resonance_multi(
        sector_stocks: List[str],
        stock_ohlcv: Optional[Dict[str, pd.DataFrame]],
        windows: tuple = _RESONANCE_WINDOWS,
    ) -> Dict[int, Dict[str, float]]:
        """
        【私有】F3.4 多窗口量价共振：一次扫最大窗口，再按窗口切片。

        健康日：价涨量涨 / 价跌量缩 / 涨停日（阈值按板块）。
        """
        result: Dict[int, Dict[str, float]] = {w: {} for w in windows}
        if stock_ohlcv is None or not windows:
            return result
        max_w = max(windows)
        need = max_w + 1
        for code in sector_stocks:
            df = stock_ohlcv.get(code)
            if df is None or len(df) < need or "close" not in df.columns:
                continue
            close_arr = df["close"].to_numpy(dtype=float, copy=False)[-need:]
            if "amount" in df.columns:
                vol_arr = df["amount"].to_numpy(dtype=float, copy=False)[-need:]
            elif "volume" in df.columns:
                vol_arr = df["volume"].to_numpy(dtype=float, copy=False)[-need:]
            else:
                continue

            price_chg = np.diff(close_arr)
            vol_chg = np.diff(vol_arr)
            prev_close = close_arr[:-1]
            daily_return = np.where(prev_close > 0, price_chg / prev_close, 0.0)
            thr = limit_up_threshold(code)
            is_sync = ((price_chg > 0) & (vol_chg > 0)) | (
                (price_chg < 0) & (vol_chg < 0)
            )
            healthy = is_sync | (daily_return >= thr)
            for w in windows:
                if len(healthy) < w:
                    continue
                result[w][code] = float(healthy[-w:].sum() / w)
        return result

    @staticmethod
    def _minmax_normalize(values_dict: Dict[str, float]) -> Dict[str, float]:
        """【私有】min-max归一化转0-100（跳过 nan）。"""
        clean = {
            k: float(v)
            for k, v in values_dict.items()
            if v is not None and v == v
        }
        if not clean:
            return {}
        min_val = min(clean.values())
        max_val = max(clean.values())
        if max_val == min_val:
            return {k: 50.0 for k in clean}
        return {k: 100 * (v - min_val) / (max_val - min_val) for k, v in clean.items()}

    @staticmethod
    def _combine_five_factors(
        f31: Dict[str, float],
        f32: Dict[str, float],
        f33: Dict[str, float],
        f34: Dict[str, float],
        f35: Dict[str, float],
    ) -> Dict[str, float]:
        """【私有】加权合成 5 个因子为 M3 得分（缺失因子按中性值 50 补齐）。"""
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
