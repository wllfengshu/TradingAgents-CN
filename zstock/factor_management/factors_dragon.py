"""
龙头层因子计算模块（M3）

公开接口（行情与基本面均由调用方传入，本模块不查库）：
- f31_excess_return / f31b_rps_percentile / f32_amount / f33_consecutive_boards
- f34_resonance_pct / f35_bollinger_trend / f36_identity_premium
- f37_relative_strength / f38_turnover_anomaly / f39_pb / f40_holder_change
- calculate_all_dragon_factors_in_sector_raw / calculate_all_dragon_factors_raw
- raw_from_doc / apply_fundamental_factors

基本面 f39/f40：调用方传入 fund_provider（get_fundamental_asof 结果）；
未传入则 f39/f40 为 NaN。MongoDB 只存原始值；打分在 strategy_management。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

from zstock.common.utils.common_utils import (
    average_rank_percentile,
    ensure_ohlcv_sorted,
    is_limit_up,
    ohlcv_asof,
)
from zstock.common.entity.factor_spec import FactorRegistry, FactorSpec, factor

logger = logging.getLogger(__name__)


def _finite_map(values: Optional[Dict[str, float]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for key, raw in (values or {}).items():
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if np.isfinite(val):
            out[key] = val
    return out

# 预计算特征工程多窗口（落库供策略按需订阅，默认窗口与线上打分一致）
_RETURN_WINDOWS = (5, 10, 15, 20)
_RESONANCE_WINDOWS = (3, 5, 10)
_DEFAULT_RETURN_WINDOW = 5
_DEFAULT_RESONANCE_WINDOW = 5
_BOLL_WINDOW = 20
_BOLL_SLOPE_WINDOW = 5
_TURNOVER_ANOMALY_WINDOW = 20


class DragonFactors:
    """龙头层因子（M3）：公开单因子算子 + 批处理组装；只计算，不查库。"""

    # ===================== 公开接口 =====================

    @staticmethod
    def calculate_all_dragon_factors_in_sector_raw(
        sector_stocks: List[str],
        stock_ohlcv: Dict[str, pd.DataFrame],
        assume_sorted: bool = False,
        trade_date: Optional[str] = None,
        fund_provider: Optional[Any] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """计算单个板块的龙头因子原始值。"""
        by_sector = DragonFactors.calculate_all_dragon_factors_raw(
            [("", list(sector_stocks or []))],
            stock_ohlcv,
            assume_sorted=assume_sorted,
            trade_date=trade_date,
            fund_provider=fund_provider,
        )
        return next(iter(by_sector.values()), {})

    @staticmethod
    def calculate_all_dragon_factors_raw(
        sector_jobs: List[Tuple[str, List[str]]],
        stock_ohlcv: Dict[str, pd.DataFrame],
        assume_sorted: bool = False,
        trade_date: Optional[str] = None,
        fund_provider: Optional[Any] = None,
    ) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """多板块原始因子：个股特征只算一次，再按板块组装。

        Args:
            sector_jobs: [(sector_code, [code, ...]), ...]
            stock_ohlcv: {code: DataFrame}
            fund_provider: get_fundamental_asof 的结果；None 则 f39/f40 为 NaN

        Returns:
            {sector_code: {code: raw_dict}}
        """
        all_codes: List[str] = []
        seen: Set[str] = set()
        for _, codes in sector_jobs:
            for code in codes:
                if code not in seen:
                    seen.add(code)
                    all_codes.append(code)
        if not all_codes:
            return {}
        features = DragonFactors.precompute_stock_features(
            stock_ohlcv,
            all_codes,
            assume_sorted=assume_sorted,
            trade_date=trade_date,
        )
        out: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for sector_code, codes in sector_jobs:
            raw = DragonFactors.assemble_sector_raw_from_features(
                codes,
                features,
                trade_date=trade_date,
                fund_provider=fund_provider,
            )
            if raw:
                out[sector_code] = raw
        return out

    @staticmethod
    def raw_from_doc(d: Dict[str, Any]) -> Dict[str, Any]:
        """预计算龙头文档 → 单股 raw（字段别名由本类持有）。"""
        nan = float("nan")
        return {
            "f31b_rps_percentile": d.get("f31b_rps_percentile", nan),
            "f31_excess_return_5d": d.get("f31_excess_return_5d", d.get("f31_excess_return", nan)),
            "f31_excess_return_10d": d.get("f31_excess_return_10d", nan),
            "f31_excess_return_15d": d.get("f31_excess_return_15d", nan),
            "f31_excess_return_20d": d.get("f31_excess_return_20d", nan),
            "f32_amount": d.get("f32_amount", nan),
            "f33_consecutive_boards": d.get("f33_consecutive_boards", 0),
            "f34_resonance_pct_5d": d.get("f34_resonance_pct_5d", d.get("f34_resonance_pct", nan)),
            "f34_resonance_pct_3d": d.get("f34_resonance_pct_3d", nan),
            "f34_resonance_pct_10d": d.get("f34_resonance_pct_10d", nan),
            "f35_bollinger_trend": d.get("f35_bollinger_trend", nan),
            "f35_bollinger_pass": d.get("f35_bollinger_pass", 0),
            "f36_identity_premium": d.get("f36_identity_premium", nan),
            "f37_relative_strength": d.get("f37_relative_strength", nan),
            "f38_turnover_anomaly": d.get("f38_turnover_anomaly", nan),
            "f39_pb": d.get("f39_pb", nan),
            "f40_holder_change": d.get("f40_holder_change", nan),
        }

    @staticmethod
    def precompute_stock_features(
            stock_ohlcv: Dict[str, pd.DataFrame],
            codes: List[str],
            assume_sorted: bool = False,
            trade_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """一次性计算个股级 M3 特征（与板块无关），供多板块复用。

        trade_date 提供时要求末行恰好为该日，停牌/缺日股票不计入当日截面。
        """
        if not assume_sorted:
            subset = {
                c: stock_ohlcv[c]
                for c in codes
                if c in (stock_ohlcv or {})
            }
            stock_ohlcv = DragonFactors.sorted_ohlcv_map(subset)
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

        volumes = DragonFactors.daily_volumes(stock_ohlcv, codes)
        turnover_rates = DragonFactors.turnover_rates(stock_ohlcv, codes)
        boards = DragonFactors.f33_consecutive_boards(stock_ohlcv, codes)
        boll_trend, boll_pass = DragonFactors.f35_bollinger_trend(
            stock_ohlcv, codes
        )

        returns_by_window = {
            w: DragonFactors.nd_returns(stock_ohlcv, codes, w)
            for w in _RETURN_WINDOWS
        }
        # 共振多窗口：一次扫最大窗口，再切分子窗口
        resonance_by_window = DragonFactors.f34_resonance_pct(
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
            "turnover_anomaly": DragonFactors.f38_turnover_anomaly(stock_ohlcv, codes),
            "boards": boards,
            "boll_trend": boll_trend,
            "boll_pass": boll_pass,
            "returns_by_window": returns_by_window,
            "resonance_by_window": resonance_by_window,
            "last_close": last_close,
        }

    @staticmethod
    def assemble_sector_raw_from_features(
            sector_stocks: List[str],
            features: Dict[str, Any],
            trade_date: Optional[str] = None,
            fund_provider: Optional[Any] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """用预计算个股特征组装单板块 M3 raw（仅做板块相对超额收益）。"""
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
            f31_by_window[w] = DragonFactors.f31_excess_return(
                sector_returns
            )

        f34_by_window: Dict[int, Dict[str, float]] = {
            w: {s: mp[s] for s in sector_stocks if s in mp}
            for w, mp in resonance_by_window.items()
        }

        f31b_rps_pct: Dict[str, float] = {}
        f37_rel_strength: Dict[str, float] = {}
        if _DEFAULT_RETURN_WINDOW in returns_by_window:
            window_returns = returns_by_window[_DEFAULT_RETURN_WINDOW]
            sector_rets = {
                s: window_returns[s]
                for s in sector_stocks
                if s in window_returns and np.isfinite(window_returns[s])
            }
            f31b_rps_pct = DragonFactors.f31b_rps_percentile(sector_rets)
            f37_rel_strength = DragonFactors.f37_relative_strength(sector_rets)

        sector_turnover = {
            s: turnover_rates[s] for s in sector_stocks if s in turnover_rates
        }
        sector_amounts = {s: volumes[s] for s in sector_stocks if s in volumes}
        f36_identity = DragonFactors.f36_identity_premium(
            sector_turnover, amounts=sector_amounts
        )

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
        fundamentals = fund_provider if isinstance(fund_provider, dict) else None
        use_fundamental = bool(trade_date and fundamentals)

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
                DragonFactors.apply_fundamental_factors(
                    row, code, last_close, fundamentals
                )
            result[code] = row

        logger.debug(f"✅ M3 原始因子收集完成: {len(result)} 只")
        return result

    @staticmethod
    def apply_fundamental_factors(
        row: Dict[str, Any],
        code: str,
        last_close: Dict[str, float],
        fundamentals: Dict[str, Dict[str, float]],
    ) -> None:
        """F3.9 PB / F3.10 股东户数变化。

        fundamentals 来自 DataQueryService.get_fundamental_asof，已按公告日截断。

        **重要**：`last_close[code]` 必须是**未复权价（raw close）**。
        因为 BPS 是绝对金额、不随除权除息调整，若传入前复权价会让除权后 PB
        系统性低估（f39 反向）。当前 pipeline 从 xtquant/MongoDB 取 close
        时使用的复权类型由数据层决定，因子层无法反查；若上游改用前复权，
        请务必同步保留一份 raw close 供本函数使用。
        """
        info = fundamentals.get(code) or {}
        close = last_close.get(code)
        pb_val = DragonFactors.f39_pb(float(close or 0), float(info.get("bps") or 0))
        if np.isfinite(pb_val):
            row["f39_pb"] = pb_val
        hc_val = DragonFactors.f40_holder_change(info.get("holder_change"))
        if np.isfinite(hc_val):
            row["f40_holder_change"] = hc_val

    @staticmethod
    def sorted_ohlcv_map(
        stock_ohlcv: Dict[str, pd.DataFrame],
    ) -> Dict[str, pd.DataFrame]:
        return {c: ensure_ohlcv_sorted(df) for c, df in (stock_ohlcv or {}).items()}

    @staticmethod
    @factor(
        FactorSpec(
            field="f31_excess_return",
            name_en="excess_return",
            name_cn="超额收益",
            definition="个股 N 日收益 - 板块中位数收益",
            inputs=("stock_returns",),
            outputs=("Dict[code, float]",),
            layer="dragon",
            default_polarity="positive",
            regimes=("momentum",),
            windows=_RETURN_WINDOWS,
            include_base=True,
        )
    )
    def f31_excess_return(
        stock_returns: Dict[str, float],
    ) -> Dict[str, float]:
        """F3.1：超额收益（个股N日收益 - 板块中位数）。"""
        finite = {
            k: float(v)
            for k, v in (stock_returns or {}).items()
            if v is not None and np.isfinite(float(v))
        }
        if not finite:
            return {}
        sector_median = float(np.median(list(finite.values())))
        return {k: v - sector_median for k, v in finite.items()}

    @staticmethod
    @factor(
        FactorSpec(
            field="f31b_rps_percentile",
            name_en="rps_percentile",
            name_cn="个股RPS分位",
            definition="个股收益在板块内的平均秩百分位 0~100（并列按股票数，不按不同取值个数）",
            inputs=("stock_returns",),
            outputs=("Dict[code, float]",),
            layer="dragon",
            default_polarity="positive",
            regimes=("momentum",),
        )
    )
    def f31b_rps_percentile(
        stock_returns: Dict[str, float],
    ) -> Dict[str, float]:
        """F3.1b：个股收益在板块内的平均秩百分位（0~100）。"""
        return average_rank_percentile(stock_returns)

    @staticmethod
    @factor(
        FactorSpec(
            field="f32_amount",
            name_en="amount",
            name_cn="成交额",
            definition="当日成交额（优先 amount）",
            inputs=("stock_ohlcv", "codes"),
            outputs=("Dict[code, float]",),
            layer="dragon",
            default_polarity="positive",
            regimes=("momentum", "reversal"),
        )
    )
    def f32_amount(
        stock_ohlcv: Dict[str, pd.DataFrame], codes: List[str]
    ) -> Dict[str, float]:
        """F3.2：当日成交额（优先 amount，否则 volume）。"""
        return DragonFactors.daily_volumes(stock_ohlcv, codes)

    @staticmethod
    @factor(
        FactorSpec(
            field="f36_identity_premium",
            name_en="identity_premium",
            name_cn="身份溢价",
            definition="个股换手 / 板块均值（无换手则用成交额比）",
            inputs=("turnover_rates", "amounts"),
            outputs=("Dict[code, float]",),
            layer="dragon",
            default_polarity="negative",
            regimes=("momentum", "reversal"),
        )
    )
    def f36_identity_premium(
        turnover_rates: Dict[str, float],
        amounts: Optional[Dict[str, float]] = None,
    ) -> Dict[str, float]:
        """F3.6：辨识度溢价 = 个股换手 / 板块均值；无换手时降级为成交额比。"""
        finite_to = _finite_map(turnover_rates)
        if finite_to:
            mean_turnover = float(np.mean(list(finite_to.values())))
            if mean_turnover > 0:
                return {s: v / mean_turnover for s, v in finite_to.items()}
        finite_amt = _finite_map(amounts)
        if len(finite_amt) >= 2:
            mean_amt = float(np.mean(list(finite_amt.values())))
            if mean_amt > 0:
                return {s: v / mean_amt for s, v in finite_amt.items()}
        return {}

    @staticmethod
    @factor(
        FactorSpec(
            field="f39_pb",
            name_en="pb",
            name_cn="市净率PB",
            definition="未复权收盘价 / BPS",
            inputs=("close", "bps"),
            outputs=("float",),
            layer="dragon",
            default_polarity="negative",
            regimes=("momentum", "reversal"),
        )
    )
    def f39_pb(close: float, bps: float) -> float:
        """F3.9：市净率 PB = 未复权收盘价 / BPS；无效时返回 NaN。"""
        if close and close > 0 and bps:
            pb_val = float(close) / float(bps)
            if np.isfinite(pb_val) and pb_val > 0:
                return pb_val
        return float("nan")

    @staticmethod
    @factor(
        FactorSpec(
            field="f40_holder_change",
            name_en="holder_change",
            name_cn="股东户数变化",
            definition="股东户数相对变化",
            inputs=("holder_change",),
            outputs=("float",),
            layer="dragon",
            default_polarity="negative",
            regimes=("momentum", "reversal"),
        )
    )
    def f40_holder_change(holder_change: Any) -> float:
        """F3.10：股东户数变化；无效时返回 NaN。"""
        if holder_change is None:
            return float("nan")
        try:
            val = float(holder_change)
        except (TypeError, ValueError):
            return float("nan")
        return val if np.isfinite(val) else float("nan")

    @staticmethod
    def nd_returns(
        stock_ohlcv: Dict[str, pd.DataFrame],
        codes: List[str],
        window: int,
    ) -> Dict[str, float]:
        """从 OHLCV 计算 N 日收益率。"""
        need = window + 1
        result = {}
        for code in codes:
            df = stock_ohlcv.get(code)
            if df is None or df.empty or "close" not in df.columns or len(df) < need:
                continue
            closes = df["close"].to_numpy(dtype=float, copy=False)
            base = float(closes[-need])
            last = float(closes[-1])
            if base > 0 and np.isfinite(base) and np.isfinite(last):
                result[code] = (last - base) / base
        return result

    @staticmethod
    def daily_volumes(
        stock_ohlcv: Dict[str, pd.DataFrame], codes: List[str]
    ) -> Dict[str, float]:
        """从 OHLCV 提取当日成交额（amount 末行）"""
        result = {}
        for code in codes:
            df = stock_ohlcv.get(code)
            if df is None or df.empty:
                continue
            raw = None
            if "amount" in df.columns:
                raw = df["amount"].iloc[-1]
            elif "volume" in df.columns:
                raw = df["volume"].iloc[-1]
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            if np.isfinite(val):
                result[code] = val
        return result

    @staticmethod
    def turnover_rates(
        stock_ohlcv: Dict[str, pd.DataFrame], codes: List[str]
    ) -> Dict[str, float]:
        """从 OHLCV 提取当日换手率（turnover 列），降级为成交额占比。"""
        result = {}
        for code in codes:
            df = stock_ohlcv.get(code)
            if df is None or df.empty:
                continue
            raw = None
            if "turnover" in df.columns:
                raw = df["turnover"].iloc[-1]
            elif "turnover_rate" in df.columns:
                raw = df["turnover_rate"].iloc[-1]
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            if np.isfinite(val):
                result[code] = val
        return result

    @staticmethod
    @factor(
        FactorSpec(
            field="f38_turnover_anomaly",
            name_en="turnover_anomaly",
            name_cn="换手异动",
            definition="当日换手 / 20 日均值",
            inputs=("stock_ohlcv", "codes"),
            outputs=("Dict[code, float]",),
            layer="dragon",
            default_polarity="negative",
            regimes=("momentum", "reversal"),
        )
    )
    def f38_turnover_anomaly(
        stock_ohlcv: Dict[str, pd.DataFrame], codes: List[str]
    ) -> Dict[str, float]:
        """F3.8 换手率异动：当日换手率 / 20日均值。

        测评结果 (2024): IC=-0.062, ICIR=-0.636 (A级)。
        高换手异动 → 筹码松动 → 后续反转。
        """
        result = {}
        for code in codes:
            df = stock_ohlcv.get(code)
            if df is None or len(df) < _TURNOVER_ANOMALY_WINDOW:
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
            ma20 = np.nanmean(turnover[-_TURNOVER_ANOMALY_WINDOW:])
            if ma20 > 0 and np.isfinite(ma20):
                result[code] = float(today / ma20)
        return result

    @staticmethod
    @factor(
        FactorSpec(
            field="f37_relative_strength",
            name_en="relative_strength",
            name_cn="相对强度",
            definition="板块内收益 z-score",
            inputs=("stock_returns",),
            outputs=("Dict[code, float]",),
            layer="dragon",
            default_polarity="positive",
            regimes=("momentum",),
        )
    )
    def f37_relative_strength(
        stock_returns: Dict[str, float],
    ) -> Dict[str, float]:
        """⚠️ DEPRECATED — 2025+2026 测评均为 D 级，已从策略配置移除。

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
    @factor(
        FactorSpec(
            field="f33_consecutive_boards",
            name_en="consecutive_boards",
            name_cn="连板天数",
            definition="最近连续涨停天数",
            inputs=("stock_ohlcv", "codes"),
            outputs=("Dict[code, int]",),
            layer="dragon",
            default_polarity="negative",
            regimes=("momentum", "reversal"),
        )
    )
    def f33_consecutive_boards(
        stock_ohlcv: Dict[str, pd.DataFrame], codes: List[str]
    ) -> Dict[str, int]:
        """从 OHLCV 计算最近连续涨停天数（按板块阈值）。"""
        result = {}
        for code in codes:
            df = stock_ohlcv.get(code)
            if df is None or len(df) < 2 or "close" not in df.columns:
                continue
            closes = df["close"].to_numpy(dtype=float, copy=False)
            dates = (
                df["trade_date"].astype(str).tolist()
                if "trade_date" in df.columns
                else [None] * len(closes)
            )
            count = 0
            for i in range(len(closes) - 1, 0, -1):
                prev = closes[i - 1]
                if not (prev > 0) or not np.isfinite(closes[i]):
                    break
                if is_limit_up(code, prev, closes[i], dates[i]):
                    count += 1
                else:
                    break
            result[code] = count
        return result

    @staticmethod
    @factor(
        FactorSpec(
            field="f35_bollinger_trend",
            name_en="bollinger_trend",
            name_cn="布林趋势",
            definition="价格相对布林中轨的趋势分",
            inputs=("stock_ohlcv", "codes"),
            outputs=("Dict[code, float]",),
            layer="dragon",
            default_polarity="positive",
            regimes=("momentum",),
        )
    )
    def f35_bollinger_trend(
        stock_ohlcv: Dict[str, pd.DataFrame],
        codes: List[str],
        window: int = _BOLL_WINDOW,
        slope_window: int = _BOLL_SLOPE_WINDOW,
        slope_threshold: float = 0.0,
    ) -> tuple:
        """一次算布林中轨，同时产出 F3.5 趋势分与硬过滤 pass。"""
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
    @factor(
        FactorSpec(
            field="f34_resonance_pct",
            name_en="resonance_pct",
            name_cn="共振涨幅",
            definition="量价共振窗口涨幅",
            inputs=("stock_ohlcv", "codes", "windows"),
            outputs=("Dict[window, Dict[code, float]]",),
            layer="dragon",
            default_polarity="negative",
            regimes=("momentum", "reversal"),
            windows=_RESONANCE_WINDOWS,
            include_base=True,
        )
    )
    def f34_resonance_pct(
        sector_stocks: List[str],
        stock_ohlcv: Optional[Dict[str, pd.DataFrame]],
        windows: tuple = _RESONANCE_WINDOWS,
    ) -> Dict[int, Dict[str, float]]:
        """
        F3.4 多窗口量价共振：一次扫最大窗口，再按窗口切片。

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
            if "trade_date" in df.columns:
                day_dates = df["trade_date"].astype(str).tolist()[-need:]
            else:
                day_dates = [None] * len(close_arr)
            limit_up = np.array(
                [
                    is_limit_up(code, prev_close[i], close_arr[i + 1], day_dates[i + 1])
                    for i in range(len(prev_close))
                ],
                dtype=bool,
            )
            is_sync = ((price_chg > 0) & (vol_chg > 0)) | (
                (price_chg < 0) & (vol_chg < 0)
            )
            healthy = is_sync | limit_up
            for w in windows:
                if len(healthy) < w:
                    # 数据不足时显式设置 NaN，而非跳过（避免多窗口下游数据缺失）
                    result[w][code] = float('nan')
                else:
                    result[w][code] = float(healthy[-w:].sum() / w)
        return result


# 测评字段（无独立公开算子，随批处理落库）
FactorRegistry.register(
    FactorSpec(
        field="f35_bollinger_pass",
        name_en="bollinger_pass",
        name_cn="布林趋势硬过滤",
        definition="布林中轨趋势是否通过硬门槛",
        inputs=("stock_ohlcv", "codes"),
        outputs=("Dict[code, float]",),
        layer="dragon",
        default_polarity="positive",
        regimes=("momentum",),
        catalog=False,
        eval=True,
    )
)
