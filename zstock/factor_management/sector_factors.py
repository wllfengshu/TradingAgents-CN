"""
主线板块层因子计算模块（M2）

黑盒设计：
- 公开接口：calculate_all_sector_factors_raw() / scores_from_raw()
- 所有实现细节都隐藏在私有方法中
- MongoDB 只存储原始因子值，不存储得分（得分由 scores_from_raw 实时计算）

因子权重/极性由 strategy_params.json active_factors.sector 提供（单一事实来源），
模块内不再硬编码任何因子权重。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from zstock.common.utils.common_utils import (
    ensure_ohlcv_sorted,
    limit_up_threshold,
    minmax_normalize,
    ohlcv_asof,
    WAN_TO_YUAN,
)

logger = logging.getLogger(__name__)

_RPS_WINDOWS = (10, 20, 60)
_VOLUME_SLOPE_WINDOWS = (3, 5, 10)
_DEFAULT_RPS_WINDOW = 20
_DEFAULT_VOLUME_SLOPE_WINDOW = 5
# F2.5：对最近 N 个 MA 点做斜率（与 MF1 slope_window 对齐）
_VOLUME_SLOPE_REG_WINDOW = 5
# F2.6：成交额 5 日增长窗口（多窗口支持）
_VOLUME_SHARE_INCREASE_WINDOWS = (5, 20)
_DEFAULT_VOLUME_SHARE_INCREASE_WINDOW = 5
# F2.8：10 日持续性 φ 门槛 - 近 10 日排名 Top 10% 的天数 ≥ 2
# _CONSISTENCY_WINDOW: 回溯天数 = 10
# _CONSISTENCY_TOP_PCT: Top 百分位 = 10%（排名前10%的板块）
# _CONSISTENCY_THRESHOLD: 达标天数阈值 = 2 天（近10日中至少2天在Top10%）
# 从3降到2：原值过严，大多数交易日无板块通过φ门槛，导致选股管线空转
_CONSISTENCY_WINDOW = 10
_CONSISTENCY_TOP_PCT = 0.10
_CONSISTENCY_THRESHOLD = 2

class SectorFactors:
    """板块层因子计算器。黑盒设计，所有实现隐藏，只暴露统一入口"""

    # ===================== 公开接口 =====================

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
            raise ValueError(
                "market_sector_ohlcv 不能为 None！F2.8 持续性必须基于全市场排名计算。"
                "请传入完整的全市场板块 OHLCV 数据。"
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
                )
            )
        else:
            # 资金流仍按当日切片重算（便宜）；OHLCV 用预聚合
            sector_ohlcv = {
                k: v for k, v in sector_ohlcv.items() if k in need_codes
            }
            _, sector_capital_flow = SectorFactors._aggregate_sectors_from_stocks(
                sector_stocks_use,
                stock_ohlcv,
                stock_flow_recent or {},
                ohlcv_only=False,
                flow_only=True,
                eligible_codes=eligible_codes,
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
            w: SectorFactors._collect_sector_rps(
                sector_ohlcv, window=w, sector_codes=valid_sector_codes
            )
            for w in _RPS_WINDOWS
        }
        # 成交占比面板只建一次，多窗口复用
        f25_by_window = SectorFactors._collect_volume_ratio_slopes_multi(
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
            f26_by_window[w] = SectorFactors._calculate_volume_share_increase_raw(
                sector_ohlcv,
                short_window=w,
                long_window=long_w,
                sector_codes=valid_sector_codes,
            )
        # F2.8 10 日持续性（φ 门槛）- 基于全市场排名
        f28_consistency = SectorFactors._calculate_10day_consistency_raw(
            sector_ohlcv,
            market_sector_ohlcv=market_sector_ohlcv,  # 必需使用全市场排名
            window=_CONSISTENCY_WINDOW,
            sector_codes=valid_sector_codes,
        )

        # 板块结构因子（P3 新增）
        f29_breadth = SectorFactors._calculate_sector_breadth(
            sector_stocks_use, stock_ohlcv, eligible_codes=eligible_codes
        )
        f30_concentration = SectorFactors._calculate_sector_concentration(
            sector_stocks_use, stock_ohlcv, eligible_codes=eligible_codes
        )
        f27_new_high_ratio = SectorFactors._calculate_sector_new_high_ratio(
            sector_stocks_use, stock_ohlcv, window=20, eligible_codes=eligible_codes
        )

        f22_main_flow = SectorFactors._collect_sector_capital_flow(
            {k: v for k, v in sector_capital_flow.items() if k in valid_sector_codes}
        )
        f23_limit_up_density = SectorFactors._collect_limit_up_densities(
            sectors_valid,
            sector_stocks,
            all_stocks_limit_up,
            eligible_codes=eligible_codes,
        )
        f24_max_consecutive = SectorFactors._collect_consecutive_boards_max(
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
    def scores_from_raw(raw: Dict[str, Dict], regime: str = "neutral", active_factors: list = None, top_n: int = 3) -> Dict[str, float]:
        """从 raw 原始值做 min-max + 加权合成，得到 M2 得分及 Top N 选股。

        配置驱动：从 active_factors.sector 按 regime 读取因子列表/权重/极性。

        Args:
            top_n: 选出的主线板块数量（对齐 config sector_layer.top_sectors）。
        """
        if not raw:
            return {}

        # 读取规范字段名（默认窗口或明确指定的窗口）
        f21 = raw.get("f21_rps_20d") or raw.get("f21_rps") or {}
        f26 = raw.get("f26_volume_growth_5d") or raw.get("f26_volume_growth") or {}
        f23 = raw.get("f23_limit_up_density") or {}

        # Step1 φ 门槛过滤（只要求 F2.3>0）
        candidate_sectors = set()
        for sector_code in f21.keys():
            if f23.get(sector_code, 0.0) > 0:
                candidate_sectors.add(sector_code)
        # 如果 f21 为空，用 f23 兜底
        if not candidate_sectors:
            for sector_code, val in f23.items():
                if val > 0:
                    candidate_sectors.add(sector_code)

        if not candidate_sectors:
            logger.warning("M2 选股 φ 门槛：无符合条件板块（F2.3>0）")
            return {}

        # Step2 α 合成 — 配置驱动
        if not active_factors:
            logger.warning("⚠️ 板块打分未收到 active_factors 配置，返回空")
            return {}
        sector_cfg = active_factors.get("sector", {})
        factor_configs = sector_cfg.get(regime, sector_cfg.get("momentum", []))
        if not factor_configs:
            return {}
        scores = SectorFactors._combine_from_config(raw, factor_configs, candidate_sectors)

        # Step3 选股 Top N
        if not scores:
            logger.warning("M2 α 合成：无法生成评分")
            return {}

        return SectorFactors._select_top_sectors(scores, f26, top_n=top_n)

    # ===================== 私有方法 =====================

    @staticmethod
    def _combine_from_config(
        raw: Dict[str, Dict],
        factor_configs: list,
        candidate_sectors: set,
    ) -> Dict[str, float]:
        """【通用】配置驱动的因子组合。

        factor_configs: [{"field": str, "weight": float, "polarity": "positive"|"negative"}]
        自动处理归一化、极性反转、缺失因子跳过。
        """
        # 收集各因子在候选板块上的归一化值
        normalized = {}  # field -> {sector_code: norm_value}
        for fc in factor_configs:
            field = fc["field"]
            # 支持带后缀和不带后缀的字段名
            values = raw.get(field) or {}
            filtered = {k: v for k, v in values.items() if k in candidate_sectors and v is not None}
            if not filtered:
                continue
            norm = minmax_normalize(filtered)
            if fc.get("polarity") == "negative":
                norm = {k: 100 - v for k, v in norm.items()}
            normalized[field] = (norm, fc["weight"])

        if not normalized:
            return {}

        # 加权组合：每个板块按其实际可用因子动态再归一化（缺失因子不稀释也不补 0）
        all_sectors = set()
        for norm, _ in normalized.values():
            all_sectors.update(norm.keys())

        result = {}
        for sector_code in all_sectors:
            available = [
                (norm[sector_code], w)
                for norm, w in normalized.values()
                if sector_code in norm
            ]
            if not available:
                continue
            total_w = sum(w for _, w in available)
            result[sector_code] = (
                sum(v * (w / total_w) for v, w in available) if total_w > 0 else 50.0
            )

        return result

    @staticmethod
    def _select_top_sectors(
        sector_scores: Dict[str, float],
        f26_tiebreaker: Dict[str, float],
        top_n: int = 3,
    ) -> Dict[str, float]:
        """【私有】从候选板块中选 Top N，并列时用 F2.6 作为优先级。"""
        if not sector_scores:
            return {}

        # 排序：先按总分降序，再按 F2.6 降序
        sorted_sectors = sorted(
            sector_scores.items(),
            key=lambda x: (
                -x[1],  # 总分降序
                -f26_tiebreaker.get(x[0], 0.0),  # F2.6 降序（作为 tiebreaker）
            ),
        )

        # 保留 Top N，但如果有并列则全部保留（暂简单取 Top N）
        return {code: score for code, score in sorted_sectors[:top_n]}

    @staticmethod
    def _collect_sector_rps(
        sector_ohlcv: Dict[str, pd.DataFrame],
        window: int = None,
        sector_codes: set = None,
    ) -> Dict[str, float]:
        """【私有】收集板块RPS原始值（支持多窗口）。"""
        if window is None:
            window = _DEFAULT_RPS_WINDOW
        sector_rps_scores = {}
        for sector_code, df in sector_ohlcv.items():
            if sector_codes is not None and sector_code not in sector_codes:
                continue
            if len(df) >= window + 1 and "close" in df.columns:
                base = float(df["close"].iloc[-window - 1])
                last = float(df["close"].iloc[-1])
                if base <= 0 or np.isnan(base) or np.isnan(last):
                    continue
                sector_rps_scores[sector_code] = last / base - 1
        return sector_rps_scores

    @staticmethod
    def _collect_sector_capital_flow(
        sector_capital_flow: Dict[str, Dict[str, float]],
    ) -> Dict[str, float]:
        """【私有】收集板块资金流原始值（主力净流入）。"""
        return {
            sector_code: float(flow_info.get("main_flow", 0.0))
            for sector_code, flow_info in sector_capital_flow.items()
        }

    @staticmethod
    def _compute_limit_and_consecutive_from_ohlcv(
        stock_ohlcv: Dict[str, pd.DataFrame],
        codes: Optional[set] = None,
    ) -> Tuple[Dict[str, bool], Dict[str, int]]:
        """【私有】一次遍历同时计算当日涨停标志和最近连续涨停天数。"""
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
            thr = limit_up_threshold(code)
            prev_close = closes[-2]
            if prev_close <= 0:
                limit_up_result[code] = False
            else:
                limit_up_result[code] = (closes[-1] / prev_close - 1.0) >= thr

            count = 0
            for i in range(len(closes) - 1, 0, -1):
                prev = closes[i - 1]
                if prev <= 0:
                    break
                if closes[i] / prev - 1.0 >= thr:
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
        ohlcv_only: bool = False,
        flow_only: bool = False,
        eligible_codes: Optional[set] = None,
    ) -> Tuple[Dict[str, pd.DataFrame], Dict[str, Dict[str, float]]]:
        """【私有】从个股聚合板块 OHLCV + 资金流（numpy 累加，避免 pandas concat）。

        板块 OHLCV 聚合用全部有行情成分（反映板块指数）；
        资金流 F2.2 在提供 eligible_codes 时只累加主板宇宙。
        """
        sector_ohlcv: Dict[str, pd.DataFrame] = {}
        sector_capital_flow: Dict[str, Dict[str, float]] = {}

        # 个股面板只建一次，供所有板块复用
        stock_panel: Dict[str, Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}
        if not flow_only:
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

            if not flow_only:
                vol_sum: Dict[Any, float] = {}
                amt_sum: Dict[Any, float] = {}
                ret_sum: Dict[Any, float] = {}
                ret_cnt: Dict[Any, int] = {}
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
                            ret_cnt[d] = ret_cnt.get(d, 0) + 1

                if not ret_sum:
                    continue

                # 与旧逻辑一致：ret 与 vol/amt 按日期 inner（以有 ret 的日期为主，且需有 vol）
                common = sorted(d for d in ret_sum if d in vol_sum)
                if not common:
                    continue
                rets = np.array(
                    [
                        (ret_sum[d] / ret_cnt[d]) if ret_cnt.get(d) else 0.0
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

            if not ohlcv_only:
                main_sum = 0.0
                total_amt = 0.0
                for c in flow_members:
                    rows = (stock_flow_recent or {}).get(c) or []
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
    def _collect_limit_up_densities(
        sectors: List[Dict],
        sector_stocks: Dict[str, List[str]],
        all_stocks_limit_up: Dict[str, bool],
        eligible_codes: Optional[set] = None,
    ) -> Dict[str, float]:
        """【私有】涨停浓度 = 涨停家数 / 成分数（无 OHLCV 视为非涨停）。

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
    def _collect_consecutive_boards_max(
        sectors: List[Dict],
        sector_stocks: Dict[str, List[str]],
        all_stocks_consecutive_boards: Dict[str, int],
        eligible_codes: Optional[set] = None,
    ) -> Dict[str, int]:
        """【私有】收集连板高度原始值（可选只计 eligible 宇宙）。"""
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
    def _calculate_volume_share_increase_raw(
        sector_ohlcv: Dict[str, pd.DataFrame],
        short_window: int = None,
        long_window: int = None,
        sector_codes: Optional[set] = None,
    ) -> Dict[str, float]:
        """【私有】F2.6 成交额增长：MA(short_window) / MA(long_window) - 1（支持多窗口）。

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
    def _calculate_10day_consistency_raw(
        sector_ohlcv: Dict[str, pd.DataFrame],
        market_sector_ohlcv: Optional[Dict[str, pd.DataFrame]] = None,
        window: int = 10,
        sector_codes: Optional[set] = None,
    ) -> Dict[str, int]:
        """【私有】F2.8 10 日持续性：近 N 日排名 Top 10% 的天数。

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
    def _collect_volume_ratio_slopes_multi(
        sector_ohlcv: Dict[str, pd.DataFrame],
        windows: Tuple[int, ...] = _VOLUME_SLOPE_WINDOWS,
        sector_codes: set = None,
    ) -> Dict[int, Dict[str, float]]:
        """【私有】多窗口成交额占比 MA 斜率：面板只对齐一次，斜率用近 N 个 MA 点。"""
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

    # ==================== 板块结构因子（P3 新增） ====================

    @staticmethod
    def _calculate_sector_breadth(
        sector_stocks: Dict[str, List[str]],
        stock_ohlcv: Dict[str, pd.DataFrame],
        eligible_codes: Optional[set] = None,
    ) -> Dict[str, float]:
        """【私有】f29 板块上涨广度：当日上涨家数 / 板块总家数。

        真实"主线"应该是板块普涨，不是靠一两只股票拉上去的。
        上涨广度越高，说明板块行情越健康。

        Returns:
            {sector_code: breadth}，范围 [0, 1]
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

            rising = 0
            valid = 0
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
                valid += 1
                if cur > prev:
                    rising += 1

            result[sector_code] = rising / max(valid, 1)
        return result

    @staticmethod
    def _calculate_sector_concentration(
        sector_stocks: Dict[str, List[str]],
        stock_ohlcv: Dict[str, pd.DataFrame],
        eligible_codes: Optional[set] = None,
    ) -> Dict[str, float]:
        """【私有】f30 板块资金聚集度：Top3 成交额 / 板块总成交额。

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
    def _calculate_sector_new_high_ratio(
        sector_stocks: Dict[str, List[str]],
        stock_ohlcv: Dict[str, pd.DataFrame],
        window: int = 20,
        eligible_codes: Optional[set] = None,
    ) -> Dict[str, float]:
        """【私有】f27 板块新高率：创 N 日新高家数 / 板块总家数。

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
                if c in (stock_ohlcv or {})
                and (eligible_codes is None or c in eligible_codes)
            ]
            if not members:
                result[sector_code] = float("nan")
                continue

            new_high = 0
            valid = 0
            for c in members:
                df = stock_ohlcv.get(c)
                if df is None or df.empty or len(df) < window:
                    continue
                closes = df["close"].to_numpy(dtype=float, copy=False)
                if len(closes) < window:
                    continue
                cur = closes[-1]
                if np.isnan(cur):
                    continue
                # 前 window 日（不含当日）的最高价
                prev_high = np.nanmax(closes[-(window + 1):-1])
                valid += 1
                if cur >= prev_high:
                    new_high += 1

            result[sector_code] = new_high / max(valid, 1)
        return result

