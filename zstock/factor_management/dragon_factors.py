"""
龙头层因子计算模块（M3）

黑盒设计：
- 公开接口：calculate_all_dragon_factors_in_sector_raw() / scores_from_raw()
- 所有实现细节都隐藏在私有方法中
- 职责：只计算龙头因子（技术面 + 基本面 f39/f40）
- MongoDB 只存储原始因子值，不存储得分（得分由 scores_from_raw 实时计算）

因子权重/极性由 strategy_params.json active_factors.dragon 提供（单一事实来源），
按 regime 路由（momentum / reversal）。scores_from_raw 从配置动态提取因子并 min-max 加权。

φ 门槛（不进加权）：布林趋势 >= _F35_MIN_TREND 或连板 >= _F33_MIN_BOARDS

预计算多窗口（网格搜索用）；默认窗口与线上打分一致：
  _RETURN_WINDOWS (5,10,15,20) / _RESONANCE_WINDOWS (3,5,10)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from zstock.common.utils.common_utils import (
    ensure_ohlcv_sorted,
    limit_up_threshold,
    minmax_normalize,
    ohlcv_asof,
)

logger = logging.getLogger(__name__)

# φ 门槛（不进加权）
_F35_MIN_TREND = 20.0   # 布林趋势绝对分下限（从40降到20：原值过严，大量候选被误杀）
_F33_MIN_BOARDS = 1     # 连板基因；或与 f35_bollinger_pass 二选一

# 预计算多窗口（网格搜索用）；默认窗口与线上打分一致
_RETURN_WINDOWS = (5, 10, 15, 20)
_RESONANCE_WINDOWS = (3, 5, 10)
_DEFAULT_RETURN_WINDOW = 5
_DEFAULT_RESONANCE_WINDOW = 5

_fundamental_provider: Optional[Any] = None
_fundamental_provider_tried: bool = False


def _get_fundamental_provider() -> Optional[Any]:
    """懒加载基本面提供者（MongoDB PIT-safe BPS / 股东户数）。"""
    global _fundamental_provider, _fundamental_provider_tried
    if _fundamental_provider_tried:
        return _fundamental_provider
    _fundamental_provider_tried = True
    try:
        from zstock.factor_management.fundamental_factors import FundamentalDataProvider

        provider = FundamentalDataProvider()
        provider.load_from_mongodb()
        if provider.is_loaded:
            _fundamental_provider = provider
            logger.info(
                "✅ 基本面因子就绪: BPS=%d, HolderChange=%d",
                provider.codes_with_pb(),
                provider.codes_with_holder(),
            )
        else:
            logger.warning("⚠️ 基本面数据未加载，f39/f40 将为 NaN")
    except Exception as e:
        logger.warning("⚠️ 基本面因子加载失败，f39/f40 将为 NaN: %s", e)
    return _fundamental_provider


class DragonFactors:
    """龙头层因子计算器（M3）。黑盒设计，只负责龙头因子计算"""

    # ===================== 公开接口 =====================

    @staticmethod
    def calculate_all_dragon_factors_in_sector_raw(
        sector_stocks: List[str],
        stock_ohlcv: Dict[str, pd.DataFrame],
        assume_sorted: bool = False,
        features: Optional[Dict[str, Any]] = None,
        trade_date: Optional[str] = None,
        fund_provider: Optional[Any] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        【公开接口1】计算单个板块的龙头因子原始值。

        供 precompute_factors.py 调用，逐板块计算并存储原始因子值到 MongoDB。

        Args:
            sector_stocks: 单个板块的股票列表 [code1, code2, ...]
            stock_ohlcv: {code: DataFrame} 个股 OHLCV 数据
            assume_sorted: True 时跳过 OHLCV 排序
            features: 可选，_precompute_stock_features 的结果（用于性能优化）
            trade_date: 截面日

        Returns:
            {code: {f31b_..., f32_..., f34_..., f35_..., f39_pb, f40_holder_change, ...}}
        """
        if features is None:
            features = DragonFactors._precompute_stock_features(
                stock_ohlcv,
                sector_stocks,
                assume_sorted=assume_sorted,
                trade_date=trade_date,
            )
        return DragonFactors._assemble_sector_raw_from_features(
            sector_stocks,
            features,
            trade_date=trade_date,
            fund_provider=fund_provider,
        )

    @staticmethod
    def scores_from_raw(raw: Dict[str, Dict[str, Any]], regime: str = "neutral", active_factors: dict = None, scoring_method: str = "linear") -> Dict[str, float]:
        """从 raw 原始值打分（配置驱动 + 决策树）。

        配置驱动：从 active_factors.dragon 按 regime 动态提取配置中声明的所有因子。
        scoring_method: "linear"（默认，加权求和）或 "tree"（决策树分箱）。

        Note: 接受规范的 MongoDB 字段名（带窗口后缀如 f31_excess_return_5d）
        """
        if not raw:
            return {}

        if not active_factors:
            logger.warning("⚠️ 龙头打分未收到 active_factors 配置，返回空")
            return {}

        dragon_cfg = active_factors.get("dragon", {})
        factor_configs = dragon_cfg.get(regime, dragon_cfg.get("momentum", []))
        if not factor_configs:
            return {}

        # 从配置中收集所有需要的因子字段
        config_fields = [fc["field"] for fc in factor_configs]

        # 动态提取：遍历 raw，提取所有配置字段
        field_values = {f: {} for f in config_fields}
        valid_stocks = set()

        for c, r in raw.items():
            if not DragonFactors._passes_dragon_filters(r):
                continue
            # 至少需要一个有效因子值
            has_any = False
            for field in config_fields:
                val = r.get(field)
                if val is None:
                    # 兼容 f34_resonance_pct_5d → f34_resonance_pct 的别名
                    if field == "f34_resonance_pct_5d":
                        val = r.get("f34_resonance_pct")
                if val is not None:
                    fval = float(val)
                    if fval == fval:  # not NaN
                        field_values[field][c] = fval
                        has_any = True
            if has_any:
                valid_stocks.add(c)

        if not valid_stocks:
            return {}

        # ── 决策树路径 ──
        if scoring_method == "tree":
            from zstock.factor_management.tree_scorer import get_tree_scorer
            try:
                scorer = get_tree_scorer()
                return scorer.score(field_values)
            except FileNotFoundError:
                logger.warning("决策树模型未找到，降级为线性加权")
                # fall through to linear path

        return DragonFactors._combine_from_config_dragon(field_values, factor_configs)

    @staticmethod
    def _combine_from_config_dragon(
        field_values: Dict[str, Dict[str, float]],
        factor_configs: list,
    ) -> Dict[str, float]:
        """【通用】配置驱动的龙头因子组合。"""
        normalized = {}
        for fc in factor_configs:
            field = fc["field"]
            values = field_values.get(field, {})
            if not values:
                continue
            norm = minmax_normalize(values)
            if fc.get("polarity") == "negative":
                norm = {k: 100 - v for k, v in norm.items()}
            normalized[field] = (norm, fc["weight"])

        if not normalized:
            return {}

        # 加权组合：每个股票按其实际可用因子动态再归一化（缺失因子不稀释也不补 50）
        all_stocks = set()
        for norm, _ in normalized.values():
            all_stocks.update(norm.keys())

        result = {}
        for s in all_stocks:
            available = [
                (norm[s], w)
                for norm, w in normalized.values()
                if s in norm
            ]
            if not available:
                continue
            total_w = sum(w for _, w in available)
            result[s] = (
                sum(v * (w / total_w) for v, w in available) if total_w > 0 else 50.0
            )

        return result

    # ===================== 私有方法 =====================

    @staticmethod
    def _precompute_stock_features(
            stock_ohlcv: Dict[str, pd.DataFrame],
            codes: List[str],
            assume_sorted: bool = False,
            trade_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        【私有】一次性计算个股级 M3 特征（与板块无关）。

        预计算路径用于性能优化：全市场特征一次计算，多板块复用。

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
        turnover_rates = DragonFactors._compute_turnover_rates(stock_ohlcv, codes)
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
        last_close: Dict[str, float] = {}
        for code in codes:
            df = stock_ohlcv.get(code)
            if df is None or df.empty or "close" not in df.columns:
                continue
            try:
                close = float(df["close"].iloc[-1])
            except (TypeError, ValueError, IndexError):
                continue
            if close > 0 and np.isfinite(close):
                last_close[code] = close
        return {
            "volumes": volumes,
            "turnover_rates": turnover_rates,
            "turnover_anomaly": DragonFactors._compute_turnover_anomaly(stock_ohlcv, codes),
            "boards": boards,
            "boll_trend": boll_trend,
            "boll_pass": boll_pass,
            "returns_by_window": returns_by_window,
            "resonance_by_window": resonance_by_window,
            "last_close": last_close,
        }

    @staticmethod
    def _assemble_sector_raw_from_features(
            sector_stocks: List[str],
            features: Dict[str, Any],
            trade_date: Optional[str] = None,
            fund_provider: Optional[Any] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """【私有】用预计算个股特征组装单板块 M3 raw（仅做板块相对超额收益）。"""
        volumes: Dict[str, float] = features["volumes"]
        turnover_rates: Dict[str, float] = features.get("turnover_rates", {})
        turnover_anomaly: Dict[str, float] = features.get("turnover_anomaly", {})
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

        # F3.1b RPS 分位：个股5日收益在板块内的百分位排名
        f31b_rps_pct = {}
        if _DEFAULT_RETURN_WINDOW in returns_by_window:
            window_returns = returns_by_window[_DEFAULT_RETURN_WINDOW]
            sector_rets = {s: window_returns[s] for s in sector_stocks
                           if s in window_returns and np.isfinite(window_returns[s])}
            if sector_rets:
                codes_list = list(sector_rets.keys())
                rets_array = np.array(list(sector_rets.values()), dtype=float)
                # 等价于原 O(n²) 逻辑：count_less_than = sum(1 for r in all_rets if r < ret)
                # 使用 np.searchsorted 在排序去重值上二分查找，O(n log n) 且正确处理 ties
                sorted_unique = np.unique(rets_array)
                counts_less = np.searchsorted(sorted_unique, rets_array, side='left')
                denom = max(1, len(rets_array) - 1)
                for i, code in enumerate(codes_list):
                    f31b_rps_pct[code] = float(counts_less[i] / denom * 100)

        # P3 F3.6 辨识度溢价：个股换手率 / 板块平均换手率
        f36_identity = {}
        if turnover_rates:
            sector_turnover_rates = {s: turnover_rates[s]
                                     for s in sector_stocks if s in turnover_rates}
            if sector_turnover_rates:
                mean_turnover = float(np.mean(list(sector_turnover_rates.values())))
                if mean_turnover > 0:
                    f36_identity = {s: v / mean_turnover
                                    for s, v in sector_turnover_rates.items()}
        # 降级：用成交额代替换手率
        if not f36_identity:
            sector_amounts = {s: volumes[s] for s in sector_stocks if s in volumes}
            if len(sector_amounts) >= 2:
                mean_amt = float(np.mean(list(sector_amounts.values())))
                if mean_amt > 0:
                    f36_identity = {s: v / mean_amt
                                    for s, v in sector_amounts.items()}

        # P3 F3.7 相对强度：z-score(个股5日收益 vs 板块)
        f37_rel_strength = {}
        if _DEFAULT_RETURN_WINDOW in returns_by_window:
            window_returns = returns_by_window[_DEFAULT_RETURN_WINDOW]
            sector_rets = {s: window_returns[s] for s in sector_stocks
                           if s in window_returns and np.isfinite(window_returns[s])}
            f37_rel_strength = DragonFactors._calculate_relative_strength(sector_rets)

        all_codes: set = set(f32_raw) | set(f33_raw) | set(f35_raw) | set(f35_pass)
        for d in f31_by_window.values():
            all_codes |= set(d)
        for d in f34_by_window.values():
            all_codes |= set(d)
        all_codes |= set(f31b_rps_pct)
        all_codes |= set(f36_identity)
        all_codes |= set(f37_rel_strength)
        all_codes |= set(turnover_anomaly)
        all_codes &= sector_set

        last_close = features.get("last_close", {})
        provider = fund_provider
        if trade_date and provider is None:
            provider = _get_fundamental_provider()
        use_fundamental = bool(
            trade_date and provider is not None and getattr(provider, "is_loaded", False)
        )

        result: Dict[str, Dict[str, Any]] = {}
        for code in all_codes:
            row: Dict[str, Any] = {
                "f32_amount": f32_raw.get(code, float("nan")),
                "f33_consecutive_boards": int(boards.get(code, 0)),
                "f35_bollinger_trend": f35_raw.get(code, float("nan")),
                "f35_bollinger_pass": float(f35_pass.get(code, 0.0)),
                "f31b_rps_percentile": f31b_rps_pct.get(code, float("nan")),
                "f36_identity_premium": f36_identity.get(code, float("nan")),
                "f37_relative_strength": f37_rel_strength.get(code, float("nan")),
                "f38_turnover_anomaly": turnover_anomaly.get(code, float("nan")),
                "f39_pb": float("nan"),
                "f40_holder_change": float("nan"),
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
            if use_fundamental:
                DragonFactors._apply_fundamental_factors(
                    row, code, trade_date, last_close, provider
                )
            result[code] = row

        logger.debug(f"✅ M3 原始因子收集完成: {len(result)} 只")
        return result

    @staticmethod
    def _apply_fundamental_factors(
        row: Dict[str, Any],
        code: str,
        trade_date: str,
        last_close: Dict[str, float],
        provider: Any,
    ) -> None:
        """F3.9 PB / F3.10 股东户数变化（PIT-safe，与预计算路径一致）。

        **重要**：`last_close[code]` 必须是**未复权价（raw close）**。
        因为 BPS 是绝对金额、不随除权除息调整，若传入前复权价会让除权后 PB
        系统性低估（f39 反向）。当前 pipeline 从 xtquant/MongoDB 取 close
        时使用的复权类型由数据层决定，因子层无法反查；若上游改用前复权，
        请务必同步保留一份 raw close 供本函数使用。
        """
        close = last_close.get(code)
        if close and close > 0:
            pb_val = provider.compute_pb(code, close, trade_date)
            if pb_val is not None:
                row["f39_pb"] = pb_val
        hc_val = provider.get_holder_change(code, trade_date)
        if hc_val is not None:
            row["f40_holder_change"] = hc_val

    @staticmethod
    def _passes_dragon_filters(raw_row: Dict[str, Any]) -> bool:
        """【私有】φ 门槛（极度放松）：仅排除完全无数据的股票。"""
        # 不再检查 F3.5 布林趋势和 F3.3 连板门槛
        # 所有因子进入打分排名，由加权分数决定排名
        f35 = float(raw_row.get("f35_bollinger_trend", float("nan")))
        if f35 != f35:  # NaN 检查：排除完全无布林数据的
            return False
        return True

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
    def _compute_turnover_rates(
        stock_ohlcv: Dict[str, pd.DataFrame], codes: List[str]
    ) -> Dict[str, float]:
        """【私有】从 OHLCV 提取当日换手率（turnover 列），降级为成交额占比。"""
        result = {}
        for code in codes:
            df = stock_ohlcv.get(code)
            if df is None or df.empty:
                continue
            if "turnover" in df.columns:
                result[code] = float(df["turnover"].iloc[-1])
            elif "turnover_rate" in df.columns:
                result[code] = float(df["turnover_rate"].iloc[-1])
        return result

    @staticmethod
    def _compute_turnover_anomaly(
        stock_ohlcv: Dict[str, pd.DataFrame], codes: List[str]
    ) -> Dict[str, float]:
        """【私有】F3.8 换手率异动：当日换手率 / 20日均值。

        测评结果 (2024): IC=-0.062, ICIR=-0.636 (A级)。
        高换手异动 → 筹码松动 → 后续反转。
        """
        result = {}
        for code in codes:
            df = stock_ohlcv.get(code)
            if df is None or len(df) < 20:
                continue
            if "turnover" in df.columns:
                col = "turnover"
            elif "turnover_rate" in df.columns:
                col = "turnover_rate"
            else:
                continue
            turnover = df[col].values.astype(float)
            today = turnover[-1]
            if not np.isfinite(today) or today <= 0:
                continue
            ma20 = np.nanmean(turnover[-20:])
            if ma20 > 0 and np.isfinite(ma20):
                result[code] = float(today / ma20)
        return result

    @staticmethod
    def _calculate_relative_strength(
        stock_returns: Dict[str, float],
    ) -> Dict[str, float]:
        """【私有】⚠️ DEPRECATED — 2025+2026 测评均为 D 级，已从策略配置移除。

        F3.7：板块内相对强度 = (个股收益 - 板块均值) / 板块标准差。

        返回的是 z-score，范围 [-3, 3]，典型值 [-1, 2]。
        与 F3.1b（RPS分位）互补：F3.1b 看排名，F3.7 看偏离程度。
        """
        if len(stock_returns) < 2:
            return {}
        values = np.array(list(stock_returns.values()), dtype=float)
        mean = float(np.mean(values))
        std = float(np.std(values))
        if std <= 0:
            return {k: 0.0 for k in stock_returns}
        return {k: (v - mean) / std for k, v in stock_returns.items()}

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
                trend[code] = float('nan')
                continue

            closes = df["close"].to_numpy(dtype=float, copy=False)
            # 仅需末尾 window+slope_window 根即可
            use = closes[-(window + slope_window) :]
            # 滚动均线：对 use 逐点算 window 均值（长度=slope_window+1 个有效 mid）
            csum = np.cumsum(use)
            # mid[i] 对应 use[i-window+1:i+1]，i 从 window-1 开始
            mids = []
            for i in range(window - 1, len(use)):
                # cumsum[i] = sum(use[0:i+1])
                # 要计算 sum(use[i-window+1:i+1])，需要 cumsum[i] - cumsum[i-window]
                prev = csum[i - window] if i > window - 1 else 0.0
                mids.append((csum[i] - prev) / window)
            mid = np.asarray(mids, dtype=float)
            if len(mid) < slope_window:
                passed[code] = 0.0
                trend[code] = float("nan")
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

            # 需要至少 slope_window+1 个 mid 点，才能访问 mid[-1-slope_window]
            if len(mid) < slope_window + 1:
                passed[code] = 0.0
                continue
            mid_prev = float(mid[-(slope_window + 1)])
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
                    # 数据不足时显式设置 NaN，而非跳过（避免多窗口下游数据缺失）
                    result[w][code] = float('nan')
                else:
                    result[w][code] = float(healthy[-w:].sum() / w)
        return result
