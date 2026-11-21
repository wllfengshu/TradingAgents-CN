"""行业 ETF 策略。自己向 query_service 要日线，输出要买的代码和权重。

规则与原来的中性行业仓相同：七只行业 ETF 里动量为正、且不弱于指数过多的，
按波动倒数加权，单只有上限；正动量只数不够时压低行业仓。空出来的权重买国债。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence

from zstock.common.book_codes import BOOK_OHLCV_CODES
from zstock.common.utils.common_utils import normalize_code, normalize_date
from zstock.data_management.query_service import get_data_query_service


def bond_code(
    closes: Dict[str, Sequence[float]], i: int, params: Optional[dict] = None
) -> str:
    """行业仓剩余资金买哪只国债。十年国债日线够长用十年，否则用五年。"""
    from zstock.strategy_management.market_book import _cfg, _ready

    cfg = _cfg(params)
    bond = str(cfg["bond_code"])
    fallback = str(cfg["bond_fallback"])
    if _ready(closes, bond, i, params=params):
        return bond
    return fallback


def industry_targets(
    closes: Dict[str, Sequence[float]],
    held: Optional[Iterable[str]] = None,
    params: Optional[dict] = None,
) -> Dict[str, float]:
    """七只行业 ETF 的动量权重，剩余买入国债。"""
    from zstock.strategy_management.market_book import (
        _annual_vol,
        _cap_weights,
        _cfg,
        _end_i,
        _momentum_score,
        _pick_codes,
        _portfolio_vol,
        _ready,
        _sma,
        _trailing_ret,
        _window,
    )

    cfg = _cfg(params)
    industry_etfs = cfg["industry_etfs"]
    mom_window = int(cfg["mom_window"])
    vol_window = int(cfg["vol_window"])
    top_n = int(cfg["top_n"])
    rank_buffer = int(cfg["rank_buffer"])
    max_weight = float(cfg["max_weight"])
    vol_target = float(cfg["vol_target"])
    rel_gate = float(cfg.get("industry_rel_index_gate", 0.0) or 0.0)
    min_pos = int(cfg.get("industry_min_positive_mom", 0) or 0)
    narrow_cap = float(cfg.get("industry_narrow_max_weight", 0.30) or 0.30)
    exit_ma20 = bool(cfg.get("industry_exit_below_ma20", False))
    index_code = str(cfg.get("index_code") or "399300")
    if not any(closes.get(code) for code in industry_etfs):
        return {str(cfg["bond_code"]): 1.0}
    held_set = [code for code in (held or []) if code in industry_etfs]
    index_series = closes.get(index_code) or []
    index_i = _end_i(closes, index_code)
    index_ret = (
        _trailing_ret(index_series, index_i, mom_window) if index_i >= 0 else None
    )
    ranked: List[tuple[float, str]] = []
    positive_n = 0
    for code in industry_etfs:
        series = closes.get(code) or []
        i = _end_i(closes, code)
        if i < 0:
            continue
        if not _ready(closes, code, i, params=params):
            continue
        prices = _window(series, i, mom_window)
        score = _momentum_score(prices) if prices is not None else 0.0
        if score > 0:
            positive_n += 1
        if score <= 0:
            continue
        if index_ret is not None:
            etf_ret = _trailing_ret(series, i, mom_window)
            if etf_ret is not None and etf_ret < index_ret + rel_gate:
                continue
        if exit_ma20:
            ma = _sma(series, i, 20)
            px = series[i] if 0 <= i < len(series) else None
            if ma is not None and px is not None and px < ma:
                continue
        ranked.append((score, code))
    ranked.sort(reverse=True)
    chosen = _pick_codes(
        ranked, held_set, top_n=top_n, rank_buffer=rank_buffer
    )
    if exit_ma20:
        filtered = []
        for code in chosen:
            series = closes.get(code) or []
            i = _end_i(closes, code)
            ma = _sma(series, i, 20)
            px = series[i] if 0 <= i < len(series) else None
            if ma is not None and px is not None and px < ma:
                continue
            filtered.append(code)
        chosen = filtered
    bond = bond_code(closes, _end_i(closes, str(cfg["bond_code"])), params=params)
    if not chosen:
        return {bond: 1.0}

    kept: List[str] = []
    vols: List[float] = []
    series_ret: List[List[float]] = []
    for code in chosen:
        i = _end_i(closes, code)
        prices = _window(closes[code], i, vol_window + 1)
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
    raw = _cap_weights(
        {code: weight / total for code, weight in zip(kept, inv)}, max_weight
    )
    port = _portfolio_vol(series_ret, [raw[code] for code in kept])
    scale = vol_target / port if port > vol_target else 1.0
    targets: Dict[str, float] = {}
    used = 0.0
    for code in kept:
        targets[code] = raw[code] * scale
        used += targets[code]
    if min_pos > 0 and positive_n < min_pos and used > narrow_cap:
        shrink = narrow_cap / used
        for code in list(targets):
            targets[code] *= shrink
        used = narrow_cap
    targets[bond] = targets.get(bond, 0.0) + max(0.0, 1.0 - used)
    return {code: weight for code, weight in targets.items() if weight > 1e-8}


class IndustryEtfStrategy:
    """不接收行情入参。select 自己查日线，返回 {代码: 权重}。"""

    def __init__(self, query_service=None):
        self._query_service = query_service

    async def select(self, trade_date: Optional[str] = None) -> Dict[str, float]:
        td = _trade_day(trade_date)
        qs = self._query_service or get_data_query_service()
        closes = await _load_closes(qs, list(BOOK_OHLCV_CODES), td)
        return industry_targets(closes)


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
