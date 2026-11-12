"""三状态账户：进攻截面、中性行业 ETF、防守债券与黄金。

状态一天判定一次，用的是截至上一交易日的收盘。
目标权重加总为 1。股票和 ETF 的空余权重买入国债，使资金利用率按目标为 100%
（执行层允许整手和 T+1 暂时低于 90%）。国债腿不受 3% 偏离带约束。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

import pandas as pd

INDUSTRY_ETFS: tuple[str, ...] = (
    "159928",  # 消费
    "512010",  # 医药
    "512880",  # 证券
    "512660",  # 军工
    "512400",  # 有色
    "512800",  # 银行
    "512480",  # 半导体
)
BOND = "511260"
BOND_FALLBACK = "511010"
BOND30 = "511090"
GOLD = "518880"
POLICY = "511520"
INDEX_CODE = "399300"
PARK_BONDS = frozenset({BOND, BOND_FALLBACK})
BOOK_OHLCV_CODES: tuple[str, ...] = (
    INDEX_CODE,
    *INDUSTRY_ETFS,
    BOND,
    BOND_FALLBACK,
    BOND30,
    GOLD,
    POLICY,
)

DEFENSE_BOND_WEIGHT = 0.70
MOM_WINDOW = 20
VOL_WINDOW = 20
TOP_N = 3
RANK_BUFFER = 2
MAX_WEIGHT = 0.40
VOL_TARGET = 0.15
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


def _pick_codes(ranked: List[tuple[float, str]], held: Iterable[str]) -> List[str]:
    order = [code for _score, code in ranked]
    rank = {code: idx for idx, code in enumerate(order)}
    limit = TOP_N + RANK_BUFFER
    chosen: List[str] = []
    carry = [code for code in held if code in rank and rank[code] < limit]
    carry.sort(key=lambda code: rank[code])
    for code in carry:
        if code not in chosen:
            chosen.append(code)
    if len(chosen) > TOP_N:
        chosen = chosen[:TOP_N]
    for code in order:
        if len(chosen) >= TOP_N:
            break
        if code not in chosen:
            chosen.append(code)
    return chosen


def _ready(closes: Dict[str, Sequence[float]], code: str, i: int) -> bool:
    series = closes.get(code) or []
    if i < 0 or i >= len(series):
        return False
    return _window(series, i, MIN_HISTORY) is not None


def bond_code(closes: Dict[str, Sequence[float]], i: int) -> str:
    if _ready(closes, BOND, i):
        return BOND
    return BOND_FALLBACK


def _gold_above_ma20(closes: Sequence[float], i: int) -> bool:
    ma = _sma(closes, i, 20)
    if ma is None or i < 0 or i >= len(closes):
        return False
    px = closes[i]
    return px is not None and px > ma


def industry_targets(
    closes: Dict[str, Sequence[float]],
    held: Optional[Iterable[str]] = None,
) -> Dict[str, float]:
    """中性：七只行业 ETF 的动量权重，剩余买入国债。"""
    lengths = [len(series) for series in closes.values() if series]
    if not lengths:
        return {BOND: 1.0}
    i = max(lengths) - 1
    held_set = [code for code in (held or []) if code in INDUSTRY_ETFS]
    ranked: List[tuple[float, str]] = []
    for code in INDUSTRY_ETFS:
        series = closes.get(code) or []
        if len(series) <= i:
            continue
        if not _ready(closes, code, i):
            continue
        prices = _window(series, i, MOM_WINDOW)
        score = _momentum_score(prices) if prices is not None else 0.0
        if score > 0:
            ranked.append((score, code))
    ranked.sort(reverse=True)
    chosen = _pick_codes(ranked, held_set)
    bond = bond_code(closes, i)
    if not chosen:
        return {bond: 1.0}

    kept: List[str] = []
    vols: List[float] = []
    series_ret: List[List[float]] = []
    for code in chosen:
        prices = _window(closes[code], i, VOL_WINDOW + 1)
        if prices is None:
            continue
        rets = [prices[k] / prices[k - 1] - 1.0 for k in range(1, len(prices))]
        vol = _annual_vol(rets)
        if vol <= 1e-6:
            continue
        kept.append(code)
        vols.append(vol)
        series_ret.append(rets)
    if not kept:
        return {bond: 1.0}
    inv = [1.0 / vol for vol in vols]
    total = sum(inv)
    raw = _cap_weights({code: weight / total for code, weight in zip(kept, inv)}, MAX_WEIGHT)
    port = _portfolio_vol(series_ret, [raw[code] for code in kept])
    scale = VOL_TARGET / port if port > VOL_TARGET else 1.0
    targets: Dict[str, float] = {}
    used = 0.0
    for code in kept:
        targets[code] = raw[code] * scale
        used += targets[code]
    targets[bond] = targets.get(bond, 0.0) + max(0.0, 1.0 - used)
    return {code: weight for code, weight in targets.items() if weight > 1e-8}


def defensive_targets(closes: Dict[str, Sequence[float]]) -> Dict[str, float]:
    """防守：债券约 70%，黄金站上 20 日均线则占 30%，否则政金债。"""
    if not closes or not any(closes.values()):
        return {BOND: 1.0}
    lengths = [len(series) for series in closes.values() if series]
    i = max(lengths) - 1
    bond10 = bond_code(closes, i)
    if _ready(closes, BOND30, i):
        bonds = {bond10: 0.5, BOND30: 0.5}
    else:
        bonds = {bond10: 1.0}
    gold = closes.get(GOLD) or []
    if len(gold) <= i or not _ready(closes, GOLD, i):
        return {code: weight for code, weight in bonds.items() if weight > 1e-8}
    if _gold_above_ma20(gold, i):
        sleeve = GOLD
    elif _ready(closes, POLICY, i):
        sleeve = POLICY
    else:
        sleeve = bond10
    out = {code: weight * DEFENSE_BOND_WEIGHT for code, weight in bonds.items()}
    out[sleeve] = out.get(sleeve, 0.0) + (1.0 - DEFENSE_BOND_WEIGHT)
    return {code: weight for code, weight in out.items() if weight > 1e-8}


def regime_step(bench: Sequence[float], i: int, regime_off: bool, above_streak: int) -> tuple[bool, int]:
    ma = _sma(bench, i, REGIME_MA)
    close = bench[i] if 0 <= i < len(bench) else None
    if ma is None or close is None:
        return regime_off, above_streak
    if not regime_off:
        if close < ma:
            return True, 0
        return False, 0
    if close > ma:
        above_streak += 1
        if above_streak >= REGIME_CONFIRM:
            return False, 0
        return True, above_streak
    return True, 0


def drawdown_step(
    bench: Sequence[float],
    i: int,
    nav: float,
    peak: float,
    dd_lock: bool,
    lock_age: int,
) -> tuple[bool, int, float]:
    if peak <= 0:
        peak = nav
    if not dd_lock:
        peak = max(peak, nav)
        if peak > 0 and nav / peak - 1.0 <= -DD_STOP:
            dd_lock = True
            lock_age = 0
    else:
        lock_age += 1
        ma = _sma(bench, i, REPAIR_MA) if bench else None
        ret = _trailing_return(bench, i, REPAIR_LOOKBACK) if bench else None
        close = bench[i] if bench and 0 <= i < len(bench) else None
        if (
            lock_age >= DD_MIN_DAYS
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


def grade_from_index_bars(rows: Sequence[dict]) -> str:
    """用已积累的指数 K 线算 M1 灯色。不足 31 根时视为黄灯。"""
    if len(rows) < 31:
        return "yellow"
    frame = pd.DataFrame(list(rows))
    from zstock.factor_management.market_factors import MarketFactors

    result = MarketFactors.calculate_market_sentiment(frame, index_name="沪深300")
    grade = str(result.get("market_risk_level") or "yellow").lower()
    if grade not in ("green", "yellow", "red"):
        return "yellow"
    return grade


def holdings_frame(weights: Dict[str, float]) -> pd.DataFrame:
    rows = [
        {"code": str(code), "weight": float(weight), "score": 0.0}
        for code, weight in weights.items()
        if weight > 1e-8
    ]
    if not rows:
        rows = [{"code": BOND, "weight": 1.0, "score": 0.0}]
    return pd.DataFrame(rows)


def park_treasury(holdings: Optional[pd.DataFrame], bond: str = BOND) -> pd.DataFrame:
    """把权重缺口补进国债，使目标权重之和为 1。"""
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
    band: float = REBALANCE_BAND,
    min_utilization: float = MIN_UTILIZATION,
) -> bool:
    """股票和 ETF 偏离超过 band，或当前权重之和低于利用率下限时需要调仓。"""
    codes = (set(current) | set(target)) - PARK_BONDS
    if any(abs(current.get(code, 0.0) - target.get(code, 0.0)) > band for code in codes):
        return True
    return sum(current.values()) < min_utilization - 1e-9


class MarketBook:
    """跨日状态：绿灯确认、均线防守、净值回撤锁。"""

    def __init__(self, confirm_green_days: int = 3):
        self.confirm_green_days = int(confirm_green_days)
        self.state = "neutral"
        self.green_streak = 0
        self.regime_off = False
        self.above_streak = 0
        self.peak = 0.0
        self.dd_lock = False
        self.lock_age = 0
        self._last_date: Optional[str] = None
        self._last: Optional[BookDecision] = None

    def step(
        self,
        trade_date: str,
        grade: str,
        index_closes: Optional[Sequence[float]] = None,
        nav: Optional[float] = None,
        closes: Optional[Dict[str, Sequence[float]]] = None,
        held: Optional[Iterable[str]] = None,
        confirm_green_days: Optional[int] = None,
    ) -> BookDecision:
        if trade_date == self._last_date and self._last is not None:
            return self._last
        grade = str(grade or "yellow").lower()
        if grade not in ("green", "yellow", "red"):
            grade = "yellow"
        confirm = self.confirm_green_days if confirm_green_days is None else int(confirm_green_days)
        bench = list(index_closes or [])
        if bench:
            i = len(bench) - 1
            self.regime_off, self.above_streak = regime_step(
                bench, i, self.regime_off, self.above_streak
            )
            if nav is not None and nav > 0:
                self.dd_lock, self.lock_age, self.peak = drawdown_step(
                    bench, i, float(nav), self.peak, self.dd_lock, self.lock_age
                )
        if grade == "green":
            self.green_streak += 1
        else:
            self.green_streak = 0

        forced = self.regime_off or self.dd_lock or grade == "red"
        leaving_defense = self.state == "defense" and not forced
        if forced:
            state = "defense"
        elif leaving_defense:
            state = "neutral"
        elif grade == "green" and self.green_streak >= confirm:
            state = "attack"
        else:
            state = "neutral"
        self.state = state

        book_closes = closes or {}
        weights: Optional[Dict[str, float]]
        if state == "defense":
            weights = defensive_targets(book_closes)
        elif state == "neutral":
            weights = industry_targets(book_closes, held)
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
            },
        )
        self._last_date = trade_date
        self._last = decision
        return decision
