"""换手与交易成本。

新持仓要明显优于当前最弱的持仓才换入。未满最短持有天数的，不能因为调仓卖掉。
另按佣金和印花税估算换手成本。国债、政金债、黄金免佣。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Iterable, Optional, Sequence, Set

import numpy as np
import pandas as pd

from zstock.common.book_codes import FEE_FREE_CODES

logger = logging.getLogger(__name__)


class TurnoverController:
    """Buffer 机制 + 最短持有 + 成本估算。"""

    def __init__(
        self,
        buffer_threshold: float = 0.25,
        fee_rate: float = 0.0015,
        min_hold_days: int = 3,
        fill_open_slots: bool = False,
        trading_calendar: Optional[Sequence[str]] = None,
        stamp_tax: float = 0.0005,
        fee_free_codes: Optional[Iterable[str]] = None,
    ):
        self.buffer_threshold = buffer_threshold
        self.fee_rate = fee_rate  # 单边佣金/滑点近似（买卖对称）
        self.stamp_tax = float(stamp_tax)  # A 股卖出印花税（只加在卖出腿）
        self.min_hold_days = int(min_hold_days)
        self.fill_open_slots = bool(fill_open_slots)
        self.trading_calendar: Optional[Set[str]] = (
            {str(d)[:10] for d in trading_calendar} if trading_calendar else None
        )
        self.fee_free_codes: Set[str] = {
            str(c).zfill(6) if str(c).isdigit() else str(c)
            for c in (fee_free_codes if fee_free_codes is not None else FEE_FREE_CODES)
        }
        logger.info(
            f"✅ TurnoverController 初始化完成: buffer={buffer_threshold} "
            f"fee={fee_rate} stamp={self.stamp_tax} min_hold={self.min_hold_days} "
            f"fill_open_slots={self.fill_open_slots} fee_free={len(self.fee_free_codes)}"
        )

    def set_trading_calendar(self, dates: Optional[Sequence[str]]) -> None:
        """注入交易日集合；有日历时最短持有按交易日计数。"""
        self.trading_calendar = {str(d)[:10] for d in dates} if dates else None

    def apply_buffer_mechanism(
        self,
        new_holdings: pd.DataFrame,
        current_holdings: Optional[pd.DataFrame] = None,
        buffer_threshold: Optional[float] = None,
        min_hold_days: Optional[int] = None,
        trade_date: Optional[str] = None,
        force_exit_codes: Optional[set] = None,
        fill_open_slots: Optional[bool] = None,
        trading_calendar: Optional[Sequence[str]] = None,
    ) -> pd.DataFrame:
        """
        Args:
            new_holdings: 候选目标持仓，需含 'code', 'weight', 可选 'score'。
            current_holdings: 当前持仓，需含 'code', 'weight', 可选 'score'/'entry_date'。
            buffer_threshold: 覆盖默认阈值。
            min_hold_days: 最短持有日历日；未满则禁止调仓卖出。
            trade_date: 当前交易日 YYYY-MM-DD。
            force_exit_codes: 强制卖出代码（豁免最短持有保护）。

        Returns:
            合并后的目标持仓。
        """
        if buffer_threshold is None:
            buffer_threshold = self.buffer_threshold
        if min_hold_days is None:
            min_hold_days = self.min_hold_days
        force_exit_codes = set(force_exit_codes or set())
        calendar = (
            {str(d)[:10] for d in trading_calendar}
            if trading_calendar is not None
            else self.trading_calendar
        )

        if new_holdings is None or new_holdings.empty:
            # 无新信号时：最短持有保护仍生效，但 force_exit 必须卖出
            if (
                current_holdings is not None
                and not current_holdings.empty
                and int(min_hold_days) > 0
                and trade_date
            ):
                protected = self._protected_holds(
                    current_holdings,
                    set(),
                    trade_date,
                    int(min_hold_days),
                    calendar=calendar,
                )
                if force_exit_codes and not protected.empty:
                    protected = protected[~protected["code"].isin(force_exit_codes)]
                if not protected.empty:
                    return self._normalize(protected)
            return pd.DataFrame(columns=['code', 'weight', 'score'])

        # 没有当前持仓时直接采纳新持仓
        if current_holdings is None or current_holdings.empty:
            return self._normalize(new_holdings.copy())

        # 整理列名
        new_df = new_holdings.copy()
        cur_df = current_holdings.copy()
        for df in (new_df, cur_df):
            if 'score' not in df.columns:
                df['score'] = np.nan

        if fill_open_slots is None:
            fill_open_slots = getattr(self, "fill_open_slots", False)

        new_codes = set(new_df['code'])

        kept = cur_df[cur_df['code'].isin(new_codes)].copy()
        kept_codes = set(kept['code'])

        # 最短持有：不在新目标中、但未满持有期的旧仓继续保留（强制退出除外）
        protected = self._protected_holds(
            cur_df,
            new_codes | force_exit_codes,
            trade_date,
            int(min_hold_days),
            calendar=calendar,
        )
        if force_exit_codes:
            if not kept.empty:
                kept = kept[~kept["code"].isin(force_exit_codes)]
            if not protected.empty:
                protected = protected[~protected["code"].isin(force_exit_codes)]

        additions = new_df[~new_df['code'].isin(kept_codes)].copy()
        if force_exit_codes and not additions.empty:
            additions = additions[~additions["code"].isin(force_exit_codes)]

        # 新增候选必须显著优于 cur 最低分
        min_cur_score = float(cur_df['score'].dropna().min()) if cur_df['score'].notna().any() else None
        if min_cur_score is None:
            threshold_score = -np.inf
        else:
            threshold_score = (
                min_cur_score * (1.0 + buffer_threshold)
                if min_cur_score > 0
                else min_cur_score + abs(min_cur_score) * buffer_threshold
            )

        if not additions.empty:
            if additions['score'].notna().any():
                if fill_open_slots:
                    target_cap = len(new_df)
                    n_retained = len(kept) + len(protected)
                    open_slots = max(0, target_cap - n_retained)
                    if open_slots > 0:
                        additions = additions.sort_values(by='score', ascending=False)
                        direct_adds = additions.iloc[:open_slots]
                        compete_adds = additions.iloc[open_slots:]
                        if not compete_adds.empty:
                            compete_adds = compete_adds[compete_adds['score'] >= threshold_score]
                        additions = pd.concat([direct_adds, compete_adds], ignore_index=True)
                    else:
                        additions = additions[additions['score'] >= threshold_score]
                else:
                    additions = additions[additions['score'] >= threshold_score]
            else:
                logger.warning("⚠️ 新增候选全部无评分(score=NaN)，拒绝换入，仅保留已有持仓")
                additions = additions.iloc[0:0]

        # 合并：kept 的权重用 new_holdings 里的最新权重
        if not kept.empty:
            new_w_map = new_df.set_index('code')['weight'].to_dict()
            kept['weight'] = kept['code'].map(new_w_map).fillna(kept['weight'])

        parts = []
        for part in (kept, additions, protected):
            if part is not None and not part.empty:
                cols = [c for c in ['code', 'weight', 'score', 'entry_date', 'entry_price', 'sector_code'] if c in part.columns]
                parts.append(part[cols])
        if not parts:
            # 旧仓已清、Buffer 又拒绝全部新候选 → 不应误空仓，回退为新目标
            if not new_df.empty:
                logger.warning(
                    "⚠️ Buffer 过滤后无持仓，回退为新目标持仓 "
                    f"({len(new_df)} 只)"
                )
                return self._normalize(new_df.copy())
            return pd.DataFrame(columns=['code', 'weight', 'score'])
        final = pd.concat(parts, ignore_index=True)
        final = final.drop_duplicates(subset=['code'], keep='first')
        return self._normalize(final)

    def _protected_holds(
        self,
        cur_df: pd.DataFrame,
        new_codes: set,
        trade_date: Optional[str],
        min_hold_days: int,
        *,
        calendar: Optional[Set[str]] = None,
    ) -> pd.DataFrame:
        if min_hold_days <= 0 or not trade_date or cur_df is None or cur_df.empty:
            return pd.DataFrame()
        if 'entry_date' not in cur_df.columns:
            return pd.DataFrame()
        rows = []
        for _, row in cur_df.iterrows():
            code = row['code']
            if code in new_codes:
                continue
            held = self._trading_days_between(
                row.get('entry_date'), trade_date, calendar=calendar
            )
            if held < min_hold_days:
                rows.append(row)
        if not rows:
            return pd.DataFrame()
        out = pd.DataFrame(rows)
        logger.info(
            f"🔒 最短持有保护 {len(out)} 只（min_hold_days={min_hold_days}）: "
            f"{list(out['code'])}"
        )
        return out

    @staticmethod
    def _trading_days_between(
        start: str,
        end: str,
        *,
        calendar: Optional[Set[str]] = None,
    ) -> int:
        """持有天数：有交易日历则数 (start, end] 内交易日，否则退回日历日差。"""
        try:
            a = str(start)[:10]
            b = str(end)[:10]
            if calendar:
                return sum(1 for d in calendar if a < d <= b)
            da = datetime.strptime(a, "%Y-%m-%d")
            db = datetime.strptime(b, "%Y-%m-%d")
            return max(0, (db - da).days)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        """只向下归一（权重和>1 时），保留组合优化留下的现金，避免满仓放大回撤。"""
        if df.empty:
            return df
        df = df.copy()
        total = float(df['weight'].sum())
        if total > 1.0 + 1e-9:
            df['weight'] = df['weight'] / total
        return df.reset_index(drop=True)

    def _norm_code(self, code: object) -> str:
        s = str(code).replace(".0", "")
        return s.zfill(6) if s.isdigit() else s

    def is_fee_free(self, code: object) -> bool:
        return self._norm_code(code) in self.fee_free_codes

    def estimate_trading_costs(
        self,
        current_holdings: Optional[pd.DataFrame],
        new_holdings: pd.DataFrame,
        total_capital: float = 1e7,
    ) -> Dict:
        """估算换手成本。

        turnover = 0.5 * Σ|Δw|（单边换手口径）；
        成本按标的分别计：fee_free_codes（债基/黄金等）买卖均为 0；
        其余 = 买入权重 * fee_rate + 卖出权重 * (fee_rate + stamp_tax)。
        """
        cur = current_holdings if current_holdings is not None and not current_holdings.empty else pd.DataFrame(columns=['code', 'weight'])
        new = new_holdings if new_holdings is not None and not new_holdings.empty else pd.DataFrame(columns=['code', 'weight'])

        cur = cur.copy()
        new = new.copy()
        if not cur.empty:
            cur["code"] = cur["code"].map(self._norm_code)
        if not new.empty:
            new["code"] = new["code"].map(self._norm_code)

        merged = pd.merge(
            cur[['code', 'weight']].rename(columns={'weight': 'w_old'}),
            new[['code', 'weight']].rename(columns={'weight': 'w_new'}),
            on='code', how='outer',
        )
        merged = merged.infer_objects().fillna(0.0)
        delta = merged['w_new'] - merged['w_old']
        buy_w = float(delta.clip(lower=0.0).sum())
        sell_w = float((-delta).clip(lower=0.0).sum())
        turnover = 0.5 * float(delta.abs().sum())

        from zstock.common.utils.common_utils import is_equity_etf

        cost_pct = 0.0
        charged_buy = 0.0
        charged_sell = 0.0
        for _, row in merged.iterrows():
            code = str(row["code"])
            if self.is_fee_free(code):
                continue
            change = float(row["w_new"] - row["w_old"])
            # 场内 ETF 收佣金，不收股票印花税
            sell_rate = self.fee_rate if is_equity_etf(code) else self.fee_rate + self.stamp_tax
            if change > 0:
                charged_buy += change
                cost_pct += change * self.fee_rate
            elif change < 0:
                charged_sell += -change
                cost_pct += (-change) * sell_rate
        return {
            'turnover': turnover,
            'cost_pct': cost_pct,
            'cost_amount': cost_pct * total_capital,
            'fee_rate': self.fee_rate,
            'stamp_tax': self.stamp_tax,
            'buy_weight': buy_w,
            'sell_weight': sell_w,
            'charged_buy_weight': charged_buy,
            'charged_sell_weight': charged_sell,
        }
