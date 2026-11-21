"""
因子有效性测评（单类 FactorEvaluation）

用法：
  python -m zstock.factor_management.script.factor_evaluation \
      --start 2026-01-05 --end 2026-07-27 --period 5 --plot

  python -m zstock.factor_management.script.factor_evaluation \
      --years 2024,2025,2026

  python -m zstock.factor_management.script.factor_evaluation \
      --command compare --years 2024,2025,2026

  python -m zstock.factor_management.script.factor_evaluation \
      --command report --compare-dir <compare_eval_dir>
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

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

# 全量因子字段（MongoDB 数值因子；与策略 active_factors 无关）
def _default_factors_by_layer() -> Dict[str, List[str]]:
    """测评默认字段：从 FactorRegistry 展开（含 catalog=False 的测评字段）。"""
    from zstock.common.entity.factor_spec import FactorRegistry
    from zstock.factor_management.registry_bootstrap import ensure_factors_loaded

    ensure_factors_loaded()
    return {
        "sector": FactorRegistry.fields_for_layer("sector", eval_only=True),
        "dragon": FactorRegistry.fields_for_layer("dragon", eval_only=True),
        "force": FactorRegistry.fields_for_layer("force", eval_only=True),
    }


DEFAULT_STOCK_FACTORS: Dict[str, List[str]] = {}
DEFAULT_SECTOR_FACTORS: List[str] = []


def _refresh_eval_factor_defaults() -> None:
    by_layer = _default_factors_by_layer()
    DEFAULT_STOCK_FACTORS.clear()
    DEFAULT_STOCK_FACTORS["dragon"] = by_layer["dragon"]
    DEFAULT_STOCK_FACTORS["force"] = by_layer["force"]
    DEFAULT_SECTOR_FACTORS.clear()
    DEFAULT_SECTOR_FACTORS.extend(by_layer["sector"])


_refresh_eval_factor_defaults()

INVERT_FIELDS_FOR_EVAL: Set[str] = {
    # 窗口变体：配置里通常只写默认窗口
    "f34_resonance_pct_3d",
    "f34_resonance_pct_5d",
    "f34_resonance_pct_10d",
    "f30_sector_concentration",
    "f36_identity_premium",
}


def _invert_fields_from_registry() -> Set[str]:
    """负极性字段：硬编码补充 + FactorRegistry.default_polarity。"""
    from zstock.common.entity.factor_spec import FactorRegistry
    from zstock.factor_management.registry_bootstrap import ensure_factors_loaded

    ensure_factors_loaded()
    fields = set(INVERT_FIELDS_FOR_EVAL)
    for row in FactorRegistry.expand_rows():
        if row.get("default_polarity") == "negative" and row.get("field"):
            fields.add(str(row["field"]))
    return fields


INVERT_FIELDS_FOR_EVAL = _invert_fields_from_registry()


class FactorEvaluation:
    """因子测评：单窗 IC、多年批量、多口径对照、报告。"""

    def __init__(
        self,
        start_date: str,
        end_date: str,
        period: int = 5,
        n_quantiles: int = 5,
        universe_by_date: Optional[Dict[str, Set[str]]] = None,
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
        # 外部注入的按日宇宙；None=全市场。不做策略加权选股。
        self.universe_by_date = universe_by_date
        self.conditional = universe_by_date is not None
        self.invert_negative = invert_negative
        self.plot = plot
        self.decay_max = decay_max
        self.layer = layer
        self.field = field
        import os

        default_workers = max(1, (os.cpu_count() or 8) // 2)
        self.workers = max(1, int(workers)) if workers is not None else default_workers

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
        # 价格面板缓存：同一年/同一宇宙下所有因子共享同一份 open/close 面板，
        # 避免每个因子重复拉取全市场 OHLCV（原实现是 O(因子数) 次重载）
        self._price_panel_cache: Dict[Tuple[str, str, frozenset], dict] = {}
        self._sector_price_cache: Dict[Tuple[str, str, frozenset], dict] = {}
        self._price_panel_lock = asyncio.Lock()
        self._sector_price_lock = asyncio.Lock()

    async def _init_services(self) -> None:
        if self.db is not None:
            return
        from zstock.data_management.query_service import get_data_query_service

        self.db = get_data_query_service()
        self.qs = self.db

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
            projection={"_id": 0, "trade_date": 1, code_key: 1, field: 1, "sector_code": 1},
        )
        if not docs:
            logger.warning(f"{collection}.{field} 无数据")
            return pd.DataFrame()

        df = pd.DataFrame(docs)
        if field not in df.columns or code_key not in df.columns:
            return pd.DataFrame()

        df[field] = pd.to_numeric(df[field], errors="coerce")
        if "sector_code" in df.columns and code_key != "sector_code":
            # 同一只股票在多个板块各有一条龙头/合力记录，取均值，不按板块代码排序留第一条。
            df = (
                df.groupby(["trade_date", code_key], as_index=False)[field]
                .mean()
            )
            panel = df.pivot(
                index="trade_date", columns=code_key, values=field
            ).sort_index()
        else:
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

            stock_panels = await self.load_price_panel(all_codes, extra_days=extra_days)
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
                    member_ret = stock_ret[members]
                    # 停牌不从分母里消失，否则一只涨停会变成整块板块的收益。
                    sec_ret = member_ret.sum(axis=1, min_count=1) / max(len(members), 1)
                    sec_ret = sec_ret.fillna(0.0)
                    sector_idx[sc] = 100.0 * (1.0 + sec_ret).cumprod()
                if sector_idx:
                    sector_panels[col] = pd.DataFrame(sector_idx).sort_index()

            if not sector_panels:
                return {}
            logger.info(f"加载板块价格: {list(sector_panels.values())[0].shape[0]} 日 × {list(sector_panels.values())[0].shape[1]} 板块 (open+close)")
            self._sector_price_cache[cache_key] = sector_panels
            return sector_panels

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

        if self.universe_by_date:
            factor = self.apply_universe_mask(factor, self.universe_by_date)

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
        hold = max(int(getattr(self, "period", 1) or 1), 1)
        # 持有期重叠时，把有效样本缩成 n/持有期，避免 t 值和 ICIR 偏大。
        if len(ic) >= 3 and ic_std and ic_std > 0:
            n_eff = max(len(ic) / hold, 1.0)
            t_stat = float(ic_mean / (ic_std / np.sqrt(n_eff)))
            p_val = float(2 * stats.t.sf(abs(t_stat), max(n_eff - 1.0, 1.0)))
        if ic_std and ic_std > 0:
            ic_std = float(ic_std * np.sqrt(hold))
        if ric_std and ric_std > 0:
            ric_std = float(ric_std * np.sqrt(hold))

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
        rows: List[Dict[str, float]] = []
        q_cols = [f"Q{q}" for q in range(1, self.n_quantiles + 1)]

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
            unique_q = set(int(x) for x in quantiles.dropna().tolist())
            if unique_q != set(range(1, self.n_quantiles + 1)):
                continue
            row = {}
            for q in range(1, self.n_quantiles + 1):
                m = quantiles == q
                row[f"Q{q}"] = float(r[m].mean()) if m.any() else np.nan
            rows.append(row)

        if not rows:
            return pd.DataFrame(columns=q_cols)
        return pd.DataFrame(rows)

    def _calc_long_short_return(self, quantile_returns_df: pd.DataFrame) -> Dict:
        if quantile_returns_df is None or quantile_returns_df.empty:
            return {}
        cols = list(quantile_returns_df.columns)
        if len(cols) < 2:
            return {}
        ls = quantile_returns_df[cols[-1]] - quantile_returns_df[cols[0]]
        std = float(ls.std()) if len(ls) > 1 else np.nan
        mean = float(ls.mean()) if len(ls) else np.nan
        ann = np.sqrt(252.0 / max(int(self.period), 1))
        return {
            "LS_Mean_Return": mean,
            "LS_Std_Return": std,
            "LS_Sharpe": (
                (mean / std * ann) if std and std > 0 else np.nan
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
        """并发测评前预热共享缓存，避免多协程重复拉取价格面板。"""
        has_stock = any(layer != "sector" for layer, _ in jobs)
        if has_stock and self.universe_by_date:
            all_codes = sorted(
                {c for codes in self.universe_by_date.values() for c in codes}
            )
            if all_codes:
                logger.info(f"预热注入宇宙价格面板: {len(all_codes)} 只股票")
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
        if layer != "sector" and self.universe_by_date:
            tag.append("univInjected")
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
                "universe_injected": bool(self.universe_by_date and layer != "sector"),
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

    async def run(self, on_progress=None) -> List[Dict]:
        """执行测评。

        on_progress: 可选异步回调
            await on_progress(row=dict, done=int, total=int, rows=list)
        每完成一个因子调用一次，便于 Web 过程中落库。
        """
        await self._init_services()

        logger.info(
            f"测评配置: start={self.start_date} end={self.end_date} "
            f"conditional={self.conditional} universe_injected={bool(self.universe_by_date)} "
            f"invert_negative={self.invert_negative} workers={self.workers}"
        )

        jobs = self._get_jobs()
        await self._prewarm_shared_data(jobs)

        sem = asyncio.Semaphore(self.workers)
        plot_lock = asyncio.Lock() if self.plot else None
        rows_acc: List[Dict] = []
        total = len(jobs)
        progress_lock = asyncio.Lock()

        async def _run_job(layer: str, field: str) -> Dict:
            async with sem:
                row = await self._eval_one(layer, field, plot_lock=plot_lock)
                async with progress_lock:
                    rows_acc.append(row)
                    done = len(rows_acc)
                    snapshot = list(rows_acc)
                if on_progress is not None:
                    try:
                        await on_progress(
                            row=row, done=done, total=total, rows=snapshot
                        )
                    except Exception as e:
                        logger.warning("on_progress 回调失败: %s", e)
                return row

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


    # ── 多年 / 对照 / 报告 ─────────────────────────────────────────

    COMPARE_MODES = [
        ("full_p5", 5),
        ("full_p10", 10),
        ("full_p20", 20),
    ]
    COMPARE_YEARS = [2024, 2025, 2026]
    HISTORICAL_A_FACTORS = [
        "dragon.f33_consecutive_boards",
        "dragon.f36_identity_premium",
        "dragon.f34_resonance_pct",
        "dragon.f34_resonance_pct_3d",
        "dragon.f34_resonance_pct_5d",
        "dragon.f34_resonance_pct_10d",
        "dragon.f35_bollinger_trend",
        "force.f_mean_reversion_signal",
    ]
    REPORT_YEARS = ["2024", "2025", "2026"]
    REPORT_YEAR_LABELS = ["2024", "2025", "2026_YTD"]
    REPORT_WINDOWS = {
        "2024": "2024-01-01 ~ 2024-12-31",
        "2025": "2025-01-02 ~ 2025-12-31",
        "2026": "2026-01-01 ~ 2026-07-27",
    }
    REPORT_SOURCES = {
        "2024": Path(__file__).resolve().parent / "output/eval_batch_20260823_160640/2024/summary.csv",
        "2025": Path(__file__).resolve().parent / "output/eval_batch_20260823_160640/2025/summary.csv",
        "2026": Path(__file__).resolve().parent / "output/eval_2026_YTD_fixed/summary.csv",
    }
    REPORT_SOURCE_NOTES = {
        "2024": "output/eval_batch_20260823_160640/2024",
        "2025": "output/eval_batch_20260823_160640/2025",
        "2026": "output/eval_2026_YTD_fixed（turnover_rate 修复后重算）",
    }
    DEFAULT_COMPARE_ROOT = (
        Path(__file__).resolve().parent / "output/compare_eval_20260823_174008"
    )

    @staticmethod
    def grade_letter(g) -> str:
        if g is None or (isinstance(g, float) and pd.isna(g)):
            return "N/A"
        s = str(g)
        if " - " in s:
            return s.split(" - ")[0].strip()
        if s.startswith("N/A"):
            return "N/A"
        return s[:1] if s else "N/A"

    @staticmethod
    def year_eval_window(year: int, today: Optional[str] = None) -> Tuple[str, str]:
        """自然年测评区间（与 overnight_backtest 一致）。"""
        y = int(year)
        start = f"{y}-01-02" if y == 2025 else f"{y}-01-01"
        if today is None:
            today = datetime.now().strftime("%Y-%m-%d")
        end = min(today, f"{y}-12-31")
        return start, end

    @classmethod
    async def run_batch_years(
        cls,
        years: Sequence[int],
        *,
        period: int = 5,
        n_quantiles: int = 5,
        universe_by_date: Optional[Dict[str, Set[str]]] = None,
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
            start, end = cls.year_eval_window(year, today=today)
            label = f"{year}" if year != datetime.now().year else f"{year}_YTD"
            out_dir = root / label
            async with year_sem:
                logger.info(
                    f"\n{'=' * 60}\n批量测评 {label}: {start} → {end}\n{'=' * 60}"
                )
                FE = getattr(sys.modules[cls.__module__], "FactorEvaluation")
                pipeline = FE(
                    start_date=start,
                    end_date=end,
                    period=period,
                    n_quantiles=n_quantiles,
                    universe_by_date=universe_by_date,
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

    @classmethod
    async def run_compare(
        cls,
        *,
        modes: Optional[Sequence[Tuple[str, int]]] = None,
        years: Optional[Sequence[int]] = None,
        workers: Optional[int] = None,
        job_parallel: int = 8,
        output_root: Optional[Path] = None,
    ) -> Path:
        """多口径 × 多年批量对照测评。"""
        modes = list(modes) if modes is not None else list(cls.COMPARE_MODES)
        years = list(years) if years is not None else list(cls.COMPARE_YEARS)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        root = output_root or (
            Path(__file__).resolve().parent / "output" / f"compare_eval_{ts}"
        )
        root.mkdir(parents=True, exist_ok=True)
        FE = getattr(sys.modules[cls.__module__], "FactorEvaluation")

        jobs: List[Tuple[str, int, str, str, Path]] = []
        for item in modes:
            # 兼容 (mode, period) 与历史 (mode, period, flag)
            mode, period = item[0], int(item[1])
            for year in years:
                start, end = cls.year_eval_window(year)
                ylabel = str(year) if year != datetime.now().year else f"{year}_YTD"
                out_dir = root / mode / ylabel
                jobs.append((mode, period, start, end, out_dir))

        sem = asyncio.Semaphore(max(1, int(job_parallel)))
        done: Dict[str, Path] = {}

        async def _one(job: Tuple[str, int, str, str, Path]) -> None:
            mode, period, start, end, out_dir = job
            key = f"{mode}/{out_dir.name}"
            async with sem:
                logger.info(
                    "\n%s\n[任务] %s | %s → %s | period=%d\n%s",
                    "=" * 60,
                    key,
                    start,
                    end,
                    period,
                    "=" * 60,
                )
                out_dir.mkdir(parents=True, exist_ok=True)
                pipeline = FE(
                    start_date=start,
                    end_date=end,
                    period=period,
                    invert_negative=True,
                    plot=False,
                    output_dir=str(out_dir),
                    workers=workers,
                )
                await pipeline.run()
                done[key] = out_dir / "summary.csv"

        await asyncio.gather(*[_one(j) for j in jobs])
        report_path = cls.write_compare_report(root, done)
        logger.info("对照测评完成: %s", root)
        logger.info("报告: %s", report_path)
        return report_path

    @classmethod
    def write_compare_report(cls, root: Path, done: Dict[str, Path]) -> Path:
        """生成历史 A 因子 × 多口径 × 三年对照表。"""
        rows: List[dict] = []
        for key, csv_path in sorted(done.items()):
            if not csv_path.exists():
                continue
            mode, ylabel = key.split("/", 1)
            df = pd.read_csv(csv_path)
            df["factor_key"] = df["layer"] + "." + df["field"]
            for fk in cls.HISTORICAL_A_FACTORS:
                m = df[df["factor_key"] == fk]
                if m.empty:
                    continue
                r = m.iloc[0]
                rows.append(
                    {
                        "factor": fk,
                        "mode": mode,
                        "year": ylabel,
                        "score": r.get("Total_Score"),
                        "grade": cls.grade_letter(r.get("Grade")),
                        "rank_icir": r.get("Rank_ICIR"),
                        "rank_ic": r.get("Rank_IC_Mean"),
                        "n": r.get("N_Periods"),
                    }
                )

        detail = pd.DataFrame(rows)
        report_path = (
            Path(__file__).resolve().parent
            / f"factor_compare_report_{datetime.now().strftime('%Y%m%d')}.md"
        )
        lines: List[str] = [
            "# 因子多口径对照测评报告（对齐 factor_ab_history）",
            "",
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"数据目录: `{root}`",
            "",
            "## 测评模式",
            "",
            "| 模式 | period | 宇宙 |",
            "|------|--------|------|",
            "| full_p5/p10/p20 | 5/10/20 | 全市场 |",
            "",
            "## 历史 A 因子：各口径最高评级",
            "",
        ]
        if not detail.empty:
            best = (
                detail.sort_values("score", ascending=False)
                .groupby("factor")
                .first()
                .reset_index()
            )
            lines += [
                "| 因子 | 最优模式 | 年份 | 得分 | 评级 | Rank ICIR |",
                "|------|----------|------|------|------|-----------|",
            ]
            for _, r in best.iterrows():
                icir = r["rank_icir"]
                icir_s = f"{icir:.3f}" if pd.notna(icir) else "-"
                sc = int(r["score"]) if pd.notna(r["score"]) else "-"
                lines.append(
                    f"| {r['factor']} | {r['mode']} | {r['year']} | {sc} | {r['grade']} | {icir_s} |"
                )
            lines.append("")
            lines.append("## 历史 A 因子完整矩阵（得分/评级）")
            lines.append("")
            for fk in cls.HISTORICAL_A_FACTORS:
                sub = detail[detail["factor"] == fk]
                if sub.empty:
                    continue
                lines.append(f"### {fk}")
                lines.append("")
                lines.append("| 模式 | 2024 | 2025 | 2026/2026_YTD |")
                lines.append("|------|------|------|----------------|")
                for mode in [m[0] for m in cls.COMPARE_MODES]:
                    cells = []
                    for yl in ["2024", "2025", "2026_YTD"]:
                        cell_df = sub[(sub["mode"] == mode) & (sub["year"] == yl)]
                        if cell_df.empty:
                            cells.append("-")
                        else:
                            r = cell_df.iloc[0]
                            sc = r["score"]
                            g = r["grade"]
                            cells.append(f"{int(sc)}/{g}" if pd.notna(sc) else g)
                    lines.append(f"| {mode} | {cells[0]} | {cells[1]} | {cells[2]} |")
                lines.append("")
            lines += [
                "## 各模式 A/B 因子数量（50 字段）",
                "",
                "| 模式 | 年份 | A | B | C | D |",
                "|------|------|---|---|---|---|",
            ]
            for key, csv_path in sorted(done.items()):
                if not csv_path.exists():
                    continue
                mode, ylabel = key.split("/", 1)
                df = pd.read_csv(csv_path)
                grades = [cls.grade_letter(g) for g in df["Grade"]]
                cnt = {g: grades.count(g) for g in ["A", "B", "C", "D", "N/A"]}
                lines.append(
                    f"| {mode} | {ylabel} | {cnt['A']} | {cnt['B']} | {cnt['C']} | "
                    f"{cnt['D'] + cnt['N/A']} |"
                )
            lines.append("")
        lines += [
            "## 说明",
            "",
            "- 历史 `factor_ab_history.md` 的 A 多来自 **period=10/20** 口径，与 full_p5 不可直接比。",
            "- 标准 A 线：Total_Score ≥ 80；66~73 分为 B。",
            "- 2026 区间截止 2026-07-27（因子/L2 数据交集）。",
            "",
        ]
        report_path.write_text("\n".join(lines), encoding="utf-8")
        detail.to_csv(
            root / "historical_a_factors_detail.csv", index=False, encoding="utf-8-sig"
        )
        return report_path

    @staticmethod
    def fmt_cell(score, grade: str) -> str:
        if score is None or (isinstance(score, float) and pd.isna(score)):
            return grade
        return f"{int(score)}/{grade}"

    @staticmethod
    def fmt_ric(v) -> str:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return "-"
        return f"{float(v):.4f}"

    @classmethod
    def load_report_data(cls) -> dict:
        dfs: dict = {}
        for y, path in cls.REPORT_SOURCES.items():
            if not Path(path).exists():
                raise FileNotFoundError(f"缺少 {y} 测评结果: {path}")
            df = pd.read_csv(path)
            df["factor_key"] = df["layer"] + "." + df["field"]
            dfs[y] = df
        return dfs

    @classmethod
    def build_merged(cls, dfs: dict) -> pd.DataFrame:
        keys = sorted(set().union(*(set(d["factor_key"]) for d in dfs.values())))
        rows = []
        for k in keys:
            layer, field = k.split(".", 1)
            row: dict = {"layer": layer, "field": field, "factor": k}
            for y in cls.REPORT_YEARS:
                m = dfs[y][dfs[y]["factor_key"] == k]
                if m.empty:
                    row[f"{y}_score"] = None
                    row[f"{y}_grade"] = "缺失"
                    row[f"{y}_ric"] = None
                    row[f"{y}_n"] = None
                else:
                    r = m.iloc[0]
                    row[f"{y}_score"] = r.get("Total_Score")
                    row[f"{y}_grade"] = cls.grade_letter(r.get("Grade"))
                    ric = r.get("Rank_IC_Mean")
                    row[f"{y}_ric"] = ric if pd.notna(ric) else None
                    n = r.get("N_Periods")
                    row[f"{y}_n"] = int(n) if pd.notna(n) else 0
            rows.append(row)
        return pd.DataFrame(rows)

    @classmethod
    def load_compare_summaries(cls, compare_root: Path) -> Dict[str, Path]:
        done: Dict[str, Path] = {}
        if not compare_root.exists():
            return done
        for mode, _ in cls.COMPARE_MODES:
            mode_dir = compare_root / mode
            if not mode_dir.is_dir():
                continue
            for ylabel in cls.REPORT_YEAR_LABELS:
                csv_path = mode_dir / ylabel / "summary.csv"
                if csv_path.exists():
                    done[f"{mode}/{ylabel}"] = csv_path
        return done

    @classmethod
    def build_compare_detail(cls, done: Dict[str, Path]) -> pd.DataFrame:
        rows: List[dict] = []
        for key, csv_path in sorted(done.items()):
            mode, ylabel = key.split("/", 1)
            df = pd.read_csv(csv_path)
            df["factor_key"] = df["layer"] + "." + df["field"]
            for fk in cls.HISTORICAL_A_FACTORS:
                m = df[df["factor_key"] == fk]
                if m.empty:
                    continue
                r = m.iloc[0]
                rows.append(
                    {
                        "factor": fk,
                        "mode": mode,
                        "year": ylabel,
                        "score": r.get("Total_Score"),
                        "grade": cls.grade_letter(r.get("Grade")),
                        "rank_icir": r.get("Rank_ICIR"),
                    }
                )
        return pd.DataFrame(rows)

    @classmethod
    def append_compare_sections(
        cls,
        lines: List[str],
        compare_root: Optional[Path],
        done: Dict[str, Path],
        detail: pd.DataFrame,
    ) -> None:
        if not compare_root or not done:
            lines.append("## 多口径对照（对齐 factor_ab_history）")
            lines.append("")
            lines.append("未找到对照测评数据，请先运行 `--command compare`。")
            lines.append("")
            return
        lines.append("## 多口径对照（对齐 factor_ab_history）")
        lines.append("")
        lines.append(f"数据目录: `{compare_root}`")
        lines.append("")
        lines.append("### 测评模式")
        lines.append("")
        lines.append("| 模式 | period | 宇宙 |")
        lines.append("|------|--------|------|")
        lines.append("| full_p5/p10/p20 | 5/10/20 | 全市场 |")
        lines.append("| full_p5/p10/p20 | 5/10/20 | 全市场 |")
        lines.append("")
        lines.append(
            "> 上文各节为 **full_p5**（标准生产口径）；本节补充 period=10/20 与全市场对照。"
        )
        lines.append("")
        if detail.empty:
            lines.append("对照明细为空。")
            lines.append("")
            return
        lines.append("### 历史 A 因子：各口径最高评级")
        lines.append("")
        best = (
            detail.sort_values("score", ascending=False)
            .groupby("factor")
            .first()
            .reset_index()
        )
        lines.append("| 因子 | 最优模式 | 年份 | 得分 | 评级 | Rank ICIR |")
        lines.append("|------|----------|------|------|------|-----------|")
        for _, r in best.iterrows():
            icir = r["rank_icir"]
            icir_s = f"{icir:.3f}" if pd.notna(icir) else "-"
            sc = int(r["score"]) if pd.notna(r["score"]) else "-"
            lines.append(
                f"| {r['factor']} | {r['mode']} | {r['year']} | {sc} | {r['grade']} | {icir_s} |"
            )
        lines.append("")
        lines.append("### 历史 A 因子完整矩阵（得分/评级）")
        lines.append("")
        for fk in cls.HISTORICAL_A_FACTORS:
            sub = detail[detail["factor"] == fk]
            if sub.empty:
                continue
            lines.append(f"#### {fk}")
            lines.append("")
            lines.append("| 模式 | 2024 | 2025 | 2026/2026_YTD |")
            lines.append("|------|------|------|----------------|")
            for mode, _ in cls.COMPARE_MODES:
                cells = []
                for yl in cls.REPORT_YEAR_LABELS:
                    cell_df = sub[(sub["mode"] == mode) & (sub["year"] == yl)]
                    if cell_df.empty:
                        cells.append("-")
                    else:
                        r = cell_df.iloc[0]
                        sc = r["score"]
                        g = r["grade"]
                        cells.append(f"{int(sc)}/{g}" if pd.notna(sc) else g)
                lines.append(f"| {mode} | {cells[0]} | {cells[1]} | {cells[2]} |")
            lines.append("")
        lines.append("### 各模式 A/B 因子数量（50 字段）")
        lines.append("")
        lines.append("| 模式 | 年份 | A | B | C | D |")
        lines.append("|------|------|---|---|---|---|")
        for key, csv_path in sorted(done.items()):
            mode, ylabel = key.split("/", 1)
            df = pd.read_csv(csv_path)
            grades = [cls.grade_letter(g) for g in df["Grade"]]
            cnt = {g: grades.count(g) for g in ["A", "B", "C", "D", "N/A"]}
            lines.append(
                f"| {mode} | {ylabel} | {cnt['A']} | {cnt['B']} | {cnt['C']} | "
                f"{cnt['D'] + cnt['N/A']} |"
            )
        lines.append("")
        lines.append("### 对照说明")
        lines.append("")
        lines.append(
            "- 历史 `factor_ab_history.md` 的 A 多来自 **period=10/20** 或 **全市场** 口径，与 full_p5 不可直接比。"
        )
        lines.append("- 标准 A 线：Total_Score ≥ 80；66~73 分为 B。")
        lines.append("- 2026 区间截止 2026-07-27（因子/L2 数据交集）。")
        lines.append("")

    @classmethod
    def generate_report(
        cls,
        compare_root: Optional[Path] = None,
        output_path: Optional[Path] = None,
    ) -> Path:
        """从 summary.csv 生成 2024/2025/2026 三年因子测评对比报告。"""
        dfs = cls.load_report_data()
        merged = cls.build_merged(dfs)
        out_path = Path(output_path) if output_path else (
            Path(__file__).resolve().parent
            / f"factor_evaluation_report_2024_2025_2026_{datetime.now().strftime('%Y%m%d')}.md"
        )
        lines: list[str] = [
            "# 因子有效性测评报告（2024 / 2025 / 2026）",
            "",
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "## 测评配置",
            "",
            "| 项 | 值 |",
            "|---|---|",
            "| 主报告口径 | full_p5（预测周期 5 日，全市场） |",
            "| 宇宙 | 默认全市场；可选外部注入 universe_by_date |",
            "| 极性取反 | f34 多窗口 / f30 / f36 |",
            "| 分层数 | 5 |",
            "| 因子字段数 | 50（sector 15 + dragon 20 + force 15） |",
            "",
            "## 测评区间",
            "",
        ]
        for y in cls.REPORT_YEARS:
            lines.append(
                f"- **{y}**: {cls.REPORT_WINDOWS[y]}（`{cls.REPORT_SOURCE_NOTES[y]}`）"
            )
        lines.append("")
        lines.append("## 各年评级统计")
        lines.append("")
        lines.append("| 年份 | A | B | C | D | N/A | 合计 |")
        lines.append("|------|---|---|---|---|---|------|")
        for y in cls.REPORT_YEARS:
            grades = [cls.grade_letter(dfs[y].loc[i, "Grade"]) for i in dfs[y].index]
            cnt = {g: grades.count(g) for g in ["A", "B", "C", "D", "N/A"]}
            lines.append(
                f"| {y} | {cnt['A']} | {cnt['B']} | {cnt['C']} | {cnt['D']} | "
                f"{cnt['N/A']} | {len(grades)} |"
            )
        lines.append("")
        lines.append("## 各年 A/B 级因子")
        lines.append("")
        for y in cls.REPORT_YEARS:
            df = dfs[y].copy()
            df["g"] = df["Grade"].apply(cls.grade_letter)
            ab = df[df["g"].isin(["A", "B"])].sort_values(
                "Total_Score", ascending=False
            )
            lines.append(f"### {y}（{len(ab)} 个）")
            lines.append("")
            if ab.empty:
                lines.append("无 A/B 级因子。")
            else:
                lines.append("| 因子 | 得分 | 评级 | Rank IC Mean | Rank ICIR | N |")
                lines.append("|------|------|------|--------------|-----------|---|")
                for _, r in ab.iterrows():
                    ric = r.get("Rank_IC_Mean")
                    rir = r.get("Rank_ICIR")
                    ric_s = f"{ric:.4f}" if pd.notna(ric) else "-"
                    rir_s = f"{rir:.3f}" if pd.notna(rir) else "-"
                    n = int(r["N_Periods"]) if pd.notna(r["N_Periods"]) else 0
                    lines.append(
                        f"| {r['layer']}.{r['field']} | {int(r['Total_Score'])} | "
                        f"{r['g']} | {ric_s} | {rir_s} | {n} |"
                    )
            lines.append("")
        lines.append("## 三年均为 B 级及以上")
        lines.append("")
        stable = [
            r
            for _, r in merged.iterrows()
            if all(r[f"{y}_grade"] in ("A", "B") for y in cls.REPORT_YEARS)
        ]
        if not stable:
            lines.append("无因子在 2024/2025/2026 三年同时达到 B+。")
        else:
            lines.append("| 因子 | 2024 | 2025 | 2026 |")
            lines.append("|------|------|------|------|")
            for r in stable:
                lines.append(
                    f"| {r['factor']} | {cls.fmt_cell(r['2024_score'], r['2024_grade'])} | "
                    f"{cls.fmt_cell(r['2025_score'], r['2025_grade'])} | "
                    f"{cls.fmt_cell(r['2026_score'], r['2026_grade'])} |"
                )
        lines.append("")
        lines.append("## 2024 或 2025 为 B+ 但 2026 未达 B（风格切换）")
        lines.append("")
        dropped = []
        for _, r in merged.iterrows():
            hist_ab = r["2024_grade"] in ("A", "B") or r["2025_grade"] in ("A", "B")
            if hist_ab and r["2026_grade"] not in ("A", "B"):
                dropped.append(r)
        dropped.sort(
            key=lambda x: (x.get("2024_score") or 0) + (x.get("2025_score") or 0),
            reverse=True,
        )
        if not dropped:
            lines.append("无。")
        else:
            lines.append("| 因子 | 2024 | 2025 | 2026 |")
            lines.append("|------|------|------|------|")
            for r in dropped:
                lines.append(
                    f"| {r['factor']} | {cls.fmt_cell(r['2024_score'], r['2024_grade'])} | "
                    f"{cls.fmt_cell(r['2025_score'], r['2025_grade'])} | "
                    f"{cls.fmt_cell(r['2026_score'], r['2026_grade'])} |"
                )
        lines.append("")
        lines.append("## 全因子三年对比（50 字段）")
        lines.append("")
        for layer in ["sector", "dragon", "force"]:
            sub = merged[merged["layer"] == layer].sort_values("field")
            lines.append(f"### {layer.upper()} 层")
            lines.append("")
            lines.append(
                "| 因子 | 2024 得分/评级 | 2025 得分/评级 | 2026 得分/评级 | "
                "2024 RankIC | 2025 RankIC | 2026 RankIC |"
            )
            lines.append(
                "|------|----------------|----------------|----------------|"
                "-------------|-------------|-------------|"
            )
            for _, r in sub.iterrows():
                lines.append(
                    f"| {r['field']} | {cls.fmt_cell(r['2024_score'], r['2024_grade'])} | "
                    f"{cls.fmt_cell(r['2025_score'], r['2025_grade'])} | "
                    f"{cls.fmt_cell(r['2026_score'], r['2026_grade'])} | "
                    f"{cls.fmt_ric(r['2024_ric'])} | {cls.fmt_ric(r['2025_ric'])} | "
                    f"{cls.fmt_ric(r['2026_ric'])} |"
                )
            lines.append("")
        lines += [
            "## 数据质量说明",
            "",
            "| 因子 | 2024 | 2025 | 2026 | 说明 |",
            "|------|------|------|------|------|",
            "| dragon.f38_turnover_anomaly | N/A | B(66) | D(23) | 2024 缺 turnover_rate；2026 已修复 |",
            "| force.fcoop4_turnover_quality | D(N=0) | D(31) | D(31) | 2024 常数 IC；2026 已修复 |",
            "| force.longhu_board_bonus | D(N=0) | D(N=0) | D(N=0) | 需 QMT 环境 sync_lhb |",
            "",
        ]
        root = compare_root or cls.DEFAULT_COMPARE_ROOT
        done = cls.load_compare_summaries(root)
        detail = cls.build_compare_detail(done)
        cls.append_compare_sections(lines, root if done else None, done, detail)
        lines += [
            "## 结论摘要",
            "",
            "1. **2024（full_p5）**：`dragon.f33_consecutive_boards` 唯一 A(83)；"
            "f31 系列、f32、f36、f37、f_mean_reversion 等多因子 B 级。",
            "2. **2025（full_p5）**：`f33` B(73)、`f38` B(66)、`f_mean_reversion_signal` B(66)；"
            "龙头因子仍有效。",
            "3. **2026 YTD（full_p5）**：仅 `force.dragon_consistency_5d` B(65)；"
            "多数历史强势龙头因子降至 C/D。",
            "4. **板块层** `f30_sector_concentration` 三年 C~B 边缘，是相对稳定的 sector 因子。",
        ]
        if not detail.empty:
            lines.append(
                "5. **多口径对照**：2024 年 `f33` 在 cond/full × p5/p10/p20 均可达 A；"
                "`f35_bollinger_trend` 仅在 **full_p20** 达 A(83)，full_p5 仅 B(65)——"
                "与 `factor_ab_history` 混用 period 口径一致。"
            )
            lines.append(
                "6. **历史 A 复现**：`f34` 系列、`f_mean_reversion` 在 p20/全市场最高 B(73)；"
                "2026 年仅 `f36_identity_premium` 在 full_p20/full_p20 仍 B(73)。"
            )
        lines.append("")
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return out_path

    @classmethod
    def build_parser(cls) -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(description="zstock 因子有效性测评")
        p.add_argument(
            "--command",
            choices=["eval", "compare", "report"],
            default="eval",
            help="eval=单窗/多年；compare=多口径对照；report=三年报告",
        )
        p.add_argument("--start", default=None, help="开始日期 YYYY-MM-DD")
        p.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD")
        p.add_argument(
            "--years",
            default=None,
            help="批量测评自然年，逗号分隔，如 2024,2025,2026",
        )
        p.add_argument("--year-parallel", type=int, default=8, help="年份级并发数")
        p.add_argument("--workers", type=int, default=None, help="因子级并发协程数")
        p.add_argument(
            "--layer",
            choices=["sector", "dragon", "force"],
            default=None,
            help="只测某一层；默认三层全测",
        )
        p.add_argument("--field", default=None, help="只测单个因子字段")
        p.add_argument("--period", type=int, default=5, help="预测周期（交易日）")
        p.add_argument("--quantiles", type=int, default=5, help="分层数")
        p.add_argument(
            "--decay-max", type=int, default=0, help="若 >0，额外输出 IC 衰减 csv"
        )
        p.add_argument("--plot", action="store_true", help="保存分析图")
        p.add_argument("--output", default=None, help="输出目录")
        p.add_argument(
            "--invert",
            dest="invert",
            action="store_true",
            default=True,
            help="对负 IC 因子取反后再测（默认开启）",
        )
        p.add_argument(
            "--no-invert",
            dest="invert",
            action="store_false",
            help="关闭负IC因子取反",
        )
        p.add_argument(
            "--modes",
            default=None,
            help="compare 模式子集，如 cond_p10,full_p5",
        )
        p.add_argument(
            "--job-parallel", type=int, default=8, help="compare 任务级并发"
        )
        p.add_argument("--compare-dir", default=None, help="report 对照测评目录")
        return p

    @classmethod
    async def async_main(cls, args: argparse.Namespace) -> int:
        """CLI 入口；构造实例时走模块全局 FactorEvaluation（便于测试 monkeypatch）。"""
        # 实例构造走本模块全局名（测试可 monkeypatch）；类方法仍用 cls
        FE = getattr(
            sys.modules["zstock.factor_management.script.factor_evaluation"],
            "FactorEvaluation",
        )
        command = getattr(args, "command", "eval") or "eval"
        if command == "report":
            compare = Path(args.compare_dir) if args.compare_dir else None
            path = cls.generate_report(compare)
            print(path)
            return 0

        try:
            from zstock.common.utils import db_utils as db_module

            await db_module.db_manager.init_mongodb()
        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")
            return 1

        if command == "compare":
            modes = cls.COMPARE_MODES
            if args.modes:
                name_set = {x.strip() for x in args.modes.split(",") if x.strip()}
                modes = [m for m in cls.COMPARE_MODES if m[0] in name_set]
                if not modes:
                    logger.error("无有效模式: %s", args.modes)
                    return 1
            years_s = args.years or "2024,2025,2026"
            years = [int(y.strip()) for y in years_s.split(",") if y.strip()]
            report = await cls.run_compare(
                modes=modes,
                years=years,
                workers=args.workers,
                job_parallel=int(args.job_parallel),
                output_root=Path(args.output) if args.output else None,
            )
            print(report)
            return 0

        if args.years:
            years = [int(y.strip()) for y in args.years.split(",") if y.strip()]
            await cls.run_batch_years(
                years,
                period=args.period,
                n_quantiles=args.quantiles,
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

        evaluator = FE(
            start_date=args.start,
            end_date=args.end,
            period=args.period,
            n_quantiles=args.quantiles,
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

    @classmethod
    def main(cls) -> int:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        from zstock.common.logging_setup import setup_logging

        setup_logging("factor_evaluation", console_fmt="%(message)s")
        args = cls.build_parser().parse_args()
        return asyncio.run(cls.async_main(args))


def build_parser() -> argparse.ArgumentParser:
    return FactorEvaluation.build_parser()


async def async_main(args: argparse.Namespace) -> int:
    return await FactorEvaluation.async_main(args)


def main() -> int:
    return FactorEvaluation.main()


if __name__ == "__main__":
    sys.exit(main())
