"""账户层：红 / 黄 / 绿 → 目标仓位。

状态名与红绿灯对应：
  red    → defense  政金债 40%；银行、黄金各 30%，不达标则该档买国债
  yellow → neutral  股票类行业 ETF（或债），不开股票新仓
  green  → 动量才进攻股票；中性或反转停在行业 ETF

判定用截至上一交易日的收盘。目标权重加总为 1；行业仓波动超标或没有行业可买时，余量买国债。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd

from zstock.common.book_codes import (
    BOND,
    BOND30,
    BOND_FALLBACK,
    BOOK_OHLCV_CODES,
    GOLD,
    INDEX_CODE,
    INDUSTRY_ETFS,
    PARK_BONDS,
    POLICY,
)
from zstock.common.config.strategy_config import get_book_regime_config
from zstock.common.utils.common_utils import normalize_code, normalize_date

logger = logging.getLogger(__name__)


def _cfg(params: Optional[dict] = None) -> Dict[str, object]:
    return get_book_regime_config(params)

DEFENSE_BOND_WEIGHT = 0.70
MOM_WINDOW = 20
VOL_WINDOW = 20
TOP_N = 3
RANK_BUFFER = 2
MAX_WEIGHT = 0.40
VOL_TARGET = 0.25
REGIME_MA = 120
REGIME_CONFIRM = 5
DD_STOP = 0.10
DD_MIN_DAYS = 15
REPAIR_MA = 60
REPAIR_LOOKBACK = 20
MIN_HISTORY = 60
MIN_UTILIZATION = 0.90
REBALANCE_BAND = 0.03


@dataclass
class BookDecision:
    state: str
    weights: Optional[Dict[str, float]]
    grade: str
    detail: Dict[str, object] = field(default_factory=dict)


def _window(values: Sequence[float], i: int, length: int) -> Optional[List[float]]:
    if i - length + 1 < 0:
        return None
    chunk = list(values[i - length + 1 : i + 1])
    if any(v is None or v <= 0 for v in chunk):
        return None
    return [float(v) for v in chunk]


def _sma(values: Sequence[float], i: int, window: int) -> Optional[float]:
    chunk = _window(values, i, window)
    if chunk is None:
        return None
    return sum(chunk) / float(len(chunk))


def _trailing_return(values: Sequence[float], i: int, window: int) -> Optional[float]:
    if i - window < 0:
        return None
    last, prev = values[i], values[i - window]
    if last is None or prev is None or prev <= 0:
        return None
    return float(last) / float(prev) - 1.0


def _slope_r2(prices: Sequence[float]) -> tuple[float, float]:
    n = len(prices)
    logs = [math.log(p) for p in prices]
    x_mean = (n - 1) / 2.0
    y_mean = sum(logs) / float(n)
    var_x = sum((k - x_mean) ** 2 for k in range(n))
    if var_x <= 0:
        return 0.0, 0.0
    cov = sum((k - x_mean) * (logs[k] - y_mean) for k in range(n))
    slope = cov / var_x
    intercept = y_mean - slope * x_mean
    ss_tot = sum((y - y_mean) ** 2 for y in logs)
    if ss_tot <= 1e-18:
        return slope, 0.0
    ss_res = sum((logs[k] - (intercept + slope * k)) ** 2 for k in range(n))
    return slope, max(0.0, 1.0 - ss_res / ss_tot)


def _momentum_score(prices: Sequence[float]) -> float:
    slope, r2 = _slope_r2(prices)
    if slope <= 0 or r2 <= 0:
        return 0.0
    annualized = math.exp(slope * 252.0) - 1.0
    if annualized <= 0:
        return 0.0
    return annualized * r2


def _annual_vol(returns: Sequence[float]) -> float:
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / float(n)
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    return math.sqrt(var) * math.sqrt(252.0)


def _portfolio_vol(series: Sequence[Sequence[float]], weights: Sequence[float]) -> float:
    n = len(weights)
    if n == 0:
        return 0.0
    t = len(series[0])
    if t < 2:
        return 0.0
    means = [sum(row) / float(t) for row in series]
    var = 0.0
    for a in range(n):
        for b in range(n):
            cov = sum(
                (series[a][k] - means[a]) * (series[b][k] - means[b]) for k in range(t)
            ) / (t - 1)
            var += weights[a] * weights[b] * cov
    return math.sqrt(max(var, 0.0)) * math.sqrt(252.0)


def _cap_weights(weights: Dict[str, float], cap: float) -> Dict[str, float]:
    capped = dict(weights)
    for _ in range(len(capped) + 1):
        over = [code for code, weight in capped.items() if weight > cap + 1e-12]
        if not over:
            return capped
        free = [code for code, weight in capped.items() if weight < cap - 1e-12]
        excess = sum(capped[code] - cap for code in over)
        for code in over:
            capped[code] = cap
        if not free or excess <= 0:
            return capped
        base = sum(capped[code] for code in free)
        if base <= 0:
            return capped
        for code in free:
            capped[code] += excess * capped[code] / base
    return capped


def _pick_codes(
    ranked: List[tuple[float, str]],
    held: Iterable[str],
    *,
    top_n: int = TOP_N,
    rank_buffer: int = RANK_BUFFER,
) -> List[str]:
    order = [code for _score, code in ranked]
    rank = {code: idx for idx, code in enumerate(order)}
    limit = top_n + rank_buffer
    chosen: List[str] = []
    carry = [code for code in held if code in rank and rank[code] < limit]
    carry.sort(key=lambda code: rank[code])
    for code in carry:
        if code not in chosen:
            chosen.append(code)
    if len(chosen) > top_n:
        chosen = chosen[:top_n]
    for code in order:
        if len(chosen) >= top_n:
            break
        if code not in chosen:
            chosen.append(code)
    return chosen


def _ready(
    closes: Dict[str, Sequence[float]],
    code: str,
    i: int,
    params: Optional[dict] = None,
) -> bool:
    cfg = _cfg(params)
    series = closes.get(code) or []
    if i < 0 or i >= len(series):
        return False
    return _window(series, i, int(cfg["min_history"])) is not None


def _gold_sleeve_ok(
    closes: Sequence[float],
    i: int,
    params: Optional[dict] = None,
) -> bool:
    """袖套趋势：收盘站上 MA_short，且 MA_short > MA_trend。黄金和银行 ETF 共用。"""
    cfg = _cfg(params)
    short_n = int(cfg.get("gold_ma_short", 20) or 20)
    trend_n = int(cfg.get("gold_ma_trend", 60) or 60)
    if short_n <= 0 or trend_n <= short_n:
        return False
    if i < 0 or i >= len(closes):
        return False
    px = closes[i]
    ma_short = _sma(closes, i, short_n)
    ma_trend = _sma(closes, i, trend_n)
    if px is None or ma_short is None or ma_trend is None:
        return False
    return float(px) > float(ma_short) and float(ma_short) > float(ma_trend)


def _trailing_ret(series: Sequence[float], i: int, window: int) -> Optional[float]:
    if i < window or i >= len(series):
        return None
    base = float(series[i - window])
    last = float(series[i])
    if base <= 0 or last <= 0:
        return None
    return last / base - 1.0


def _end_i(closes: Dict[str, Sequence[float]], code: str) -> int:
    """各标的用自己序列末索引，避免长短历史混用 max(len)-1 把短序列整段踢掉。"""
    series = closes.get(code) or []
    return len(series) - 1



def _defense_trend_ok(
    closes: Dict[str, Sequence[float]],
    code: str,
    params: Optional[dict] = None,
) -> bool:
    """收盘站上 20 日均线，且 20 日均线高于 60 日均线。历史不够则不达标。"""
    series = list(closes.get(code) or [])
    i = len(series) - 1
    if i < 0 or not _ready(closes, code, i, params=params):
        return False
    return _gold_sleeve_ok(series, i, params=params)



def holdings_frame(weights: Dict[str, float]) -> pd.DataFrame:
    rows = [
        {"code": str(code), "weight": float(weight), "score": 0.0}
        for code, weight in weights.items()
        if weight > 1e-8
    ]
    if not rows:
        rows = [{"code": BOND, "weight": 1.0, "score": 0.0}]
    return pd.DataFrame(rows)


def park_treasury(
    holdings: Optional[pd.DataFrame],
    bond: Optional[str] = None,
    params: Optional[dict] = None,
) -> pd.DataFrame:
    """把权重缺口补进国债，使目标权重之和为 1。"""
    if bond is None:
        bond = str(_cfg(params)["bond_code"])
    if holdings is None or holdings.empty or "weight" not in getattr(holdings, "columns", []):
        return holdings_frame({bond: 1.0})
    frame = holdings.copy()
    frame["code"] = frame["code"].astype(str)
    frame["weight"] = frame["weight"].astype(float)
    if "score" not in frame.columns:
        frame["score"] = 0.0
    total = float(frame["weight"].sum())
    if total > 1.0 + 1e-8:
        frame["weight"] = frame["weight"] / total
        return frame.reset_index(drop=True)
    gap = 1.0 - total
    if gap <= 1e-8:
        return frame.reset_index(drop=True)
    mask = frame["code"] == str(bond)
    if mask.any():
        frame.loc[mask, "weight"] = frame.loc[mask, "weight"] + gap
    else:
        extra = pd.DataFrame([{"code": str(bond), "weight": gap, "score": 0.0}])
        frame = pd.concat([frame, extra], ignore_index=True)
    return frame


def weight_map(holdings: Optional[pd.DataFrame]) -> Dict[str, float]:
    if holdings is None or holdings.empty or "code" not in holdings.columns:
        return {}
    out: Dict[str, float] = {}
    for _, row in holdings.iterrows():
        out[str(row["code"])] = out.get(str(row["code"]), 0.0) + float(row["weight"])
    return out


def needs_book_trade(
    current: Dict[str, float],
    target: Dict[str, float],
    *,
    band: Optional[float] = None,
    min_utilization: Optional[float] = None,
    params: Optional[dict] = None,
) -> bool:
    """股票和 ETF 偏离超过 band，或当前权重之和低于利用率下限时需要调仓。"""
    cfg = _cfg(params)
    if band is None:
        band = float(cfg["rebalance_band"])
    if min_utilization is None:
        min_utilization = float(cfg["min_utilization"])
    park_bonds = frozenset({str(cfg["bond_code"]), str(cfg["bond_fallback"])})
    codes = (set(current) | set(target)) - park_bonds
    if any(abs(current.get(code, 0.0) - target.get(code, 0.0)) > band for code in codes):
        return True
    return sum(current.values()) < min_utilization - 1e-9


class MarketBook:
    """跨日状态：绿灯确认、均线防守、净值回撤锁。

    实例不可跨无关请求共享：回测按日顺序复用同一 book；
    API 单日路径应 reset / restore(snapshot) 或每次新建。
    """

    BOND = BOND
    BOND_FALLBACK = BOND_FALLBACK
    BOND30 = BOND30
    GOLD = GOLD
    POLICY = POLICY
    INDEX_CODE = INDEX_CODE
    INDUSTRY_ETFS = INDUSTRY_ETFS
    BOOK_OHLCV_CODES = BOOK_OHLCV_CODES

    def __init__(
        self,
        confirm_green_days: Optional[int] = None,
        params: Optional[dict] = None,
    ):
        cfg = _cfg(params)
        self._params = params
        self.confirm_green_days = int(
            cfg["confirm_green_days"]
            if confirm_green_days is None
            else confirm_green_days
        )
        self.reset()

    def reset(self) -> None:
        """清空跨日状态（保留 confirm_green_days）。"""
        self.state = "neutral"
        self.green_streak = 0
        self.regime_off = False
        self.above_streak = 0
        self.peak = 0.0
        self.dd_lock = False
        self.lock_age = 0
        self._gold_active: str = "policy"  # gold | policy
        self._gold_wish: str = "policy"
        self._gold_wish_streak: int = 0
        self._gold_peak: float = 0.0  # 持金期间收盘峰值
        self._gold_cooldown: int = 0  # 硬止损后禁止再开金的剩余交易日
        self._last_date: Optional[str] = None
        self._last: Optional[BookDecision] = None

    def snapshot(self) -> Dict[str, object]:
        """可序列化状态，供实盘跨进程恢复。"""
        return {
            "confirm_green_days": int(self.confirm_green_days),
            "state": str(self.state),
            "green_streak": int(self.green_streak),
            "regime_off": bool(self.regime_off),
            "above_streak": int(self.above_streak),
            "peak": float(self.peak),
            "dd_lock": bool(self.dd_lock),
            "lock_age": int(self.lock_age),
            "last_date": self._last_date,
            "gold_active": str(self._gold_active),
            "gold_wish": str(self._gold_wish),
            "gold_wish_streak": int(self._gold_wish_streak),
            "gold_peak": float(self._gold_peak),
            "gold_cooldown": int(self._gold_cooldown),
        }

    def restore(self, snap: Optional[Dict[str, object]]) -> None:
        """从 snapshot 恢复；snap 为空则 reset。"""
        if not snap:
            self.reset()
            return
        self.confirm_green_days = int(snap.get("confirm_green_days", self.confirm_green_days) or 2)
        self.state = str(snap.get("state") or "neutral")
        self.green_streak = int(snap.get("green_streak") or 0)
        self.regime_off = bool(snap.get("regime_off"))
        self.above_streak = int(snap.get("above_streak") or 0)
        self.peak = float(snap.get("peak") or 0.0)
        self.dd_lock = bool(snap.get("dd_lock"))
        self.lock_age = int(snap.get("lock_age") or 0)
        last = snap.get("last_date")
        self._last_date = str(last) if last else None
        self._gold_active = str(snap.get("gold_active") or "policy")
        self._gold_wish = str(snap.get("gold_wish") or self._gold_active)
        self._gold_wish_streak = int(snap.get("gold_wish_streak") or 0)
        self._gold_peak = float(snap.get("gold_peak") or 0.0)
        self._gold_cooldown = int(snap.get("gold_cooldown") or 0)
        self._last = None

    def prepare_for_date(self, trade_date: str) -> None:
        """按交易日对齐状态：同日幂等；日期回退则 reset。"""
        td = normalize_date(trade_date)
        if self._last_date is None:
            return
        last = normalize_date(self._last_date)
        if td == last:
            return
        if td < last:
            logger.warning(
                "MarketBook 日期回退 %s → %s，重置跨日状态", last, td
            )
            self.reset()

    def _defensive_targets(
        self,
        closes: Dict[str, Sequence[float]],
        params: Optional[dict] = None,
    ) -> Dict[str, float]:
        """防守：政金债 40%；银行、黄金各 30%，不达标的那一档改买国债。"""
        cfg = _cfg(params)
        policy_w = float(cfg.get("defense_policy_weight", 0.40))
        bank_w = float(cfg.get("defense_bank_weight", 0.30))
        gold_w = float(cfg.get("defense_gold_weight", 0.30))
        treasury = str(cfg["bond_code"])
        if not closes or not any(closes.values()):
            return {treasury: 1.0}
        from zstock.strategy_management.strategy_industry_etf import bond_code

        treasury = bond_code(closes, _end_i(closes, treasury), params=params)
        policy = str(cfg["policy_code"])
        bank = str(cfg.get("bank_code") or "512800")
        gold = str(cfg["gold_code"])
        out: Dict[str, float] = {}

        def add(code: str, weight: float) -> None:
            if weight > 1e-8:
                out[code] = out.get(code, 0.0) + weight

        add(policy if _ready(closes, policy, _end_i(closes, policy), params=params) else treasury, policy_w)
        add(bank if _defense_trend_ok(closes, bank, params=params) else treasury, bank_w)
        add(gold if _defense_trend_ok(closes, gold, params=params) else treasury, gold_w)
        return {code: weight for code, weight in out.items() if weight > 1e-8}


    def _regime_step(
        self,
        bench: Sequence[float],
        i: int,
        regime_off: bool,
        above_streak: int,
        params: Optional[dict] = None,
    ) -> tuple[bool, int]:
        cfg = _cfg(params)
        regime_ma = int(cfg["regime_ma"])
        regime_confirm = int(cfg["regime_confirm"])
        ma = _sma(bench, i, regime_ma)
        close = bench[i] if 0 <= i < len(bench) else None
        if ma is None or close is None:
            return regime_off, above_streak
        if not regime_off:
            if close < ma:
                return True, 0
            return False, 0
        if close > ma:
            above_streak += 1
            if above_streak >= regime_confirm:
                return False, 0
            return True, above_streak
        return True, 0


    def _drawdown_step(
        self,
        bench: Sequence[float],
        i: int,
        nav: float,
        peak: float,
        dd_lock: bool,
        lock_age: int,
        params: Optional[dict] = None,
    ) -> tuple[bool, int, float]:
        cfg = _cfg(params)
        dd_stop = float(cfg["dd_stop"])
        dd_min_days = int(cfg["dd_min_days"])
        repair_ma = int(cfg["repair_ma"])
        repair_lookback = int(cfg["repair_lookback"])
        if peak <= 0:
            peak = nav
        if not dd_lock:
            peak = max(peak, nav)
            if peak > 0 and nav / peak - 1.0 <= -dd_stop:
                dd_lock = True
                lock_age = 0
        else:
            lock_age += 1
            ma = _sma(bench, i, repair_ma) if bench else None
            ret = _trailing_return(bench, i, repair_lookback) if bench else None
            close = bench[i] if bench and 0 <= i < len(bench) else None
            if (
                lock_age >= dd_min_days
                and ma is not None
                and close is not None
                and close > ma
                and ret is not None
                and ret > 0
            ):
                dd_lock = False
                lock_age = 0
                peak = nav
        return dd_lock, lock_age, peak


    def step(
        self,
        trade_date: str,
        grade: str,
        index_closes: Optional[Sequence[float]] = None,
        nav: Optional[float] = None,
        closes: Optional[Dict[str, Sequence[float]]] = None,
        held: Optional[Iterable[str]] = None,
        confirm_green_days: Optional[int] = None,
        params: Optional[dict] = None,
        style_regime: Optional[str] = None,
    ) -> BookDecision:
        step_params = params if params is not None else self._params
        grade = str(grade or "yellow").lower()
        if grade not in ("green", "yellow", "red"):
            grade = "yellow"
        regime = str(style_regime or "neutral").lower()
        if (
            trade_date == self._last_date
            and self._last is not None
            and grade == self._last.grade
            and regime == str((self._last.detail or {}).get("style_regime") or "")
        ):
            return self._last
        confirm = self.confirm_green_days if confirm_green_days is None else int(confirm_green_days)
        bench = list(index_closes or [])
        if bench:
            i = len(bench) - 1
            self.regime_off, self.above_streak = self._regime_step(
                bench, i, self.regime_off, self.above_streak, params=step_params
            )
            if nav is not None and nav > 0:
                self.dd_lock, self.lock_age, self.peak = self._drawdown_step(
                    bench,
                    i,
                    float(nav),
                    self.peak,
                    self.dd_lock,
                    self.lock_age,
                    params=step_params,
                )
        if grade == "green":
            self.green_streak += 1
        else:
            self.green_streak = 0

        cfg = _cfg(step_params)
        skip_neutral = bool(cfg.get("attack_skip_neutral_on_green", False))
        ma_below = str(cfg.get("ma_below") or "defense")
        # 红灯和回撤锁一定防守。均线下方默认也防守；ma_below=neutral 时改停在行业 ETF。
        forced = self.dd_lock or grade == "red" or (self.regime_off and ma_below != "neutral")
        below_ma_neutral = self.regime_off and ma_below == "neutral" and not (self.dd_lock or grade == "red")
        leaving_defense = self.state == "defense" and not forced and not below_ma_neutral
        # 只有动量绿灯才开股票。中性、反转绿灯留在行业 ETF。
        can_attack = grade == "green" and regime == "momentum" and self.green_streak >= confirm
        if forced:
            state = "defense"
        elif below_ma_neutral:
            state = "neutral"
        elif leaving_defense:
            if skip_neutral and can_attack:
                state = "attack"
            else:
                state = "neutral"
        elif can_attack:
            state = "attack"
        else:
            state = "neutral"
        self.state = state

        book_closes = closes or {}
        weights: Optional[Dict[str, float]]
        if state == "defense":
            weights = self._defensive_targets(book_closes, params=step_params)
        elif state == "neutral":
            from zstock.strategy_management.strategy_industry_etf import industry_targets

            weights = industry_targets(book_closes, held, params=step_params)
        else:
            weights = None
        decision = BookDecision(
            state=state,
            weights=weights,
            grade=grade,
            detail={
                "green_streak": self.green_streak,
                "regime_off": self.regime_off,
                "dd_lock": self.dd_lock,
                "leaving_defense": leaving_defense,
                "style_regime": regime,
                "gold_sleeve": self._gold_active,
                "gold_cooldown": int(self._gold_cooldown),
                "gold_peak": float(self._gold_peak),
            },
        )
        self._last_date = trade_date
        self._last = decision
        return decision


# ── Book 行情输入（Mongo → 判定用序列）────────────────────────────────────────

_LOOKBACK_CALENDAR_DAYS = max(MIN_HISTORY + 80, 150)


@dataclass(frozen=True)
class BookMarketInputs:
    """截至 trade_date 前一交易日的 Book 输入。"""

    trade_date: str
    index_bars: List[dict]
    index_closes: List[float]
    asset_closes: Dict[str, List[float]]


def _date_before(trade_date: str, days: int = 1) -> str:
    td = datetime.strptime(normalize_date(trade_date), "%Y-%m-%d").date()
    return (td - timedelta(days=days)).strftime("%Y-%m-%d")


def _filter_through_exclusive(df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if "trade_date" not in out.columns:
        return out
    out["trade_date"] = out["trade_date"].astype(str).map(normalize_date)
    end_exclusive = normalize_date(trade_date)
    return out[out["trade_date"] < end_exclusive].sort_values("trade_date")


def build_index_bars(index_df: pd.DataFrame) -> List[dict]:
    rows: List[dict] = []
    if index_df is None or index_df.empty:
        return rows
    for _, row in index_df.iterrows():
        try:
            close = float(row.get("close", float("nan")))
        except (TypeError, ValueError):
            continue
        if not close > 0:
            continue

        def _num(key: str, default: float) -> float:
            try:
                value = float(row.get(key, default))
            except (TypeError, ValueError):
                return default
            if value != value:
                return default
            return value

        rows.append(
            {
                "close": close,
                "high": _num("high", close),
                "low": _num("low", close),
                "volume": _num("volume", 0.0),
            }
        )
    return rows


def build_asset_closes(ohlcv_map: Dict[str, pd.DataFrame], trade_date: str) -> Dict[str, List[float]]:
    """各 BOOK 代码的收盘价序列（不含 trade_date 当日）。"""
    out: Dict[str, List[float]] = {}
    for code in BOOK_OHLCV_CODES:
        code_s = normalize_code(code)
        df = ohlcv_map.get(code_s)
        if df is None:
            df = ohlcv_map.get(code)
        if df is None or df.empty:
            continue
        hist = _filter_through_exclusive(df, trade_date)
        if hist.empty or "close" not in hist.columns:
            continue
        closes: List[float] = []
        for v in hist["close"].tolist():
            try:
                px = float(v)
            except (TypeError, ValueError):
                continue
            if px > 0:
                closes.append(px)
        if closes:
            out[code_s] = closes
    return out


def compose_book_inputs(
    trade_date: str,
    ohlcv_map: Dict[str, pd.DataFrame],
) -> BookMarketInputs:
    td = normalize_date(trade_date)
    index_key = normalize_code(INDEX_CODE)
    index_df = ohlcv_map.get(index_key)
    if index_df is None:
        index_df = ohlcv_map.get(INDEX_CODE)
    if index_df is None:
        index_df = pd.DataFrame()
    index_df = _filter_through_exclusive(index_df, td)
    index_bars = build_index_bars(index_df)
    index_closes = [float(r["close"]) for r in index_bars]
    asset_closes = build_asset_closes(ohlcv_map, td)
    return BookMarketInputs(
        trade_date=td,
        index_bars=index_bars,
        index_closes=index_closes,
        asset_closes=asset_closes,
    )


async def load_book_market_inputs(
    trade_date: str,
    *,
    lookback_calendar_days: int = _LOOKBACK_CALENDAR_DAYS,
) -> BookMarketInputs:
    """从 DataQueryService 加载 Book 所需 OHLCV。"""
    from zstock.data_management.query_service import get_data_query_service

    td = normalize_date(trade_date)
    start = _date_before(td, lookback_calendar_days)
    qs = get_data_query_service()
    batch = await qs.get_ohlcv_batch(
        [normalize_code(c) for c in BOOK_OHLCV_CODES],
        start,
        td,
        batch_size=20,
        query_concurrency=4,
    )
    normalized: Dict[str, pd.DataFrame] = {}
    for code, df in (batch or {}).items():
        normalized[normalize_code(code)] = df
    if not normalized:
        logger.warning("Book 行情为空: trade_date=%s", td)
    return compose_book_inputs(td, normalized)


def positions_to_weights_frame(
    positions: Optional[list],
    total_capital: float,
) -> pd.DataFrame:
    """QMT 持仓 → 策略层 current_positions（code, weight）。"""
    if not positions or total_capital <= 0:
        return pd.DataFrame(columns=["code", "weight", "score"])
    rows: List[dict] = []
    for p in positions:
        if isinstance(p, dict):
            code = normalize_code(p.get("code") or "")
            raw_mv = p.get("market_value")
        else:
            code = normalize_code(getattr(p, "code", "") or "")
            raw_mv = getattr(p, "market_value", None)
        mv: Optional[float]
        try:
            mv = float(raw_mv) if raw_mv is not None else None
        except (TypeError, ValueError):
            mv = None
        if (mv is None or mv <= 0) and isinstance(p, dict):
            vol = int(p.get("volume", 0) or 0)
            px = float(p.get("current_price", 0) or 0)
            mv = vol * px
        if mv is None or mv <= 0:
            continue
        rows.append({"code": code, "weight": mv / float(total_capital), "score": 0.0})
    if not rows:
        return pd.DataFrame(columns=["code", "weight", "score"])
    return pd.DataFrame(rows)


def park_attack_idle(holdings, cfg, asset_closes, params):
    """进攻日剩下的权重：行业 ETF，否则国债。"""
    from zstock.strategy_management.strategy_industry_etf import industry_targets

    book = (cfg.get("book_regime") or {}) if isinstance(cfg, dict) else {}
    mode = str(book.get("attack_idle") or "industry")
    bond = str(book.get("bond_code") or BOND)
    if mode != "industry" or not asset_closes:
        return park_treasury(holdings, bond)
    sleeve = industry_targets(asset_closes, params=params)
    if not sleeve:
        return park_treasury(holdings, bond)
    if holdings is None or holdings.empty:
        return _scale_sleeve(sleeve, 1.0)
    frame = holdings.copy()
    frame["code"] = frame["code"].map(lambda c: str(c))
    used = float(frame["weight"].astype(float).sum())
    residual = max(0.0, 1.0 - used)
    if residual <= 1e-8:
        return frame
    extra = _scale_sleeve(sleeve, residual)
    out = pd.concat([frame, extra], ignore_index=True)
    out = out.groupby("code", as_index=False).agg(
        {col: "sum" if col == "weight" else "first" for col in out.columns if col != "code"}
    )
    return out.reset_index(drop=True)


def _scale_sleeve(sleeve, scale: float) -> pd.DataFrame:
    total = sum(float(w) for w in sleeve.values()) or 1.0
    rows = [
        {"code": str(code), "weight": scale * float(weight) / total, "score": 0.0}
        for code, weight in sleeve.items()
        if float(weight) > 1e-8
    ]
    return pd.DataFrame(rows)


def book_enabled(cfg: Dict[str, Any]) -> bool:
    book = (cfg or {}).get("book_regime") or {}
    return bool(book.get("enabled", False))


def book_result(
    trade_date: str,
    results: Dict[str, Any],
    state: str,
    holdings: pd.DataFrame,
    total_capital: float,
    *,
    rebalance_skipped: bool = False,
    trading_costs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """把账户状态和目标持仓包成管道返回值。"""
    results["final_holdings"] = holdings
    results["book_state"] = state
    costs = trading_costs or {
        "turnover": 0.0,
        "cost_pct": 0.0,
        "cost_amount": 0.0,
        "fee_rate": 0.0,
    }
    results["trading_costs"] = costs
    results["position_scale"] = 1.0
    results["force_exit_codes"] = []
    style = str(results.get("style_regime") or "neutral")
    return {
        "status": "success",
        "trade_date": trade_date,
        "execution_time": datetime.now().astimezone().isoformat(),
        "market_grade": results.get("market_grade"),
        "style_regime": style,
        "regime": style,
        "book_state": state,
        "attack_idle": bool(results.get("attack_idle", False)),
        "book_rebalance_skipped": rebalance_skipped,
        "effective_top_k": 0,
        "reduce_only": False,
        "statistics": {
            "signals_count": 0,
            "universe_count": len(results["signals"])
            if results.get("signals") is not None
            else 0,
            "optimized_holdings": len(holdings) if holdings is not None else 0,
            "final_holdings": len(holdings) if holdings is not None else 0,
            "risk_status": "passed",
            "turnover": float(costs.get("turnover", 0.0) or 0.0),
            "cost_pct": float(costs.get("cost_pct", 0.0) or 0.0),
            "total_capital": total_capital,
            "position_scale": 1.0,
        },
        "results": results,
        "position_scale": 1.0,
    }

