"""
主线板块层因子计算模块（M2）

公开接口（行情/资金流由调用方传入，本模块不查库）：
- f21_rps / f22_main_flow / f23_limit_up_density / f24_max_consecutive
- f25_volume_slope / f26_volume_growth / f27_new_high_ratio / f28_consistency
- f29_sector_breadth / f30_sector_concentration
- calculate_all_sector_factors_raw / raw_from_docs / aggregate_sector_ohlcv

MongoDB 只存原始因子值；打分与选股在 strategy_management。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

from zstock.common.utils.common_utils import (
    ensure_ohlcv_sorted,
    flow_docs_asof,
    average_rank_percentile,
    is_limit_up,
    ohlcv_asof,
    WAN_TO_YUAN,
)
from zstock.common.entity.factor_spec import FactorSpec, factor

logger = logging.getLogger(__name__)

# ==============================================================================
# 特征工程常数：预计算多窗口 + 算子数学定义（非策略权重/门禁）。
# ==============================================================================

# 特征工程多窗口（落库供策略按需订阅）
_RPS_WINDOWS = (10, 20, 60)
_VOLUME_SLOPE_WINDOWS = (3, 5, 10)
_VOLUME_SHARE_INCREASE_WINDOWS = (5, 20)

# F2.5 / F2.6 内部均线平滑与回归窗口
_VOLUME_SLOPE_REG_WINDOW = 5

# F2.8 板块持续性算子的数学定义（回溯 10 日，统计跻身 Top 10% 的天数）
_CONSISTENCY_WINDOW = 10
_CONSISTENCY_TOP_PCT = 0.10

# 历史无后缀字段兼容别名默认窗口（如 f21_rps 别名映射）
_DEFAULT_RPS_WINDOW = 20
_DEFAULT_VOLUME_SLOPE_WINDOW = 5
_DEFAULT_VOLUME_SHARE_INCREASE_WINDOW = 5


class SectorFactors:
    """板块层因子（M2）：公开单因子算子（f21~f30）+ 批处理 raw。"""

    # ===================== 公开接口 =====================

    @staticmethod
    def aggregate_sector_ohlcv(
        sector_stocks: Dict[str, List[str]],
        stock_ohlcv: Dict[str, pd.DataFrame],
        stock_flow_recent: Optional[Dict[str, List[Dict]]] = None,
        eligible_codes: Optional[Set[str]] = None,
        trade_date: Optional[str] = None,
    ) -> Tuple[Dict[str, pd.DataFrame], Dict[str, Dict]]:
        """将成分股行情与资金流聚合为板块日线 OHLCV 与资金流。"""
        return SectorFactors._aggregate_sectors_from_stocks(
            sector_stocks=sector_stocks,
            stock_ohlcv=stock_ohlcv,
            stock_flow_recent=stock_flow_recent or {},
            eligible_codes=eligible_codes,
            trade_date=trade_date,
        )

    @staticmethod
    def calculate_all_sector_factors_raw(
        sectors: List[Dict],
        sector_stocks: Dict[str, List[str]],
        stock_ohlcv: Dict[str, pd.DataFrame],
        stock_flow_recent: Optional[Dict[str, List[Dict]]] = None,
        sector_ohlcv: Optional[Dict[str, pd.DataFrame]] = None,
        market_sector_ohlcv: Optional[Dict[str, pd.DataFrame]] = None,
        trade_date: Optional[str] = None,
        eligible_codes: Optional[set] = None,
    ) -> Dict[str, Dict]:
        """【公开接口】返回 M2 子因子原始值（归一化前）。

        Args:
            sectors: 板块列表，每项含 sector_code、sector_name 等元数据
            sector_stocks: 板块成分股映射 {sector_code: [code1, code2, ...]}
            stock_ohlcv: 个股 OHLCV 数据 {code: DataFrame}，包含 trade_date/close/volume/amount 等
            stock_flow_recent: 个股资金流（近 N 日）{code: [day_doc1, day_doc2, ...]}；
                每个 day_doc 含 main_net/turnover 等字段，用于 F2.2 资金流计算
            sector_ohlcv: 可选预聚合板块 OHLCV {sector_code: DataFrame}；
                若传入则跳过个股→板块聚合（耗时操作），直接复用；
                若为 None 则从 stock_ohlcv 动态聚合
            market_sector_ohlcv: 必需！全市场板块 OHLCV {sector_code: DataFrame}，
                用于 F2.8 持续性的全市场排名计算；
                若传入 None 则抛出 ValueError，因子计算必须基于完整市场数据
            trade_date: 截面日期（YYYY-MM-DD 格式，可选）；
                若提供则要求 stock_ohlcv 和 market_sector_ohlcv 末行恰好为该日；
                不提供时使用各 DataFrame 最后一行作为截面
            eligible_codes: 主板非 ST 等可交易宇宙（set 类型）；
                F2.2（资金流）、F2.3（涨停浓度）、F2.4（连板高度）仅统计该集合；
                若为 None 则统计全部成分股

        Returns:
            Dict 含多个因子类型及其原始值映射：
            - f21_rps_{w}d / f21_rps：RPS 原始值（多窗口 + 默认窗口）
            - f22_main_flow：主力净流入（元）
            - f23_limit_up_density：涨停浓度 [0, 1]
            - f24_max_consecutive：最大连板天数
            - f25_volume_slope_{w}d / f25_volume_slope：成交占比 MA 斜率（多窗口）
            - f26_volume_growth_{w}d / f26_volume_growth：成交额增长率（多窗口）
            - f28_consistency：10 日 Top10% 天数（0-10 的整数）
            - sector_names：板块名称映射 {sector_code: sector_name}
        """
        if market_sector_ohlcv is None:
            if sector_ohlcv is not None and len(sector_ohlcv) > 0:
                market_sector_ohlcv = sector_ohlcv
            else:
                market_sector_ohlcv, _ = SectorFactors._aggregate_sectors_from_stocks(
                    sector_stocks or {},
                    stock_ohlcv or {},
                    stock_flow_recent or {},
                    trade_date=trade_date,
                )
        stock_ohlcv = {
            c: ensure_ohlcv_sorted(df) for c, df in (stock_ohlcv or {}).items()
        }
        if trade_date:
            stock_ohlcv = {
                c: adf
                for c, df in (stock_ohlcv or {}).items()
                if (adf := ohlcv_asof(df, trade_date, require_exact=True)) is not None
            }
            if sector_ohlcv is not None:
                sector_ohlcv = {
                    c: adf
                    for c, df in sector_ohlcv.items()
                    if (adf := ohlcv_asof(df, trade_date, require_exact=True))
                    is not None
                }

        # 只聚合本次 sectors 涉及的板块，避免全表空转
        need_codes = {
            s.get("sector_code") for s in sectors if s.get("sector_code")
        }
        sector_stocks_use = {
            k: v for k, v in (sector_stocks or {}).items() if k in need_codes
        }
        if sector_ohlcv is None:
            sector_ohlcv, sector_capital_flow = (
                SectorFactors._aggregate_sectors_from_stocks(
                    sector_stocks_use,
                    stock_ohlcv,
                    stock_flow_recent or {},
                    eligible_codes=eligible_codes,
                    trade_date=trade_date,
                )
            )
        else:
            sector_ohlcv = {
                k: v for k, v in sector_ohlcv.items() if k in need_codes
            }
            # 如果提供了预聚合的 sector_ohlcv，那么只计算资金流
            _, sector_capital_flow = SectorFactors._aggregate_sectors_from_stocks(
                sector_stocks_use,
                stock_ohlcv,
                stock_flow_recent or {},
                eligible_codes=eligible_codes,
                trade_date=trade_date,
            )

        # 涨停/连板：只扫板块内 eligible 成分（默认全成分）
        member_codes: set = set()
        for codes in sector_stocks_use.values():
            if eligible_codes is None:
                member_codes.update(codes)
            else:
                member_codes.update(c for c in codes if c in eligible_codes)
        (
            all_stocks_limit_up,
            all_stocks_consecutive_boards,
        ) = SectorFactors._compute_limit_and_consecutive_from_ohlcv(
            stock_ohlcv, codes=member_codes or None
        )

        valid_sector_codes = {
            s["sector_code"] for s in sectors if s.get("sector_code") is not None
        }
        sector_names = {
            s["sector_code"]: s.get("sector_name", "")
            for s in sectors
            if s.get("sector_code")
        }
        sectors_valid = [
            s for s in sectors if s.get("sector_code") in valid_sector_codes
        ]

        f21_by_window = {
            w: SectorFactors.f21_rps(
                sector_ohlcv, window=w, sector_codes=valid_sector_codes
            )
            for w in _RPS_WINDOWS
        }
        # 成交占比面板只建一次，多窗口复用
        f25_by_window = SectorFactors.f25_volume_slope(
            sector_ohlcv,
            windows=_VOLUME_SLOPE_WINDOWS,
            sector_codes=valid_sector_codes,
        )
        # F2.6 成交额增长（多窗口）
        # F2.6 成交额增长（多窗口）：根据 short_window 动态确定 long_window
        # 防止 short_window == long_window 导致 MA/MA=1 的逻辑错误
        f26_by_window = {}
        for w in _VOLUME_SHARE_INCREASE_WINDOWS:
            # 动态长窗口：5d→20d, 20d→60d
            long_w = 20 if w == 5 else (60 if w == 20 else max(20, w * 3))
            f26_by_window[w] = SectorFactors.f26_volume_growth(
                sector_ohlcv,
                short_window=w,
                long_window=long_w,
                sector_codes=valid_sector_codes,
            )
        # F2.8 10 日持续性（φ 门槛）- 基于全市场排名
        f28_consistency = SectorFactors.f28_consistency(
            sector_ohlcv,
            market_sector_ohlcv=market_sector_ohlcv,  # 必需使用全市场排名
            window=_CONSISTENCY_WINDOW,
            sector_codes=valid_sector_codes,
        )

        # 板块结构因子（P3 新增）
        f29_breadth = SectorFactors.f29_sector_breadth(
            sector_stocks_use, stock_ohlcv, eligible_codes=eligible_codes
        )
        f30_concentration = SectorFactors.f30_sector_concentration(
            sector_stocks_use, stock_ohlcv, eligible_codes=eligible_codes
        )
        f27_new_high_ratio = SectorFactors.f27_new_high_ratio(
            sector_stocks_use, stock_ohlcv, window=20, eligible_codes=eligible_codes
        )

        f22_main_flow = SectorFactors.f22_main_flow(
            {k: v for k, v in sector_capital_flow.items() if k in valid_sector_codes}
        )
        f23_limit_up_density = SectorFactors.f23_limit_up_density(
            sectors_valid,
            sector_stocks,
            all_stocks_limit_up,
            eligible_codes=eligible_codes,
        )
        f24_max_consecutive = SectorFactors.f24_max_consecutive(
            sectors_valid,
            sector_stocks,
            all_stocks_consecutive_boards,
            eligible_codes=eligible_codes,
        )

        out: Dict[str, Dict] = {
            "sector_names": sector_names,
            "f22_main_flow": f22_main_flow,
            "f23_limit_up_density": f23_limit_up_density,
            "f24_max_consecutive": f24_max_consecutive,
            "f28_consistency": f28_consistency,
            # 板块结构因子（P3 新增）
            "f27_new_high_ratio": f27_new_high_ratio,
            "f29_sector_breadth": f29_breadth,
            "f30_sector_concentration": f30_concentration,
        }
        for w, mp in f21_by_window.items():
            out[f"f21_rps_{w}d"] = mp
        for w, mp in f25_by_window.items():
            out[f"f25_volume_slope_{w}d"] = mp
        for w, mp in f26_by_window.items():
            out[f"f26_volume_growth_{w}d"] = mp
        out["f21_rps"] = f21_by_window[_DEFAULT_RPS_WINDOW]
        out["f25_volume_slope"] = f25_by_window[_DEFAULT_VOLUME_SLOPE_WINDOW]
        out["f26_volume_growth"] = f26_by_window[_DEFAULT_VOLUME_SHARE_INCREASE_WINDOW]

        logger.debug(
            f"✅ M2 原始因子收集完成: {len(out['f21_rps'])} 个板块 "
            f"(RPS windows={_RPS_WINDOWS})"
        )
        return out

    @staticmethod
    def raw_from_docs(docs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """预计算板块文档列表 → {field: {sector_code: value}}。"""
        nan = float("nan")

        def col(field: str, alias: Optional[str] = None, default: Any = nan) -> Dict[str, Any]:
            out: Dict[str, Any] = {}
            for d in docs:
                sc = d.get("sector_code")
                if not sc:
                    continue
                val = d.get(field, d.get(alias, default) if alias else default)
                out[sc] = val
            return out

        return {
            "f21_rps_20d": col("f21_rps_20d", "f21_rps"),
            "f21_rps_10d": col("f21_rps_10d"),
            "f21_rps_60d": col("f21_rps_60d"),
            "f22_main_flow": col("f22_main_flow", default=0.0),
            "f23_limit_up_density": col("f23_limit_up_density", default=0.0),
            "f24_max_consecutive": col("f24_max_consecutive", default=0.0),
            "f25_volume_slope_5d": col("f25_volume_slope_5d", "f25_volume_slope"),
            "f25_volume_slope_3d": col("f25_volume_slope_3d"),
            "f25_volume_slope_10d": col("f25_volume_slope_10d"),
            "f26_volume_growth_5d": col("f26_volume_growth_5d", "f26_volume_growth"),
            "f26_volume_growth_20d": col("f26_volume_growth_20d"),
            "f27_new_high_ratio": col("f27_new_high_ratio"),
            "f28_consistency": col("f28_consistency", default=0),
            "f29_sector_breadth": col("f29_sector_breadth"),
            "f30_sector_concentration": col("f30_sector_concentration"),
        }

    # ===================== 公开单因子算子 =====================

    @staticmethod
    @factor(
        FactorSpec(
            field="f21_rps",
            name_en="sector_rps",
            name_cn="板块RPS",
            definition="近 window 日收益在全部板块中的平均秩百分位 0~100",
            inputs=("sector_ohlcv", "window"),
            outputs=("Dict[sector_code, float]",),
            layer="sector",
            default_polarity="negative",
            regimes=("momentum", "reversal"),
            windows=_RPS_WINDOWS,
        )
    )
    def f21_rps(
        sector_ohlcv: Dict[str, pd.DataFrame],
        window: int = None,
        sector_codes: set = None,
    ) -> Dict[str, float]:
        """板块窗口收益的截面平均秩百分位（0~100），不是绝对涨跌幅。"""
        if window is None:
            window = _DEFAULT_RPS_WINDOW
        raw_returns = {}
        for sector_code, df in sector_ohlcv.items():
            if sector_codes is not None and sector_code not in sector_codes:
                continue
            if len(df) >= window + 1 and "close" in df.columns:
                base = float(df["close"].iloc[-window - 1])
                last = float(df["close"].iloc[-1])
                if base <= 0 or np.isnan(base) or np.isnan(last):
                    continue
                raw_returns[sector_code] = last / base - 1
        return average_rank_percentile(raw_returns)

    @staticmethod
    @factor(
        FactorSpec(
            field="f22_main_flow",
            name_en="sector_main_flow",
            name_cn="板块主力净流入",
            definition="板块成分股主力净流入合计",
            inputs=("sector_capital_flow",),
            outputs=("Dict[sector_code, float]",),
            layer="sector",
            default_polarity="positive",
            regimes=("momentum", "reversal"),
        )
    )
    def f22_main_flow(
        sector_capital_flow: Dict[str, Dict[str, float]],
    ) -> Dict[str, float]:
        """收集板块资金流原始值（主力净流入）。"""
        return {
            sector_code: float(flow_info.get("main_flow", 0.0))
            for sector_code, flow_info in sector_capital_flow.items()
        }

    @staticmethod
    def _compute_limit_and_consecutive_from_ohlcv(
        stock_ohlcv: Dict[str, pd.DataFrame],
        codes: Optional[set] = None,
    ) -> Tuple[Dict[str, bool], Dict[str, int]]:
        """一次遍历同时计算当日涨停标志和最近连续涨停天数。"""
        limit_up_result: Dict[str, bool] = {}
        consecutive_result: Dict[str, int] = {}
        if codes is None:
            items = (stock_ohlcv or {}).items()
        else:
            items = (
                (c, stock_ohlcv[c])
                for c in codes
                if c in (stock_ohlcv or {})
            )
        for code, df in items:
            if df is None or len(df) < 2 or "close" not in df.columns:
                limit_up_result[code] = False
                continue

            closes = df["close"].to_numpy(dtype=float, copy=False)
            dates = (
                df["trade_date"].astype(str).tolist()
                if "trade_date" in df.columns
                else [None] * len(closes)
            )
            prev_close = closes[-2]
            if not (prev_close > 0) or not np.isfinite(closes[-1]):
                limit_up_result[code] = False
            else:
                limit_up_result[code] = is_limit_up(
                    code, prev_close, closes[-1], dates[-1]
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
            consecutive_result[code] = count

        return limit_up_result, consecutive_result

    @staticmethod
    def _aggregate_sectors_from_stocks(
        sector_stocks: Dict[str, List[str]],
        stock_ohlcv: Dict[str, pd.DataFrame],
        stock_flow_recent: Dict[str, List[Dict]],
        *,
        eligible_codes: Optional[set] = None,
        trade_date: Optional[str] = None,
    ) -> Tuple[Dict[str, pd.DataFrame], Dict[str, Dict[str, float]]]:
        """从个股聚合板块 OHLCV + 资金流（numpy 累加，避免 pandas concat）。

        板块 OHLCV 聚合用全部有行情成分（反映板块指数）；
        资金流 F2.2 在提供 eligible_codes 时只累加主板宇宙。

        trade_date 提供时对个股资金流做 asof 截断（require_exact=True），
        禁止把 trade_date 之后的资金流数据混入截面计算，避免共享缓存跨日
        造成 F2.2 look-ahead。
        """
        sector_ohlcv: Dict[str, pd.DataFrame] = {}
        sector_capital_flow: Dict[str, Dict[str, float]] = {}

        # 个股面板只建一次，供所有板块复用
        stock_panel: Dict[str, Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}
        for code, df in (stock_ohlcv or {}).items():
            if df is None or df.empty:
                continue
            if "trade_date" not in df.columns or "close" not in df.columns:
                continue
            dates = pd.to_datetime(df["trade_date"], errors="coerce").to_numpy()
            close = df["close"].to_numpy(dtype=float, copy=False)
            if "volume" in df.columns:
                vol = df["volume"].to_numpy(dtype=float, copy=False)
            else:
                vol = np.zeros(len(close), dtype=float)
            if "amount" in df.columns:
                amt = df["amount"].to_numpy(dtype=float, copy=False)
            else:
                # 无 amount 时用 volume*close 近似，避免 F2.5 整列为 0
                amt = vol * np.abs(close)
            ret = np.empty(len(close), dtype=float)
            ret[0] = np.nan
            prev = close[:-1]
            cur = close[1:]
            with np.errstate(divide="ignore", invalid="ignore"):
                ret[1:] = np.where(prev > 0, cur / prev - 1.0, np.nan)
            stock_panel[code] = (dates, close, vol, amt, ret)

        for sector_code, codes in sector_stocks.items():
            members = [c for c in codes if c in (stock_ohlcv or {})]
            if not members:
                continue
            flow_members = (
                [c for c in members if c in eligible_codes]
                if eligible_codes is not None
                else members
            )

            vol_sum: Dict[Any, float] = {}
            amt_sum: Dict[Any, float] = {}
            ret_sum: Dict[Any, float] = {}
            first_date: Dict[str, Any] = {}
            for c in members:
                panel = stock_panel.get(c)
                if panel is None:
                    continue
                dates = panel[0]
                present = [d for d in dates if d is not None and not pd.isna(d)]
                if present:
                    first_date[c] = min(present)
            for c in members:
                panel = stock_panel.get(c)
                if panel is None:
                    continue
                dates, _close, vol, amt, ret = panel
                for i, d in enumerate(dates):
                    if d is None or (isinstance(d, float) and np.isnan(d)):
                        continue
                    # pandas NaT
                    if pd.isna(d):
                        continue
                    vol_sum[d] = vol_sum.get(d, 0.0) + float(vol[i])
                    amt_sum[d] = amt_sum.get(d, 0.0) + float(amt[i])
                    r = ret[i]
                    if r == r:  # not nan
                        ret_sum[d] = ret_sum.get(d, 0.0) + float(r)

            if not ret_sum:
                continue

            # 与旧逻辑一致：ret 与 vol/amt 按日期 inner（以有 ret 的日期为主，且需有 vol）
            common = sorted(d for d in ret_sum if d in vol_sum)
            if not common:
                continue
            rets = np.array(
                [
                    ret_sum[d] / max(
                        1,
                        sum(
                            1
                            for c, born in first_date.items()
                            if born <= d
                        ),
                    )
                    for d in common
                ],
                dtype=float,
            )
            rets = np.nan_to_num(rets, nan=0.0)
            closes = 100.0 * np.cumprod(1.0 + rets)
            vols = np.array([vol_sum[d] for d in common], dtype=float)
            amts = np.array([amt_sum[d] for d in common], dtype=float)
            opens = np.empty_like(closes)
            opens[0] = closes[0]
            opens[1:] = closes[:-1]
            ret_abs = np.abs(rets)
            # 注意：high/low 为从收益率反推的近似值，非真实日内极值。
            # 对板块 RPS 和布林计算影响可忽略，因为板块 OHLCV 仅用于比值类因子。
            highs = np.maximum(closes, opens) * (1 + ret_abs * 0.5 + 0.001)
            lows = np.minimum(closes, opens) / (1 + ret_abs * 0.5 + 0.001)
            sector_ohlcv[sector_code] = pd.DataFrame(
                {
                    "trade_date": common,
                    "close": closes,
                    "volume": vols,
                    "amount": amts,
                    "open": opens,
                    "high": highs,
                    "low": lows,
                }
            )

            main_sum = 0.0
            total_amt = 0.0
            for c in flow_members:
                rows = (stock_flow_recent or {}).get(c) or []
                if not rows:
                    continue
                # 与 stock_ohlcv 的 ohlcv_asof(require_exact=True) 语义保持一致：
                # 若提供 trade_date，就把资金流也截到当日且末条必须等于当日，
                # 防止共享缓存/预取跨日的资金流数据造成 F2.2 look-ahead。
                if trade_date:
                    rows = flow_docs_asof(rows, trade_date, require_exact=True)
                    if not rows:
                        continue
                today_doc = rows[-1]
                try:
                    main_sum += float(today_doc.get("main_net", 0.0) or 0.0) * WAN_TO_YUAN
                    total_amt += float(today_doc.get("turnover", 0.0) or 0.0)
                except (ValueError, TypeError):
                    pass
            sector_capital_flow[sector_code] = {
                "main_flow": main_sum,
                "retail_flow": 0.0,
                "total_amount": total_amt,
            }

        return sector_ohlcv, sector_capital_flow

    @staticmethod
    @factor(
        FactorSpec(
            field="f23_limit_up_density",
            name_en="limit_up_density",
            name_cn="涨停密度",
            definition="涨停家数 / 板块成分数",
            inputs=("sectors", "sector_stocks", "all_stocks_limit_up"),
            outputs=("Dict[sector_code, float]",),
            layer="sector",
            default_polarity="positive",
            regimes=("momentum",),
        )
    )
    def f23_limit_up_density(
        sectors: List[Dict],
        sector_stocks: Dict[str, List[str]],
        all_stocks_limit_up: Dict[str, bool],
        eligible_codes: Optional[set] = None,
    ) -> Dict[str, float]:
        """涨停浓度 = 涨停家数 / 成分数（无 OHLCV 视为非涨停）。

        提供 eligible_codes 时，分子分母均只计该宇宙（主板非 ST）。
        """
        sector_limit_up_densities = {}
        for sector in sectors:
            sector_code = sector.get("sector_code")
            if sector_code is None:
                continue
            stocks_in_sector = sector_stocks.get(sector_code, [])
            if eligible_codes is not None:
                stocks_in_sector = [s for s in stocks_in_sector if s in eligible_codes]
            if not stocks_in_sector:
                continue
            density = (
                sum(1 for s in stocks_in_sector if all_stocks_limit_up.get(s, False))
                / len(stocks_in_sector)
            )
            sector_limit_up_densities[sector_code] = density
        return sector_limit_up_densities

    @staticmethod
    @factor(
        FactorSpec(
            field="f24_max_consecutive",
            name_en="max_consecutive_boards",
            name_cn="最大连板高度",
            definition="板块内个股最大连续涨停天数",
            inputs=("sectors", "sector_stocks", "all_stocks_consecutive_boards"),
            outputs=("Dict[sector_code, float]",),
            layer="sector",
            default_polarity="positive",
            regimes=("momentum",),
        )
    )
    def f24_max_consecutive(
        sectors: List[Dict],
        sector_stocks: Dict[str, List[str]],
        all_stocks_consecutive_boards: Dict[str, int],
        eligible_codes: Optional[set] = None,
    ) -> Dict[str, int]:
        """收集连板高度原始值（可选只计 eligible 宇宙）。"""
        sector_consecutive_boards = {}
        for sector in sectors:
            sector_code = sector.get("sector_code")
            if sector_code is None:
                continue
            stocks_in_sector = sector_stocks.get(sector_code, [])
            if eligible_codes is not None:
                stocks_in_sector = [s for s in stocks_in_sector if s in eligible_codes]
            if stocks_in_sector:
                consecutive_counts = [
                    all_stocks_consecutive_boards.get(s, 0) for s in stocks_in_sector
                ]
                sector_consecutive_boards[sector_code] = (
                    max(consecutive_counts) if consecutive_counts else 0
                )
        return sector_consecutive_boards

    @staticmethod
    @factor(
        FactorSpec(
            field="f26_volume_growth",
            name_en="volume_growth",
            name_cn="量能增长",
            definition="MA(short)/MA(long)-1 成交额增长",
            inputs=("sector_ohlcv", "short_window", "long_window"),
            outputs=("Dict[sector_code, float]",),
            layer="sector",
            default_polarity="positive",
            regimes=("momentum",),
            windows=_VOLUME_SHARE_INCREASE_WINDOWS,
        )
    )
    def f26_volume_growth(
        sector_ohlcv: Dict[str, pd.DataFrame],
        short_window: int = None,
        long_window: int = None,
        sector_codes: Optional[set] = None,
    ) -> Dict[str, float]:
        """F2.6 成交额增长：MA(short_window) / MA(long_window) - 1（支持多窗口）。

        返回原始增长率值（未归一化），范围限制在 [-0.99, 10.0] 以避免数值溢出。
        """
        if short_window is None:
            short_window = _DEFAULT_VOLUME_SHARE_INCREASE_WINDOW
        if long_window is None:
            long_window = 20
        result = {}
        for code, df in (sector_ohlcv or {}).items():
            if sector_codes is not None and code not in sector_codes:
                continue
            if df is None or "amount" not in df.columns or len(df) < long_window:
                continue

            amounts = df["amount"].to_numpy(dtype=float, copy=False)
            if len(amounts) < long_window:
                continue

            amounts_series = pd.Series(amounts)
            ma_short = amounts_series.rolling(window=short_window, min_periods=short_window).mean().iloc[-1]
            ma_long = amounts_series.rolling(window=long_window, min_periods=long_window).mean().iloc[-1]

            # 数据有效性检查：避免极端值进入后续计算
            if pd.isna(ma_short) or pd.isna(ma_long) or ma_long <= 1e-8 or ma_short <= 1e-8:
                continue

            # 计算增长率并限制范围，避免 inf 污染
            growth = float(ma_short / ma_long - 1.0)
            if not np.isfinite(growth):
                continue

            # 限制增长率范围：-99% ~ +1000%
            growth = np.clip(growth, -0.99, 10.0)
            result[code] = growth

        return result

    @staticmethod
    @factor(
        FactorSpec(
            field="f28_consistency",
            name_en="sector_consistency",
            name_cn="板块一致性",
            definition="近 N 日跻身市场涨幅 Top10% 的天数",
            inputs=("sector_ohlcv", "market_ret_by_date"),
            outputs=("Dict[sector_code, float]",),
            layer="sector",
            default_polarity="negative",
            regimes=("momentum", "reversal"),
        )
    )
    def f28_consistency(
        sector_ohlcv: Dict[str, pd.DataFrame],
        market_sector_ohlcv: Optional[Dict[str, pd.DataFrame]] = None,
        window: int = 10,
        sector_codes: Optional[set] = None,
    ) -> Dict[str, int]:
        """F2.8 10 日持续性：近 N 日排名 Top 10% 的天数。

        Args:
            market_sector_ohlcv: 全市场板块 OHLCV（用于排名）；为 None 时用 sector_ohlcv 自身排名
        """
        if not sector_ohlcv:
            return {}

        result = {}
        if market_sector_ohlcv is None:
            market_sector_ohlcv = sector_ohlcv

        min_len = window + 1

        def _dates(df):
            if "trade_date" not in df.columns:
                return None
            return pd.to_datetime(df["trade_date"], errors="coerce").to_numpy()

        # 构建全市场每日收益率面板（按 trade_date 对齐，避免板块停牌导致位置错位）
        market_ret_by_date: Dict[Any, List[float]] = {}
        for market_code, market_df in market_sector_ohlcv.items():
            if market_df is None or "close" not in market_df.columns or len(market_df) < min_len:
                continue
            closes = market_df["close"].to_numpy(dtype=float, copy=False)
            dates = _dates(market_df)
            if dates is None:
                continue
            for i in range(1, len(closes)):
                prev = closes[i - 1]
                if prev > 0:
                    d = dates[i]
                    if pd.notna(d):
                        market_ret_by_date.setdefault(d, []).append(closes[i] / prev - 1.0)

        if not market_ret_by_date:
            return result

        for code, df in sector_ohlcv.items():
            if sector_codes is not None and code not in sector_codes:
                continue
            if df is None or "close" not in df.columns or len(df) < min_len:
                continue

            closes = df["close"].to_numpy(dtype=float, copy=False)
            dates = _dates(df)
            if dates is None:
                continue
            top_count = 0

            # 只统计末尾 window 日
            for day_idx in range(len(closes) - window, len(closes)):
                if day_idx < 1:
                    continue
                prev_close = closes[day_idx - 1]
                if prev_close <= 0:
                    continue
                day_ret = closes[day_idx] / prev_close - 1.0
                d = dates[day_idx]
                if pd.isna(d):
                    continue
                day_rets_all = market_ret_by_date.get(d, [])
                # 需要至少 10 个市场板块才能排名
                if len(day_rets_all) >= 10:
                    top_threshold = np.percentile(day_rets_all, 90)
                    if day_ret >= top_threshold:
                        top_count += 1

            result[code] = top_count

        return result

    @staticmethod
    @factor(
        FactorSpec(
            field="f25_volume_slope",
            name_en="volume_slope",
            name_cn="量能斜率",
            definition="成交额占比 MA 近窗线性回归斜率",
            inputs=("sector_ohlcv", "windows"),
            outputs=("Dict[window, Dict[sector_code, float]]",),
            layer="sector",
            default_polarity="positive",
            regimes=("momentum",),
            windows=_VOLUME_SLOPE_WINDOWS,
        )
    )
    def f25_volume_slope(
        sector_ohlcv: Dict[str, pd.DataFrame],
        windows: Tuple[int, ...] = _VOLUME_SLOPE_WINDOWS,
        sector_codes: set = None,
    ) -> Dict[int, Dict[str, float]]:
        """多窗口成交额占比 MA 斜率：面板只对齐一次，斜率用近 N 个 MA 点。"""
        empty = {w: {} for w in windows}
        if not sector_ohlcv or not windows:
            return empty

        min_need = min(windows) + _VOLUME_SLOPE_REG_WINDOW
        series_by_code: Dict[str, pd.Series] = {}
        for code, df in sector_ohlcv.items():
            if sector_codes is not None and code not in sector_codes:
                continue
            if df is None or "trade_date" not in df.columns:
                continue
            if "amount" in df.columns:
                vals = df["amount"].to_numpy(dtype=float, copy=False)
            elif "volume" in df.columns:
                # 兼容缺 amount 的旧数据
                vals = df["volume"].to_numpy(dtype=float, copy=False)
            else:
                continue
            if len(df) < min_need:
                continue
            dates = pd.to_datetime(df["trade_date"], errors="coerce")
            mask = ~pd.isna(dates)
            if not bool(mask.any()):
                continue
            series_by_code[code] = pd.Series(vals[mask.to_numpy()], index=dates[mask])

        if not series_by_code:
            return empty

        amt_df = pd.DataFrame(series_by_code).sort_index().fillna(0.0)
        total_amt = amt_df.sum(axis=1).to_numpy(dtype=float, copy=False)
        result: Dict[int, Dict[str, float]] = {w: {} for w in windows}
        reg_n = _VOLUME_SLOPE_REG_WINDOW

        for code in amt_df.columns:
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = np.where(
                    total_amt > 0,
                    amt_df[code].to_numpy(dtype=float, copy=False) / total_amt,
                    np.nan,
                )
            for w in windows:
                if len(ratio) < w + 1:
                    continue
                ma = (
                    pd.Series(ratio)
                    .rolling(window=w, min_periods=w)
                    .mean()
                    .to_numpy(dtype=float, copy=False)
                )
                valid = ma[~np.isnan(ma)]
                if len(valid) < 2:
                    continue
                # 与 MF1 一致：只用最近 reg_n 个 MA 点回归，避免全历史稀释
                recent = valid[-reg_n:]
                n = len(recent)
                x = np.arange(n, dtype=float)
                x_mean = x.mean()
                y_mean = recent.mean()
                denom = float(((x - x_mean) ** 2).sum()) or 1.0
                slope = float(((x - x_mean) * (recent - y_mean)).sum() / denom)
                if slope == slope:
                    result[w][code] = slope
        return result


    @staticmethod
    @factor(
        FactorSpec(
            field="f29_sector_breadth",
            name_en="sector_breadth",
            name_cn="板块宽度",
            definition="当日上涨家数 / 板块总家数",
            inputs=("sector_stocks", "stock_ohlcv"),
            outputs=("Dict[sector_code, float]",),
            layer="sector",
            default_polarity="positive",
            regimes=("momentum", "reversal"),
        )
    )
    def f29_sector_breadth(
        sector_stocks: Dict[str, List[str]],
        stock_ohlcv: Dict[str, pd.DataFrame],
        eligible_codes: Optional[set] = None,
    ) -> Dict[str, float]:
        """f29 板块上涨广度：当日上涨家数 / 板块总家数。

        真实"主线"应该是板块普涨，不是靠一两只股票拉上去的。
        上涨广度越高，说明板块行情越健康。

        Returns:
            {sector_code: breadth}，范围 [0, 1]
        """
        result: Dict[str, float] = {}
        for sector_code, codes in sector_stocks.items():
            members = [
                c for c in codes
                if eligible_codes is None or c in eligible_codes
            ]
            if not members:
                result[sector_code] = float("nan")
                continue

            rising = 0
            for c in members:
                df = stock_ohlcv.get(c)
                if df is None or df.empty or len(df) < 2:
                    continue
                closes = df["close"].to_numpy(dtype=float, copy=False)
                if len(closes) < 2:
                    continue
                cur, prev = closes[-1], closes[-2]
                if prev <= 0 or np.isnan(cur) or np.isnan(prev):
                    continue
                if cur > prev:
                    rising += 1

            result[sector_code] = rising / len(members)
        return result

    @staticmethod
    @factor(
        FactorSpec(
            field="f30_sector_concentration",
            name_en="sector_concentration",
            name_cn="板块集中度",
            definition="Top3 成交额 / 板块总成交额",
            inputs=("sector_stocks", "stock_ohlcv"),
            outputs=("Dict[sector_code, float]",),
            layer="sector",
            default_polarity="negative",
            regimes=("momentum", "reversal"),
        )
    )
    def f30_sector_concentration(
        sector_stocks: Dict[str, List[str]],
        stock_ohlcv: Dict[str, pd.DataFrame],
        eligible_codes: Optional[set] = None,
    ) -> Dict[str, float]:
        """f30 板块资金聚集度：Top3 成交额 / 板块总成交额。

        资金集中度反映板块热度是否聚焦。主线板块资金应相对集中（龙头领涨），
        但过度集中（>0.8）可能是"一家独大"的假主线。

        Returns:
            {sector_code: concentration}，范围 [0, 1]，最优区间 [0.3, 0.7]
        """
        result: Dict[str, float] = {}
        for sector_code, codes in sector_stocks.items():
            members = [
                c for c in codes
                if c in (stock_ohlcv or {})
                and (eligible_codes is None or c in eligible_codes)
            ]
            if not members:
                result[sector_code] = float("nan")
                continue

            amounts = []
            for c in members:
                df = stock_ohlcv.get(c)
                if df is None or df.empty:
                    continue
                if "amount" not in df.columns:
                    continue
                amt = float(df["amount"].iloc[-1])
                if amt > 0 and np.isfinite(amt):
                    amounts.append(amt)

            if not amounts:
                result[sector_code] = float("nan")
                continue

            amounts.sort(reverse=True)
            top3 = sum(amounts[:3])
            total = sum(amounts)
            result[sector_code] = top3 / max(total, 1.0)
        return result

    @staticmethod
    @factor(
        FactorSpec(
            field="f27_new_high_ratio",
            name_en="new_high_ratio",
            name_cn="新高占比",
            definition="创 N 日新高家数 / 板块总家数",
            inputs=("sector_stocks", "stock_ohlcv"),
            outputs=("Dict[sector_code, float]",),
            layer="sector",
            default_polarity="positive",
            regimes=("momentum",),
        )
    )
    def f27_new_high_ratio(
        sector_stocks: Dict[str, List[str]],
        stock_ohlcv: Dict[str, pd.DataFrame],
        window: int = 20,
        eligible_codes: Optional[set] = None,
    ) -> Dict[str, float]:
        """f27 板块新高率：创 N 日新高家数 / 板块总家数。

        板块内多只股票同时创新高，说明板块动量强劲。
        替换之前返回 NaN 的占位实现。

        Args:
            window: 新高窗口，默认 20 日

        Returns:
            {sector_code: new_high_ratio}，范围 [0, 1]
        """
        result: Dict[str, float] = {}
        for sector_code, codes in sector_stocks.items():
            members = [
                c for c in codes
                if eligible_codes is None or c in eligible_codes
            ]
            if not members:
                result[sector_code] = float("nan")
                continue

            new_high = 0
            for c in members:
                df = stock_ohlcv.get(c)
                if df is None or df.empty or len(df) < window + 1:
                    continue
                closes = df["close"].to_numpy(dtype=float, copy=False)
                if len(closes) < window + 1:
                    continue
                cur = closes[-1]
                if np.isnan(cur):
                    continue
                # 前 window 日（不含当日）的最高价；不足 window 根不把短窗口当成新高
                prev_high = np.nanmax(closes[-(window + 1):-1])
                if cur >= prev_high:
                    new_high += 1

            result[sector_code] = new_high / len(members)
        return result

