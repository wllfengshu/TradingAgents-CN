"""
因子有效性评价核心（对照 示例代码.md）

能力：
1. IC / Rank IC / ICIR
2. 分层收益 + 多空
3. IC 衰减
4. 因子自相关
5. 综合评分
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import stats


class FactorEvaluator:
    """全面的因子有效性评价系统。"""

    def __init__(
        self,
        factor_data: pd.DataFrame,
        price_data: pd.DataFrame,
        periods: Optional[List[int]] = None,
    ):
        """
        Args:
            factor_data: index=date(str), columns=asset_code
            price_data:  index=date(str), columns=asset_code（收盘价）
            periods: 预测周期（交易日）
        """
        self.factor_data = factor_data.sort_index()
        self.price_data = price_data.sort_index()
        self.periods = periods or [1, 5, 10, 20]
        self.results: Dict = {}

    # ==================== IC 系列 ====================

    def calc_ic_series(self, period: int = 5) -> pd.DataFrame:
        """计算每期截面 IC / Rank IC 时间序列。"""
        forward_returns = self.price_data.pct_change(
            period, fill_method=None
        ).shift(-period)

        rows = []
        for date in self.factor_data.index:
            if date not in forward_returns.index:
                continue
            factor = self.factor_data.loc[date].dropna()
            ret = forward_returns.loc[date].dropna()
            common = factor.index.intersection(ret.index)
            if len(common) < 10:
                continue
            f = factor[common].astype(float)
            r = ret[common].astype(float)
            # 去掉非有限值
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

    def calc_ic_summary(self, ic_series: pd.DataFrame) -> pd.Series:
        """IC 统计摘要。"""
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

    # ==================== 分层回测 ====================

    def calc_quantile_returns(
        self, period: int = 5, n_quantiles: int = 5
    ) -> pd.DataFrame:
        """分层收益：按因子值分 N 组，看收益是否单调。"""
        forward_returns = self.price_data.pct_change(
            period, fill_method=None
        ).shift(-period)
        buckets: Dict[int, List[float]] = {i: [] for i in range(1, n_quantiles + 1)}

        for date in self.factor_data.index:
            if date not in forward_returns.index:
                continue
            factor = self.factor_data.loc[date].dropna()
            ret = forward_returns.loc[date].dropna()
            common = factor.index.intersection(ret.index)
            if len(common) < n_quantiles * 5:
                continue
            f = factor[common].astype(float)
            r = ret[common].astype(float)
            mask = np.isfinite(f.values) & np.isfinite(r.values)
            f, r = f[mask], r[mask]
            if len(f) < n_quantiles * 5 or f.nunique() < n_quantiles:
                continue
            try:
                quantiles = pd.qcut(f, n_quantiles, labels=False, duplicates="drop") + 1
            except ValueError:
                continue
            for q in range(1, n_quantiles + 1):
                m = quantiles == q
                if m.any():
                    buckets[q].append(float(r[m].mean()))

        min_len = min((len(v) for v in buckets.values()), default=0)
        if min_len == 0:
            return pd.DataFrame(columns=[f"Q{q}" for q in range(1, n_quantiles + 1)])
        return pd.DataFrame(
            {f"Q{q}": buckets[q][:min_len] for q in range(1, n_quantiles + 1)}
        )

    def calc_long_short_return(self, quantile_returns_df: pd.DataFrame) -> Dict:
        """多空组合收益 = Qn - Q1。"""
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

    # ==================== 衰减 / 自相关 ====================

    def calc_factor_decay(self, max_period: int = 20) -> pd.DataFrame:
        """因子 IC 随预测期衰减。"""
        rows = []
        for period in range(1, max_period + 1):
            ic_series = self.calc_ic_series(period=period)
            summary = self.calc_ic_summary(ic_series)
            rows.append(
                {
                    "period": period,
                    "IC_Mean": summary.get("IC_Mean", np.nan),
                    "Rank_IC_Mean": summary.get("Rank_IC_Mean", np.nan),
                    "Rank_ICIR": summary.get("Rank_ICIR", np.nan),
                    "N_Periods": summary.get("N_Periods", 0),
                }
            )
        return pd.DataFrame(rows).set_index("period")

    def calc_factor_autocorr(self, lag: int = 1) -> Dict:
        """因子截面自相关（Spearman）。"""
        dates = sorted(self.factor_data.index)
        vals = []
        for i in range(lag, len(dates)):
            curr = self.factor_data.loc[dates[i]].dropna()
            prev = self.factor_data.loc[dates[i - lag]].dropna()
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

    # ==================== 综合评分 ====================

    def comprehensive_score(self, period: int = 5) -> Dict:
        """综合因子评分 (0-100)。"""
        ic_series = self.calc_ic_series(period)
        summary = self.calc_ic_summary(ic_series)
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

    def evaluate(self, period: int = 5, n_quantiles: int = 5) -> Dict:
        """一站式评价：IC + 分层 + 多空 + 自相关 + 综合分。"""
        ic_series = self.calc_ic_series(period)
        summary = self.calc_ic_summary(ic_series)
        qret = self.calc_quantile_returns(period, n_quantiles)
        ls = self.calc_long_short_return(qret)
        autocorr = self.calc_factor_autocorr(lag=1)
        score = self.comprehensive_score(period)
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
