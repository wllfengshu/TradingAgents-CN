"""
主线板块层因子计算模块（M2）

黑盒设计：
- 公开接口：calculate_all_sector_factors() / *_raw() / scores_from_raw()
- 所有实现细节都隐藏在私有方法中

因子权重（非等权）：
  F2.1 RPS 0.30 / F2.2 资金流 0.20 / F2.3 涨停浓度 0.30 /
  F2.4 连板高度 0.10 / F2.5 成交占比斜率 0.10
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from zstock.common.utils.common_utils import (
    ensure_ohlcv_sorted,
    limit_up_threshold,
    ohlcv_asof,
)

logger = logging.getLogger(__name__)

_W_F21 = 0.30   # F2.1 板块RPS
_W_F22 = 0.20   # F2.2 板块资金净流入
_W_F23 = 0.30   # F2.3 涨停浓度
_W_F24 = 0.10   # F2.4 连板高度
_W_F25 = 0.10   # F2.5 成交占比斜率

_RPS_WINDOWS = (10, 20, 60)
_VOLUME_SLOPE_WINDOWS = (3, 5, 10)
_DEFAULT_RPS_WINDOW = 20
_DEFAULT_VOLUME_SLOPE_WINDOW = 5
# F2.5：对最近 N 个 MA 点做斜率（与 MF1 slope_window 对齐）
_VOLUME_SLOPE_REG_WINDOW = 5


class SectorFactors:
    """板块层因子计算器。黑盒设计，所有实现隐藏，只暴露统一入口"""

    # ===================== 公开接口 =====================

    @staticmethod
    def calculate_all_sector_factors_raw(
        sectors: List[Dict],
        sector_stocks: Dict[str, List[str]],
        stock_ohlcv: Dict[str, pd.DataFrame],
        stock_flow_recent: Optional[Dict[str, List[Dict]]] = None,
        assume_sorted: bool = False,
        sector_ohlcv: Optional[Dict[str, pd.DataFrame]] = None,
        trade_date: Optional[str] = None,
        eligible_codes: Optional[set] = None,
    ) -> Dict[str, Dict]:
        """【公开接口】返回 M2 子因子原始值（归一化前）。

        Args:
            sector_ohlcv: 可选预聚合板块 OHLCV；传入则跳过昂贵的个股→板块聚合
            trade_date: 截面日；提供时个股 OHLCV 要求末行恰好为该日
            eligible_codes: 主板非 ST 等可交易宇宙；F2.2/F2.3/F2.4 只统计该集合
        """
        if not assume_sorted:
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
        }
        for w, mp in f21_by_window.items():
            out[f"f21_rps_{w}d"] = mp
        for w, mp in f25_by_window.items():
            out[f"f25_volume_slope_{w}d"] = mp
        out["f21_rps"] = f21_by_window[_DEFAULT_RPS_WINDOW]
        out["f25_volume_slope"] = f25_by_window[_DEFAULT_VOLUME_SLOPE_WINDOW]

        logger.info(
            f"✅ M2 原始因子收集完成: {len(out['f21_rps'])} 个板块 "
            f"(RPS windows={_RPS_WINDOWS})"
        )
        return out

    @staticmethod
    def scores_from_raw(raw: Dict[str, Dict]) -> Dict[str, float]:
        """从 raw 原始值做 min-max + 加权合成，得到 M2 得分。"""
        if not raw:
            return {}
        f21 = raw.get("f21_rps") or {}
        f22 = raw.get("f22_main_flow") or {}
        f23 = raw.get("f23_limit_up_density") or {}
        f24 = raw.get("f24_max_consecutive") or {}
        f25 = raw.get("f25_volume_slope") or {}

        return SectorFactors._combine_five_factors(
            SectorFactors._minmax_normalize(f21),
            SectorFactors._minmax_normalize(f22),
            SectorFactors._minmax_normalize(f23),
            SectorFactors._minmax_normalize(
                {k: float(v) for k, v in f24.items()}
            ),
            SectorFactors._minmax_normalize(f25),
        )

    @staticmethod
    def calculate_all_sector_factors(
        sectors: List[Dict],
        sector_stocks: Dict[str, List[str]],
        stock_ohlcv: Dict[str, pd.DataFrame],
        stock_flow_recent: Optional[Dict[str, List[Dict]]] = None,
        trade_date: Optional[str] = None,
        eligible_codes: Optional[set] = None,
    ) -> Dict[str, float]:
        """【公开接口】完整计算M2，内部复用 raw 路径避免重复聚合。"""
        raw = SectorFactors.calculate_all_sector_factors_raw(
            sectors,
            sector_stocks,
            stock_ohlcv,
            stock_flow_recent,
            trade_date=trade_date,
            eligible_codes=eligible_codes,
        )
        m2_scores = SectorFactors.scores_from_raw(raw)
        avg = np.mean(list(m2_scores.values())) if m2_scores else 0.0
        logger.info(f"✅ M2 完整计算完成: {len(m2_scores)} 个板块，平均分 {avg:.2f}")
        return m2_scores

    # ===================== 私有方法 =====================

    @staticmethod
    def _collect_sector_rps(
        sector_ohlcv: Dict[str, pd.DataFrame],
        window: int = 20,
        sector_codes: set = None,
    ) -> Dict[str, float]:
        """【私有】收集板块RPS原始值。"""
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
    def _compute_limit_up_from_ohlcv(
        stock_ohlcv: Dict[str, pd.DataFrame],
    ) -> Dict[str, bool]:
        """【私有】从 OHLCV 计算当日涨停标志（按板块阈值）。"""
        result = {}
        for code, df in stock_ohlcv.items():
            if df is None or len(df) < 2 or "close" not in df.columns:
                result[code] = False
                continue
            closes = df["close"].astype(float).values
            prev_close = closes[-2]
            if prev_close <= 0:
                result[code] = False
                continue
            daily_return = closes[-1] / prev_close - 1.0
            result[code] = daily_return >= limit_up_threshold(code)
        return result

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
    def _compute_consecutive_boards_from_ohlcv(
        stock_ohlcv: Dict[str, pd.DataFrame],
    ) -> Dict[str, int]:
        """【私有】从 OHLCV 计算最近连续涨停天数（按板块阈值）。"""
        result = {}
        for code, df in stock_ohlcv.items():
            if df is None or len(df) < 2 or "close" not in df.columns:
                continue
            closes = df["close"].astype(float).values
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
                        main_sum += float(today_doc.get("main_net", 0.0) or 0.0) * 10000.0
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
    def _collect_volume_ratio_slope(
        sector_ohlcv: Dict[str, pd.DataFrame],
        ma_window: int = 5,
        sector_codes: set = None,
    ) -> Dict[str, float]:
        """【私有】板块成交占比 MA 斜率（单窗口，兼容旧调用）。"""
        multi = SectorFactors._collect_volume_ratio_slopes_multi(
            sector_ohlcv, windows=(ma_window,), sector_codes=sector_codes
        )
        return multi.get(ma_window, {})

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
        rps_scores: Dict[str, float],
        capital_flow_scores: Dict[str, float],
        limit_up_scores: Dict[str, float],
        consecutive_boards_scores: Dict[str, float],
        volume_slope_scores: Dict[str, float],
    ) -> Dict[str, float]:
        """
        【私有】加权合成5个因子为M2得分。

        必须同时具备 F2.1 与 F2.5；其余因子缺失时按可用权重重归一化。
        """
        valid_sectors = set(rps_scores.keys()) & set(volume_slope_scores.keys())
        if not valid_sectors:
            logger.error("⚠️ M2 合成失败：没有同时具备 F2.1+F2.5 的板块（OHLCV 数据不足）")
            return {}

        result = {}
        for sector_code in valid_sectors:
            scores = [rps_scores[sector_code], volume_slope_scores[sector_code]]
            weights = [_W_F21, _W_F25]
            if sector_code in capital_flow_scores:
                scores.append(capital_flow_scores[sector_code])
                weights.append(_W_F22)
            if sector_code in limit_up_scores:
                scores.append(limit_up_scores[sector_code])
                weights.append(_W_F23)
            if sector_code in consecutive_boards_scores:
                scores.append(consecutive_boards_scores[sector_code])
                weights.append(_W_F24)
            result[sector_code] = sum(s * w for s, w in zip(scores, weights)) / sum(weights)
        return result
