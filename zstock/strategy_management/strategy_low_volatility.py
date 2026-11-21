"""低波策略。自己向 query_service 要日线，输出要买的代码和权重。

合计 100%：30 年国债 35%，10 年国债 35%，其余 30% 只买一只。
这 30% 先看黄金，趋势达标就买黄金；黄金不达标再看银行；银行也不达标就买政金债。
趋势：收盘高于 20 日均线，且 20 日均线高于 60 日均线。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence

from zstock.common.book_codes import BOND, BOND30, GOLD, POLICY
from zstock.common.utils.common_utils import normalize_code, normalize_date
from zstock.data_management.query_service import get_data_query_service

BANK = "512800"
WEIGHT_30Y = 0.35
WEIGHT_10Y = 0.35
WEIGHT_SLEEVE = 0.30


def low_volatility_targets(
    closes: Dict[str, Sequence[float]],
    params: Optional[dict] = None,
) -> Dict[str, float]:
    from zstock.strategy_management.market_book import _defense_trend_ok

    out: Dict[str, float] = {}

    def add(code: str, weight: float) -> None:
        if weight > 1e-8:
            out[code] = out.get(code, 0.0) + weight

    add(BOND30, WEIGHT_30Y)
    add(BOND, WEIGHT_10Y)
    if _defense_trend_ok(closes, GOLD, params=params):
        add(GOLD, WEIGHT_SLEEVE)
    elif _defense_trend_ok(closes, BANK, params=params):
        add(BANK, WEIGHT_SLEEVE)
    else:
        add(POLICY, WEIGHT_SLEEVE)
    return out


class LowVolatilityStrategy:
    """不接收行情入参。select 自己查日线，返回 {代码: 权重}。"""

    def __init__(self, query_service=None):
        self._query_service = query_service

    async def select(self, trade_date: Optional[str] = None) -> Dict[str, float]:
        td = _trade_day(trade_date)
        qs = self._query_service or get_data_query_service()
        closes = await _load_closes(qs, [BOND30, BOND, BANK, GOLD, POLICY], td)
        return low_volatility_targets(closes)


def _trade_day(trade_date: Optional[str]) -> str:
    if trade_date:
        return normalize_date(trade_date)
    return datetime.now().strftime("%Y-%m-%d")


async def _load_closes(qs, codes: Sequence[str], trade_date: str) -> Dict[str, List[float]]:
    end = normalize_date(trade_date)
    start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=500)).strftime("%Y-%m-%d")
    frames = await qs.get_ohlcv_batch(list(codes), start, end, period="daily")
    out: Dict[str, List[float]] = {}
    for code, frame in (frames or {}).items():
        if frame is None or getattr(frame, "empty", True) or "close" not in frame.columns:
            continue
        view = frame
        if "trade_date" in frame.columns:
            days = frame["trade_date"].map(lambda x: normalize_date(str(x)[:10]))
            view = frame.loc[days <= end].sort_values("trade_date")
        closes = []
        for value in view["close"].tolist():
            try:
                px = float(value)
            except (TypeError, ValueError):
                continue
            if px > 0:
                closes.append(px)
        if closes:
            out[normalize_code(str(code))] = closes
    return out
