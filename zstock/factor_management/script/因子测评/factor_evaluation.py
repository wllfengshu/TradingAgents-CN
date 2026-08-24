"""
因子有效性测评 CLI - 一体化脚本 + 自定义时间范围

用法：
  # P0 默认：条件宇宙（Top3板块∩主板）+ 负IC因子取反 + 全层测评
  python -m zstock.factor_management.script.因子测评.factor_evaluation \\
      --start 2026-01-05 --end 2026-07-27 --period 5 --plot

  # 关闭条件宇宙 / 关闭极性取反（对照全市场原始 IC）
  python -m zstock.factor_management.script.因子测评.factor_evaluation \\
      --start 2026-01-05 --end 2026-07-27 --no-conditional --no-invert

  # 只测某一层
  python -m zstock.factor_management.script.因子测评.factor_evaluation \\
      --start 2026-01-05 --end 2026-07-27 --layer dragon

输出：
  因子测评/output/<timestamp>/summary.csv
  因子测评/output/<timestamp>/<factor>_analysis.png（可选 --plot）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import numpy as np
import pandas as pd
from scipy import stats

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

_STRATEGY_PARAMS_PATH = (
    PROJECT_ROOT / "zstock" / "common" / "config" / "strategy_params.json"
)


def _load_active_factors() -> dict:
    """从 strategy_params.json 加载 active_factors（sector/dragon/force 层因子配置）。

    条件宇宙构建必须与生产策略的板块选择逻辑一致，故复用同一份配置。
    """
    try:
        with open(_STRATEGY_PARAMS_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("active_factors", {}) or {}
    except Exception as e:
        logger.warning(f"⚠️ 加载 strategy_params.json 失败，条件宇宙降级为空: {e}")
        return {}

# 全量因子字段（MongoDB dragon/force 集合全部数值因子，含策略未使用的因子）
DEFAULT_STOCK_FACTORS: Dict[str, List[str]] = {
    "dragon": [
        # f31 超额收益（base + 多窗口）
        "f31_excess_return",
        "f31_excess_return_5d",
        "f31_excess_return_10d",
        "f31_excess_return_15d",
        "f31_excess_return_20d",
        "f31b_rps_percentile",
        "f32_amount",
        "f33_consecutive_boards",
        # f34 共振（base + 多窗口）
        "f34_resonance_pct",
        "f34_resonance_pct_3d",
        "f34_resonance_pct_5d",
        "f34_resonance_pct_10d",
        "f35_bollinger_pass",
        "f35_bollinger_trend",
        "f36_identity_premium",
        "f37_relative_strength",
        "f38_turnover_anomaly",
        "f39_pb",
        "f40_holder_change",
    ],
    "force": [
        "fcoop1_main_net_ratio",
        "fcoop2_main_retail_ratio",
        # fcoop3 持续天数（base + 多窗口）
        "fcoop3_sustained_days",
        "fcoop3_sustained_days_3d",
        "fcoop3_sustained_days_5d",
        "fcoop3_sustained_days_10d",
        "fcoop4_turnover_quality",
        "fcoop5_main_flow_acceleration",
        "fcoop6_main_force_aggression",
        "fcoop7_super_large_net_ratio",
        "fcoop8_main_flow_trend_5d",
        "dragon_consistency_5d",
        "f_power_divergence",
        "f_main_force_persistence",
        "f_mean_reversion_signal",
        "longhu_board_bonus",
    ],
}

DEFAULT_SECTOR_FACTORS: List[str] = [
    "f21_rps_10d",
    "f21_rps_20d",
    "f21_rps_60d",
    "f22_main_flow",
    "f23_limit_up_density",
    "f24_max_consecutive",
    "f25_volume_slope_3d",
    "f25_volume_slope_5d",
    "f25_volume_slope_10d",
    "f26_volume_growth_5d",
    "f26_volume_growth_20d",
    "f28_consistency",
    # P3 新增板块结构因子
    "f27_new_high_ratio",
    "f29_sector_breadth",
    "f30_sector_concentration",
]

INVERT_FIELDS_FOR_EVAL: Set[str] = {
    # 以下字段极性为负（与 strategy_params.json active_factors 的 polarity: negative 一致）
    "f34_resonance_pct_3d",
    "f34_resonance_pct_5d",
    "f34_resonance_pct_10d",
    # f30 板块资金聚集度 IC 为负，取反后正向
    "f30_sector_concentration",
    # f36 辨识度溢价 IC 为负，取反后正向
    "f36_identity_premium",
}

_SECTOR_RAW_FIELD_MAP: Dict[str, str] = {
    "f21_rps": "f21_rps_20d",
    "f22_main_flow": "f22_main_flow",
    "f23_limit_up_density": "f23_limit_up_density",
    "f24_max_consecutive": "f24_max_consecutive",
    "f25_volume_slope": "f25_volume_slope_5d",
    "f26_volume_growth": "f26_volume_growth_5d",
    "f28_consistency": "f28_consistency",
}


class FactorEvaluationPipeline:
    """一体化因子测评：数据加载、因子计算、图表生成。"""

    def __init__(
        self,
        start_date: str,
        end_date: str,
        period: int = 5,
        n_quantiles: int = 5,
        conditional: bool = True,
        top_sectors: int = 3,
        invert_negative: bool = True,
        plot: bool = False,
        output_dir: Optional[str] = None,
        decay_max: int = 0,
        layer: Optional[str] = None,
        field: Optional[str] = None,
        workers: Optional[int] = None,
    ) -> None:
        self.start_date = start_date
        self.end_date = end_date
        self.period = period
        self.n_quantiles = n_quantiles
        self.conditional = conditional
        self.top_sectors = top_sectors
        self.invert_negative = invert_negative
        self.plot = plot
        self.decay_max = decay_max
        self.layer = layer
        self.field = field
        from zstock.common.utils.resource_budget import (
            cap_worker_count,
            compute_resource_budget,
        )

        budget = compute_resource_budget()
        self.workers = cap_worker_count(workers, budget.compute_workers, name="workers")

        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            self.output_dir = (
                Path(__file__).resolve().parent
                / "output"
                / datetime.now().strftime("%Y%m%d_%H%M%S")
            )
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.db = None
        self.qs = None
        self._universe_cache: Dict[Tuple[str, str, int], Dict[str, Set[str]]] = {}
        self._mainboard_codes: Optional[Set[str]] = None
        self._sector_members: Optional[Dict[str, List[str]]] = None
        # 价格面板缓存：同一年/同一宇宙下所有因子共享同一份 open/close 面板，
        # 避免每个因子重复拉取全市场 OHLCV（原实现是 O(因子数) 次重载）
        self._price_panel_cache: Dict[Tuple[str, str, frozenset], dict] = {}
        self._sector_price_cache: Dict[Tuple[str, str, frozenset], dict] = {}
        self._price_panel_lock = asyncio.Lock()
        self._sector_price_lock = asyncio.Lock()

    async def _init_services(self) -> None:
        if self.db is not None:
            return
        from zstock.data_management.database_service import get_database_service
        from zstock.data_management.query_service import get_data_query_service

        self.db = get_database_service()
        self.qs = get_data_query_service()

    async def load_stock_factor_panel(
        self,
        collection: str,
        field: str,
        code_key: str = "code",
    ) -> pd.DataFrame:
        from zstock.common.utils.common_utils import normalize_date

        start = normalize_date(self.start_date)
        end = normalize_date(self.end_date)

        docs = await self.db.query(
            collection,
            {"trade_date": {"$gte": start, "$lte": end}},
            projection={"_id": 0, "trade_date": 1, code_key: 1, field: 1},
        )
        if not docs:
            logger.warning(f"{collection}.{field} 无数据")
            return pd.DataFrame()

        df = pd.DataFrame(docs)
        if field not in df.columns or code_key not in df.columns:
            return pd.DataFrame()

        df[field] = pd.to_numeric(df[field], errors="coerce")
        panel = (
            df.groupby(["trade_date", code_key], as_index=False)[field]
            .mean()
            .pivot(index="trade_date", columns=code_key, values=field)
            .sort_index()
        )
        logger.info(
            f"加载因子 {collection}.{field}: "
            f"{panel.shape[0]} 日 × {panel.shape[1]} 标的"
        )
        return panel

    async def load_sector_factor_panel(self, field: str) -> pd.DataFrame:
        from zstock.data_management.query_service import COL_FACTOR_SECTOR

        return await self.load_stock_factor_panel(
            COL_FACTOR_SECTOR, field, code_key="sector_code"
        )

    async def load_price_panel(
        self,
        codes: Sequence[str],
        extra_days: int = 40,
    ) -> pd.DataFrame:
        from zstock.common.utils.common_utils import normalize_date

        if not codes:
            return pd.DataFrame()

        start = normalize_date(self.start_date)
        end = normalize_date(self.end_date)
        end_ext = (
            datetime.strptime(end, "%Y-%m-%d") + timedelta(days=extra_days)
        ).strftime("%Y-%m-%d")

        cache_key = (start, end_ext, frozenset(codes))
        cached = self._price_panel_cache.get(cache_key)
        if cached is not None:
            return cached

        async with self._price_panel_lock:
            cached = self._price_panel_cache.get(cache_key)
            if cached is not None:
                return cached

            ohlcv_map = await self.qs.get_ohlcv_batch(list(codes), start, end_ext)
            # 使用 open+close 两个价格面板：
            # 因子在 T 日 close 产生信号 → T+1 日 open 买入 → T+p 日 close 卖出
            # forward_return(T) = close(T+p) / open(T+1) - 1
            panels = {}
            for col in ("open", "close"):
                frames = []
                for code, df in ohlcv_map.items():
                    if df is None or df.empty or col not in df.columns:
                        continue
                    tmp = df[["trade_date", col]].copy()
                    tmp["trade_date"] = tmp["trade_date"].astype(str)
                    tmp[col] = pd.to_numeric(tmp[col], errors="coerce")
                    tmp = tmp.rename(columns={col: code})
                    frames.append(tmp.set_index("trade_date")[[code]])
                if not frames:
                    print(f"  [警告] {col} 无有效 OHLCV 数据")
                    return None
                panels[col] = pd.concat(frames, axis=1).sort_index()

            logger.info(
                f"加载价格面板: {panels['close'].shape[0]} 日 × {panels['close'].shape[1]} 股票 (open+close)"
            )
            self._price_panel_cache[cache_key] = panels
            return panels

    async def load_sector_price_panel(
        self,
        sector_codes: Optional[Sequence[str]] = None,
        extra_days: int = 40,
    ) -> pd.DataFrame:
        from zstock.common.utils.common_utils import normalize_date

        start = normalize_date(self.start_date)
        end = normalize_date(self.end_date)
        end_ext = (
            datetime.strptime(end, "%Y-%m-%d") + timedelta(days=extra_days)
        ).strftime("%Y-%m-%d")

        cache_key = (start, end_ext, frozenset(sector_codes or []))
        cached = self._sector_price_cache.get(cache_key)
        if cached is not None:
            return cached

        async with self._sector_price_lock:
            cached = self._sector_price_cache.get(cache_key)
            if cached is not None:
                return cached

            sectors, _ = await self.qs.get_sector_list()
            sectors = sectors or []
            if sector_codes:
                sc_set = set(sector_codes)
                sectors = [s for s in sectors if s.get("sector_code") in sc_set]

            all_codes = sorted(
                {
                    c
                    for s in sectors
                    for c in (s.get("stocks") or [])
                    if c
                }
            )
            if not all_codes:
                return pd.DataFrame()

            stock_panels = await self.load_price_panel(all_codes, extra_days=0)
            if not stock_panels or not stock_panels.get("open", pd.DataFrame()).size:
                return {}

            # 同时构建 open 和 close 板块指数，匹配 forward_return = close(T+p)/open(T+1) - 1
            sector_panels: Dict[str, pd.DataFrame] = {}
            for col in ("open", "close"):
                stock_prices = stock_panels[col]
                stock_ret = stock_prices.pct_change(fill_method=None)
                sector_idx: Dict[str, pd.Series] = {}
                for s in sectors:
                    sc = s.get("sector_code")
                    members = [c for c in (s.get("stocks") or []) if c in stock_ret.columns]
                    if not members or not sc:
                        continue
                    sec_ret = stock_ret[members].mean(axis=1, skipna=True).fillna(0.0)
                    sector_idx[sc] = 100.0 * (1.0 + sec_ret).cumprod()
                if sector_idx:
                    sector_panels[col] = pd.DataFrame(sector_idx).sort_index()

            if not sector_panels:
                return {}
            logger.info(f"加载板块价格: {list(sector_panels.values())[0].shape[0]} 日 × {list(sector_panels.values())[0].shape[1]} 板块 (open+close)")
            self._sector_price_cache[cache_key] = sector_panels
            return sector_panels

    async def _ensure_universe_meta(self) -> None:
        if self._mainboard_codes is not None and self._sector_members is not None:
            return
        stocks, _ = await self.qs.get_all_stocks()
        self._mainboard_codes = {
            d["code"]
            for d in (stocks or [])
            if d.get("is_mainboard") and not d.get("is_st") and d.get("code")
        }
        sectors, _ = await self.qs.get_sector_list()
        self._sector_members = {
            s["sector_code"]: list(s.get("stocks") or [])
            for s in (sectors or [])
            if s.get("sector_code")
        }
        logger.info(
            f"宇宙元数据: 主板非ST={len(self._mainboard_codes)}  "
            f"板块={len(self._sector_members)}"
        )

    async def build_conditional_universe(self) -> Dict[str, Set[str]]:
        cache_key = (self.start_date, self.end_date, int(self.top_sectors))
        if cache_key in self._universe_cache:
            return self._universe_cache[cache_key]

        await self._ensure_universe_meta()

        from zstock.factor_management.sector_factors import SectorFactors

        # 板块选择逻辑与生产策略一致（配置驱动的 active_factors.sector）
        active_factors = _load_active_factors()

        panels: Dict[str, pd.DataFrame] = {}
        for raw_key, store_field in _SECTOR_RAW_FIELD_MAP.items():
            panels[raw_key] = await self.load_sector_factor_panel(store_field)

        all_dates: Set[str] = set()
        for p in panels.values():
            if not p.empty:
                all_dates.update(str(d) for d in p.index)
        dates = sorted(all_dates)

        universe: Dict[str, Set[str]] = {}
        for td in dates:
            raw: Dict[str, Dict[str, float]] = {}
            for raw_key, panel in panels.items():
                if panel.empty or td not in panel.index:
                    raw[raw_key] = {}
                    continue
                row = panel.loc[td]
                raw[raw_key] = {
                    str(sc): float(v)
                    for sc, v in row.items()
                    if v == v
                }
            scores = SectorFactors.scores_from_raw(
                raw, active_factors=active_factors, top_n=int(self.top_sectors)
            )
            if not scores:
                universe[td] = set()
                continue
            top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[: self.top_sectors]
            members: Set[str] = set()
            for sc, _ in top:
                for code in self._sector_members.get(sc, []):
                    if code in self._mainboard_codes:
                        members.add(code)
            universe[td] = members

        avg_n = (
            float(np.mean([len(v) for v in universe.values()])) if universe else 0.0
        )
        logger.info(
            f"条件宇宙 Top{self.top_sectors}: {len(universe)} 日, 日均标的≈{avg_n:.0f}"
        )
        self._universe_cache[cache_key] = universe
        return universe

    @staticmethod
    def apply_universe_mask(
        factor: pd.DataFrame,
        universe: Dict[str, Set[str]],
    ) -> pd.DataFrame:
        if factor.empty or not universe:
            return factor
        out = factor.copy()
        for td in out.index:
            allowed = universe.get(str(td))
            if not allowed:
                out.loc[td] = np.nan
                continue
            keep = [c for c in out.columns if c in allowed]
            drop = [c for c in out.columns if c not in allowed]
            if drop:
                out.loc[td, drop] = np.nan
        out = out.dropna(axis=1, how="all")
        return out

    @staticmethod
    def apply_polarity_invert(
        factor: pd.DataFrame,
        field: str,
        invert: bool,
    ) -> pd.DataFrame:
        if not invert or field not in INVERT_FIELDS_FOR_EVAL or factor.empty:
            return factor
        logger.info(f"极性取反: {field}")
        return -factor

    async def load_eval_bundle_stock(
        self,
        layer: str,
        field: str,
    ) -> Tuple[pd.DataFrame, dict]:
        from zstock.data_management.query_service import (
            COL_FACTOR_DRAGON,
            COL_FACTOR_FORCE,
        )

        col_map = {"dragon": COL_FACTOR_DRAGON, "force": COL_FACTOR_FORCE}
        if layer not in col_map:
            raise ValueError(f"未知个股层: {layer}")

        factor = await self.load_stock_factor_panel(col_map[layer], field)
        if factor.empty:
            return factor, {}

        if self.conditional:
            universe = await self.build_conditional_universe()
            factor = self.apply_universe_mask(factor, universe)

        factor = self.apply_polarity_invert(factor, field, self.invert_negative)

        if factor.empty or factor.dropna(how="all").empty:
            return factor, {}

        codes = list(factor.columns)
        price_panels = await self.load_price_panel(codes)
        if not price_panels:
            return factor, {}
        common_cols = factor.columns.intersection(price_panels["close"].columns)
        return factor[common_cols], {k: v[common_cols] for k, v in price_panels.items()}

    async def load_eval_bundle_sector(
        self,
        field: str,
    ) -> Tuple[pd.DataFrame, dict]:
        factor = await self.load_sector_factor_panel(field)
        if factor.empty:
            return factor, {}
        factor = self.apply_polarity_invert(factor, field, self.invert_negative)
        price_panels = await self.load_sector_price_panel(sector_codes=list(factor.columns))
        if not price_panels:
            return factor, {}
        common_cols = factor.columns.intersection(price_panels["open"].columns)
        return factor[common_cols], {k: v[common_cols] for k, v in price_panels.items()}

    def _calc_ic_series(
        self, factor_data: pd.DataFrame, price_panels: dict, period: int = None
    ) -> pd.DataFrame:
        p = period if period is not None else self.period
        # forward_return(T) = close(T+p) / open(T+1) - 1
        # close.shift(-p) 取 T+p 日 close；open.shift(-1) 取 T+1 日 open
        forward_returns = price_panels["close"].shift(-p) / price_panels["open"].shift(-1) - 1

        rows = []
        for date in factor_data.index:
            if date not in forward_returns.index:
                continue
            factor = factor_data.loc[date].dropna()
            ret = forward_returns.loc[date].dropna()
            common = factor.index.intersection(ret.index)
            if len(common) < 10:
                continue
            f = factor[common].astype(float)
            r = ret[common].astype(float)
            mask = np.isfinite(f.values) & np.isfinite(r.values)
            if mask.sum() < 10:
                continue
            f, r = f[mask], r[mask]
            if f.nunique() < 2 or r.nunique() < 2:
                continue
            ic, _ = stats.pearsonr(f, r)
            rank_ic, _ = stats.spearmanr(f, r)
            if np.isfinite(ic) and np.isfinite(rank_ic):
                rows.append({"date": date, "IC": float(ic), "Rank_IC": float(rank_ic)})

        if not rows:
            return pd.DataFrame(columns=["IC", "Rank_IC"])
        return pd.DataFrame(rows).set_index("date")

    def _calc_ic_summary(self, ic_series: pd.DataFrame) -> pd.Series:
        if ic_series is None or ic_series.empty:
            return pd.Series(dtype=float)

        ic = ic_series["IC"].dropna()
        ric = ic_series["Rank_IC"].dropna()
        ic_std = float(ic.std()) if len(ic) > 1 else np.nan
        ric_std = float(ric.std()) if len(ric) > 1 else np.nan
        ic_mean = float(ic.mean()) if len(ic) else np.nan
        ric_mean = float(ric.mean()) if len(ric) else np.nan

        t_stat, p_val = (np.nan, np.nan)
        if len(ic) >= 3:
            t_stat, p_val = stats.ttest_1samp(ic, 0.0)

        return pd.Series(
            {
                "IC_Mean": ic_mean,
                "IC_Std": ic_std,
                "ICIR": (ic_mean / ic_std) if ic_std and ic_std > 0 else np.nan,
                "IC_Positive_Ratio": float((ic > 0).mean()) if len(ic) else np.nan,
                "Rank_IC_Mean": ric_mean,
                "Rank_IC_Std": ric_std,
                "Rank_ICIR": (ric_mean / ric_std) if ric_std and ric_std > 0 else np.nan,
                "IC_t_stat": float(t_stat) if t_stat == t_stat else np.nan,
                "IC_p_value": float(p_val) if p_val == p_val else np.nan,
                "N_Periods": int(len(ic)),
            }
        )

    def _calc_quantile_returns(
        self, factor_data: pd.DataFrame, price_panels: dict
    ) -> pd.DataFrame:
        # forward_return(T) = close(T+p) / open(T+1) - 1
        forward_returns = price_panels["close"].shift(-self.period) / price_panels["open"].shift(-1) - 1
        buckets: Dict[int, List[float]] = {i: [] for i in range(1, self.n_quantiles + 1)}

        for date in factor_data.index:
            if date not in forward_returns.index:
                continue
            factor = factor_data.loc[date].dropna()
            ret = forward_returns.loc[date].dropna()
            common = factor.index.intersection(ret.index)
            if len(common) < self.n_quantiles * 5:
                continue
            f = factor[common].astype(float)
            r = ret[common].astype(float)
            mask = np.isfinite(f.values) & np.isfinite(r.values)
            f, r = f[mask], r[mask]
            if len(f) < self.n_quantiles * 5 or f.nunique() < self.n_quantiles:
                continue
            try:
                quantiles = pd.qcut(f, self.n_quantiles, labels=False, duplicates="drop") + 1
            except ValueError:
                continue
            for q in range(1, self.n_quantiles + 1):
                m = quantiles == q
                if m.any():
                    buckets[q].append(float(r[m].mean()))

        min_len = min((len(v) for v in buckets.values()), default=0)
        if min_len == 0:
            return pd.DataFrame(
                columns=[f"Q{q}" for q in range(1, self.n_quantiles + 1)]
            )
        return pd.DataFrame(
            {f"Q{q}": buckets[q][:min_len] for q in range(1, self.n_quantiles + 1)}
        )

    def _calc_long_short_return(self, quantile_returns_df: pd.DataFrame) -> Dict:
        if quantile_returns_df is None or quantile_returns_df.empty:
            return {}
        cols = list(quantile_returns_df.columns)
        if len(cols) < 2:
            return {}
        ls = quantile_returns_df[cols[-1]] - quantile_returns_df[cols[0]]
        std = float(ls.std()) if len(ls) > 1 else np.nan
        mean = float(ls.mean()) if len(ls) else np.nan
        return {
            "LS_Mean_Return": mean,
            "LS_Std_Return": std,
            "LS_Sharpe": (
                (mean / std * np.sqrt(252)) if std and std > 0 else np.nan
            ),
            "LS_Win_Rate": float((ls > 0).mean()) if len(ls) else np.nan,
            "LS_Max_Drawdown": float(self._max_drawdown(ls.cumsum())),
        }

    def _calc_factor_autocorr(
        self, factor_data: pd.DataFrame, lag: int = 1
    ) -> Dict:
        dates = sorted(factor_data.index)
        vals = []
        for i in range(lag, len(dates)):
            curr = factor_data.loc[dates[i]].dropna()
            prev = factor_data.loc[dates[i - lag]].dropna()
            common = curr.index.intersection(prev.index)
            if len(common) < 10:
                continue
            a, b = curr[common].astype(float), prev[common].astype(float)
            mask = np.isfinite(a.values) & np.isfinite(b.values)
            if mask.sum() < 10 or a[mask].nunique() < 2:
                continue
            corr, _ = stats.spearmanr(a[mask], b[mask])
            if np.isfinite(corr):
                vals.append(float(corr))
        return {
            "mean_autocorr": float(np.mean(vals)) if vals else np.nan,
            "autocorr_series": vals,
        }

    def _comprehensive_score(self, ic_series: pd.DataFrame) -> Dict:
        summary = self._calc_ic_summary(ic_series)
        if summary.empty:
            return {"Total_Score": 0, "Grade": "D - 无效因子", "N_Periods": 0}

        scores: Dict = {}
        ic_mean = abs(float(summary.get("IC_Mean", 0) or 0))
        if ic_mean >= 0.10:
            scores["IC_Score"] = 25
        elif ic_mean >= 0.05:
            scores["IC_Score"] = 15
        elif ic_mean >= 0.02:
            scores["IC_Score"] = 8
        else:
            scores["IC_Score"] = 0

        icir = abs(float(summary.get("Rank_ICIR", 0) or 0))
        if icir >= 2.0:
            scores["ICIR_Score"] = 25
        elif icir >= 1.0:
            scores["ICIR_Score"] = 15
        elif icir >= 0.5:
            scores["ICIR_Score"] = 8
        else:
            scores["ICIR_Score"] = 0

        win_rate = float(summary.get("IC_Positive_Ratio", 0.5) or 0.5)
        if float(summary.get("IC_Mean", 0) or 0) < 0:
            win_rate = 1.0 - win_rate
        if win_rate >= 0.60:
            scores["WinRate_Score"] = 25
        elif win_rate >= 0.55:
            scores["WinRate_Score"] = 15
        elif win_rate >= 0.50:
            scores["WinRate_Score"] = 8
        else:
            scores["WinRate_Score"] = 0

        p_value = float(summary.get("IC_p_value", 1.0) or 1.0)
        if p_value <= 0.01:
            scores["Significance_Score"] = 25
        elif p_value <= 0.05:
            scores["Significance_Score"] = 15
        elif p_value <= 0.10:
            scores["Significance_Score"] = 8
        else:
            scores["Significance_Score"] = 0

        total = sum(scores.values())
        scores["Total_Score"] = total
        scores["Grade"] = self._get_grade(total)
        scores["N_Periods"] = int(summary.get("N_Periods", 0) or 0)
        scores["IC_Mean"] = float(summary.get("IC_Mean", np.nan))
        scores["Rank_IC_Mean"] = float(summary.get("Rank_IC_Mean", np.nan))
        scores["Rank_ICIR"] = float(summary.get("Rank_ICIR", np.nan))
        return scores

    def _evaluate(
        self, factor_data: pd.DataFrame, price_panels: dict
    ) -> Dict:
        ic_series = self._calc_ic_series(factor_data, price_panels)
        summary = self._calc_ic_summary(ic_series)
        qret = self._calc_quantile_returns(factor_data, price_panels)
        ls = self._calc_long_short_return(qret)
        autocorr = self._calc_factor_autocorr(factor_data, lag=1)
        score = self._comprehensive_score(ic_series)
        return {
            "ic_series": ic_series,
            "ic_summary": summary,
            "quantile_returns": qret,
            "long_short": ls,
            "autocorr": autocorr,
            "score": score,
        }

    @staticmethod
    def _max_drawdown(cumulative_returns: pd.Series) -> float:
        if cumulative_returns is None or len(cumulative_returns) == 0:
            return 0.0
        rolling_max = cumulative_returns.cummax()
        drawdown = cumulative_returns - rolling_max
        return float(drawdown.min())

    @staticmethod
    def _get_grade(score: float) -> str:
        if score >= 80:
            return "A - 优秀因子"
        if score >= 60:
            return "B - 良好因子"
        if score >= 40:
            return "C - 一般因子"
        return "D - 无效因子"

    def _plot_factor_analysis(
        self,
        ic_series: pd.DataFrame,
        quantile_returns: pd.DataFrame,
        title: str = "",
        save_path: Optional[str] = None,
    ) -> None:
        plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "Arial Unicode MS"]
        plt.rcParams["axes.unicode_minus"] = False

        fig, axes = plt.subplots(2, 2, figsize=(15, 10))
        fig.suptitle(title or "因子有效性分析", fontsize=14)

        ax1 = axes[0, 0]
        if not ic_series.empty and "Rank_IC" in ic_series.columns:
            ic_series["Rank_IC"].plot(ax=ax1, alpha=0.7, label="Rank IC")
            if len(ic_series) >= 5:
                ic_series["Rank_IC"].rolling(12, min_periods=3).mean().plot(
                    ax=ax1, color="red", linewidth=2, label="12期均值"
                )
            ax1.axhline(y=0, color="black", linestyle="--")
            ax1.set_title("Rank IC 时间序列")
            ax1.legend()

        ax2 = axes[0, 1]
        if not ic_series.empty and "Rank_IC" in ic_series.columns:
            ic_series["Rank_IC"].hist(bins=30, ax=ax2, edgecolor="black")
            ax2.axvline(x=0, color="red", linestyle="--")
            ax2.set_title(f'IC分布 (均值={ic_series["Rank_IC"].mean():.4f})')

        ax3 = axes[1, 0]
        if not quantile_returns.empty:
            for col in quantile_returns.columns:
                quantile_returns[col].cumsum().plot(ax=ax3, label=col)
            ax3.set_title("各分层累计收益")
            ax3.legend()

        ax4 = axes[1, 1]
        if not quantile_returns.empty:
            quantile_returns.mean().plot(kind="bar", ax=ax4, color="steelblue")
            ax4.set_title("各分层平均收益")
            ax4.axhline(y=0, color="red", linestyle="--")

        plt.tight_layout()
        try:
            if save_path:
                Path(save_path).parent.mkdir(parents=True, exist_ok=True)
                fig.savefig(save_path, dpi=150)
        finally:
            plt.close(fig)

    def _get_jobs(self) -> List[Tuple[str, str]]:
        if self.field:
            layer = self.layer or "dragon"
            return [(layer, self.field)]

        jobs: List[Tuple[str, str]] = []
        layers = [self.layer] if self.layer else ["sector", "dragon", "force"]
        for layer in layers:
            if layer == "sector":
                jobs.extend(("sector", f) for f in DEFAULT_SECTOR_FACTORS)
            elif layer in DEFAULT_STOCK_FACTORS:
                jobs.extend((layer, f) for f in DEFAULT_STOCK_FACTORS[layer])
        return jobs

    async def _prewarm_shared_data(self, jobs: List[Tuple[str, str]]) -> None:
        """并发测评前预热共享缓存，避免多协程重复拉取宇宙/价格面板。"""
        await self._ensure_universe_meta()

        has_stock = any(layer != "sector" for layer, _ in jobs)
        if has_stock and self.conditional:
            universe = await self.build_conditional_universe()
            if universe:
                all_codes = sorted({c for codes in universe.values() for c in codes})
                if all_codes:
                    logger.info(f"预热条件宇宙价格面板: {len(all_codes)} 只股票")
                    await self.load_price_panel(all_codes)

        sector_jobs = [field for layer, field in jobs if layer == "sector"]
        if sector_jobs:
            sector_codes: Set[str] = set()
            for field in sector_jobs:
                panel = await self.load_sector_factor_panel(field)
                if not panel.empty:
                    sector_codes.update(str(c) for c in panel.columns)
            if sector_codes:
                logger.info(f"预热板块价格面板: {len(sector_codes)} 个板块")
                await self.load_sector_price_panel(sector_codes=sorted(sector_codes))

    async def _eval_one(
        self,
        layer: str,
        field: str,
        plot_lock: Optional[asyncio.Lock] = None,
    ) -> Dict:
        tag = []
        if layer != "sector" and self.conditional:
            tag.append(f"condTop{self.top_sectors}")
        if self.invert_negative and field in INVERT_FIELDS_FOR_EVAL:
            tag.append("inv")
        tag_s = f" [{' '.join(tag)}]" if tag else ""
        logger.info(f"\n===== 测评 {layer}.{field}{tag_s} =====")

        try:
            if layer == "sector":
                factor, price_panels = await self.load_eval_bundle_sector(field)
            else:
                factor, price_panels = await self.load_eval_bundle_stock(layer, field)

            if factor.empty or not price_panels:
                logger.warning(f"跳过 {layer}.{field}: 无因子或价格数据")
                return {
                    "layer": layer,
                    "field": field,
                    "conditional": bool(self.conditional and layer != "sector"),
                    "polarity": -1 if (self.invert_negative and field in INVERT_FIELDS_FOR_EVAL) else 1,
                    "Grade": "N/A",
                    "Total_Score": 0,
                    "error": "no_data",
                }

            # 用 close（stock）或 open（sector）的日期索引对齐
            ref_key = "close" if "close" in price_panels else "open"
            common_dates = factor.index.intersection(price_panels[ref_key].index)
            factor = factor.loc[common_dates]
            price_panels = {k: v.loc[v.index >= common_dates.min()] for k, v in price_panels.items()}

            result = self._evaluate(factor, price_panels)
            score = result["score"]
            summary = result["ic_summary"]
            ls = result["long_short"]
            autocorr = result["autocorr"]

            polarity = (
                -1
                if (
                    self.invert_negative
                    and field in INVERT_FIELDS_FOR_EVAL
                )
                else 1
            )
            row = {
                "layer": layer,
                "field": field,
                "period": self.period,
                "conditional": bool(self.conditional and layer != "sector"),
                "top_sectors": int(self.top_sectors) if (self.conditional and layer != "sector") else 0,
                "polarity": polarity,
                "N_Periods": score.get("N_Periods", 0),
                "IC_Mean": score.get("IC_Mean"),
                "Rank_IC_Mean": score.get("Rank_IC_Mean"),
                "Rank_ICIR": score.get("Rank_ICIR"),
                "IC_Positive_Ratio": (
                    float(summary.get("IC_Positive_Ratio")) if not summary.empty else None
                ),
                "IC_p_value": float(summary.get("IC_p_value")) if not summary.empty else None,
                "LS_Mean_Return": ls.get("LS_Mean_Return"),
                "LS_Sharpe": ls.get("LS_Sharpe"),
                "LS_Win_Rate": ls.get("LS_Win_Rate"),
                "mean_autocorr": autocorr.get("mean_autocorr"),
                "IC_Score": score.get("IC_Score"),
                "ICIR_Score": score.get("ICIR_Score"),
                "WinRate_Score": score.get("WinRate_Score"),
                "Significance_Score": score.get("Significance_Score"),
                "Total_Score": score.get("Total_Score"),
                "Grade": score.get("Grade"),
            }

            ric_mean = row.get("Rank_IC_Mean")
            if ric_mean is not None and ric_mean == ric_mean:
                logger.info(
                    f"  RankIC={ric_mean:.4f}  RankICIR={row['Rank_ICIR']:.3f}  "
                    f"Score={row['Total_Score']}  {row['Grade']}"
                )
            else:
                logger.info(f"  Score={row['Total_Score']}  {row['Grade']}")

            if self.plot:
                png = self.output_dir / f"{layer}_{field}_p{self.period}.png"
                if plot_lock is not None:
                    async with plot_lock:
                        self._plot_factor_analysis(
                            result["ic_series"],
                            result["quantile_returns"],
                            title=f"{layer}.{field} (period={self.period})",
                            save_path=str(png),
                        )
                else:
                    self._plot_factor_analysis(
                        result["ic_series"],
                        result["quantile_returns"],
                        title=f"{layer}.{field} (period={self.period})",
                        save_path=str(png),
                    )
                logger.info(f"  图已保存: {png.name}")

            if self.decay_max > 0:
                decay_rows = []
                for period in range(1, self.decay_max + 1):
                    ic_series = self._calc_ic_series(factor, price_panels, period=period)
                    summary_p = self._calc_ic_summary(ic_series)
                    decay_rows.append({
                        "period": period,
                        "IC_Mean": summary_p.get("IC_Mean", np.nan),
                        "Rank_IC_Mean": summary_p.get("Rank_IC_Mean", np.nan),
                        "Rank_ICIR": summary_p.get("Rank_ICIR", np.nan),
                        "N_Periods": summary_p.get("N_Periods", 0),
                    })
                decay_df = pd.DataFrame(decay_rows).set_index("period")
                decay_df.to_csv(self.output_dir / f"{layer}_{field}_decay.csv")

            return row
        except Exception as e:
            logger.error(f"{layer}.{field} 测评失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {
                "layer": layer,
                "field": field,
                "Grade": "ERROR",
                "Total_Score": 0,
                "error": str(e),
            }

    async def run(self) -> List[Dict]:
        await self._init_services()

        logger.info(
            f"测评配置: start={self.start_date} end={self.end_date} "
            f"conditional={self.conditional} top_sectors={self.top_sectors} "
            f"invert_negative={self.invert_negative} workers={self.workers}"
        )

        jobs = self._get_jobs()
        await self._prewarm_shared_data(jobs)

        sem = asyncio.Semaphore(self.workers)
        plot_lock = asyncio.Lock() if self.plot else None

        async def _run_job(layer: str, field: str) -> Dict:
            async with sem:
                return await self._eval_one(layer, field, plot_lock=plot_lock)

        rows = list(await asyncio.gather(*[_run_job(layer, field) for layer, field in jobs]))

        summary = pd.DataFrame(rows)
        csv_path = self.output_dir / "summary.csv"
        summary.to_csv(csv_path, index=False, encoding="utf-8-sig")
        logger.info(f"\n汇总已写入: {csv_path}")

        if not summary.empty and "Total_Score" in summary.columns:
            show_cols = [
                c
                for c in [
                    "layer",
                    "field",
                    "Rank_IC_Mean",
                    "Rank_ICIR",
                    "Total_Score",
                    "Grade",
                ]
                if c in summary.columns
            ]
            logger.info("\n" + summary[show_cols].to_string(index=False))

        return rows


def _year_eval_window(year: int, today: Optional[str] = None) -> Tuple[str, str]:
    """自然年测评区间（与 overnight_validation 一致）。"""
    y = int(year)
    start = f"{y}-01-02" if y == 2025 else f"{y}-01-01"
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")
    end = min(today, f"{y}-12-31")
    return start, end


async def run_batch_years(
    years: Sequence[int],
    *,
    period: int = 5,
    n_quantiles: int = 5,
    conditional: bool = True,
    top_sectors: int = 3,
    invert_negative: bool = True,
    plot: bool = False,
    output_root: Optional[str] = None,
    decay_max: int = 0,
    layer: Optional[str] = None,
    field: Optional[str] = None,
    workers: Optional[int] = None,
    year_parallel: int = 8,
) -> Dict[int, Path]:
    """批量测评多个自然年；year_parallel 控制年份级并发。"""
    batch_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = Path(output_root) if output_root else (
        Path(__file__).resolve().parent / "output" / f"eval_batch_{batch_ts}"
    )
    root.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    year_sem = asyncio.Semaphore(max(1, int(year_parallel)))
    results: Dict[int, Path] = {}

    async def _run_year(year: int) -> None:
        start, end = _year_eval_window(year, today=today)
        label = f"{year}" if year != datetime.now().year else f"{year}_YTD"
        out_dir = root / label
        async with year_sem:
            logger.info(f"\n{'=' * 60}\n批量测评 {label}: {start} → {end}\n{'=' * 60}")
            pipeline = FactorEvaluationPipeline(
                start_date=start,
                end_date=end,
                period=period,
                n_quantiles=n_quantiles,
                conditional=conditional,
                top_sectors=top_sectors,
                invert_negative=invert_negative,
                plot=plot,
                output_dir=str(out_dir),
                decay_max=decay_max,
                layer=layer,
                field=field,
                workers=workers,
            )
            await pipeline.run()
            results[year] = out_dir / "summary.csv"

    await asyncio.gather(*[_run_year(y) for y in years])
    logger.info(f"\n批量测评完成，输出根目录: {root}")
    for year, path in sorted(results.items()):
        logger.info(f"  {year}: {path}")
    return results


async def async_main(args: argparse.Namespace) -> int:
    try:
        from app.core import database as db_module

        await db_module.db_manager.init_mongodb()
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")
        return 1

    if args.years:
        years = [int(y.strip()) for y in args.years.split(",") if y.strip()]
        await run_batch_years(
            years,
            period=args.period,
            n_quantiles=args.quantiles,
            conditional=bool(args.conditional),
            top_sectors=int(args.top_sectors),
            invert_negative=bool(args.invert),
            plot=args.plot,
            output_root=args.output,
            decay_max=int(args.decay_max or 0),
            layer=args.layer,
            field=args.field,
            workers=args.workers,
            year_parallel=int(args.year_parallel),
        )
        return 0

    if not args.start or not args.end:
        logger.error("请指定 --start/--end，或使用 --years 批量测评")
        return 1

    evaluator = FactorEvaluationPipeline(
        start_date=args.start,
        end_date=args.end,
        period=args.period,
        n_quantiles=args.quantiles,
        conditional=bool(args.conditional),
        top_sectors=int(args.top_sectors),
        invert_negative=bool(args.invert),
        plot=args.plot,
        output_dir=args.output,
        decay_max=int(args.decay_max or 0),
        layer=args.layer,
        field=args.field,
        workers=args.workers,
    )
    await evaluator.run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="zstock 因子有效性测评")
    p.add_argument("--start", default=None, help="开始日期 YYYY-MM-DD")
    p.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD")
    p.add_argument(
        "--years",
        default=None,
        help="批量测评自然年，逗号分隔，如 2024,2025,2026（与 --start/--end 互斥）",
    )
    p.add_argument(
        "--year-parallel",
        type=int,
        default=8,
        help="年份级并发数（--years 模式），默认 8",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=None,
        help="因子级并发协程数，默认按 CPU/内存资源预算",
    )
    p.add_argument(
        "--layer",
        choices=["sector", "dragon", "force"],
        default=None,
        help="只测某一层；默认三层全测",
    )
    p.add_argument("--field", default=None, help="只测单个因子字段")
    p.add_argument("--period", type=int, default=5, help="预测周期（交易日），默认 5")
    p.add_argument("--quantiles", type=int, default=5, help="分层数，默认 5")
    p.add_argument(
        "--decay-max",
        type=int,
        default=0,
        help="若 >0，额外输出 1..N 的 IC 衰减 csv",
    )
    p.add_argument("--plot", action="store_true", help="保存分析图")
    p.add_argument("--output", default=None, help="输出目录")
    p.add_argument(
        "--conditional",
        dest="conditional",
        action="store_true",
        default=True,
        help="个股层用条件宇宙（Top板块∩主板非ST），默认开启",
    )
    p.add_argument(
        "--no-conditional",
        dest="conditional",
        action="store_false",
        help="关闭条件宇宙，全市场测评",
    )
    p.add_argument(
        "--top-sectors",
        type=int,
        default=3,
        help="条件宇宙 Top-N 板块，默认 3",
    )
    p.add_argument(
        "--invert",
        dest="invert",
        action="store_true",
        default=True,
        help="对负 IC 因子取反后再测（与 INVERT_FIELDS_FOR_EVAL 一致），默认开启",
    )
    p.add_argument(
        "--no-invert",
        dest="invert",
        action="store_false",
        help="关闭负IC因子取反",
    )
    return p


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )
    args = build_parser().parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    sys.exit(main())
