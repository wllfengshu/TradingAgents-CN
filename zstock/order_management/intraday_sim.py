"""用 5 分钟 K 走一遍日内规则。

09:30 那根 K 按目标权重调仓。之后每根 K 检查持仓回撤、日内回撤、盈利回撤。
14:50 那根 K 把今天策略还要的、盘中卖掉的买回来。
立即按收盘价成交，所以没有未成交单，14:00 重挂和 14:50 撤单在这里不产生第二笔价格。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as time_obj
from typing import Dict, List, Optional

import pandas as pd

from zstock.common.book_codes import is_t0
from zstock.common.utils.common_utils import (
    is_equity_etf,
    is_limit_down,
    is_limit_up,
    normalize_code,
)
from zstock.order_management.intraday import IntradayBook

LOT = 100
_REBUY_AT = time_obj(14, 50)


@dataclass
class SharePos:
    shares: int
    cost: float
    can_use: int


@dataclass
class DayResult:
    positions: Dict[str, SharePos]
    cash: float
    equity: float
    trades: List[dict] = field(default_factory=list)


def simulate_day(
    book: IntradayBook,
    positions: Dict[str, SharePos],
    cash: float,
    target_weights: Dict[str, float],
    bars_by_code: Dict[str, pd.DataFrame],
    prev_close: Dict[str, float],
    trade_date: str,
    *,
    fee_rate: float = 0.0015,
    stamp_tax: float = 0.0005,
    risk: bool = True,
) -> DayResult:
    """交易日走完。positions / book 会被改掉。"""
    bars = _day_bars(bars_by_code, trade_date)
    trades: List[dict] = []
    if not bars:
        equity = _equity(positions, cash, _last_prices(positions, prev_close))
        return DayResult(positions, cash, equity, trades)

    first_ts = next(iter(bars))
    opened = False
    for ts, row in bars.items():
        clock = _decision_clock(ts)
        closes = {code: px.close for code, px in row.items()}
        if not opened:
            _unlock(positions)
            _push(book, positions)
            book.note_targets(code for code, w in target_weights.items() if w > 1e-8)
            book.on_open({code: px.open for code, px in row.items() if px.open > 0}, trade_date)
            cash = _rebalance(
                book, positions, cash, target_weights, closes, prev_close, trade_date, trades,
                fee_rate, stamp_tax,
            )
            opened = True
        _push(book, positions)
        if not risk:
            first_ts = ts
            continue
        blocked = {
            code
            for code, px in closes.items()
            if _blocked_sell(code, px, prev_close.get(code), trade_date) or _bar_volume(row, code) <= 0
        }
        snap = _snap(book)
        actions = book.on_prices(clock, {c: p for c, p in closes.items() if c not in blocked})
        for action in actions:
            code = action["code"]
            if code in blocked:
                _restore(book, code, snap.get(code))
                continue
            cash = _sell(
                book, positions, cash, code, int(action["volume"]), float(action["price"]),
                trade_date, str(action.get("reason") or "卖出"), trades, fee_rate, stamp_tax,
            )
        if ts.time() >= _REBUY_AT and not book._cancelled:
            for action in book.on_cancel_and_rebuy([], closes):
                if action.get("action") != "buy":
                    continue
                code = action["code"]
                px = closes.get(code)
                if px is None or _blocked_buy(code, px, prev_close.get(code), trade_date):
                    continue
                if _bar_volume(row, code) <= 0:
                    continue
                cash = _buy(
                    book, positions, cash, code, int(action["volume"]), px,
                    trade_date, "尾盘买回", trades, fee_rate,
                )
        first_ts = ts
    last = bars[first_ts]
    equity = _equity(positions, cash, {code: px.close for code, px in last.items()})
    return DayResult(positions, cash, equity, trades)


def _rebalance(
    book, positions, cash, target_weights, prices, prev_close, trade_date, trades,
    fee_rate, stamp_tax,
) -> float:
    equity = _equity(positions, cash, prices)
    targets = {
        normalize_code(str(code)): _lots(equity, prices.get(normalize_code(str(code)), 0.0), float(weight))
        for code, weight in target_weights.items()
        if float(weight) > 1e-8
    }
    codes = set(positions) | set(targets)
    for code in sorted(codes):
        held = positions.get(code)
        have = held.shares if held else 0
        want = targets.get(code, 0)
        px = prices.get(code)
        if px is None or px <= 0 or have <= want:
            continue
        sell_qty = have - want
        if held is not None and not is_t0(code):
            sell_qty = min(sell_qty, held.can_use)
        if _blocked_sell(code, px, prev_close.get(code), trade_date):
            continue
        cash = _sell(
            book, positions, cash, code, sell_qty, px, trade_date, "调仓", trades, fee_rate, stamp_tax,
        )
    equity = _equity(positions, cash, prices)
    for code, weight in target_weights.items():
        code = normalize_code(str(code))
        px = prices.get(code)
        if px is None or px <= 0 or float(weight) <= 1e-8:
            continue
        if _blocked_buy(code, px, prev_close.get(code), trade_date):
            continue
        want = _lots(equity, px, float(weight))
        have = positions[code].shares if code in positions else 0
        if want > have:
            cash = _buy(
                book, positions, cash, code, want - have, px, trade_date, "调仓", trades, fee_rate,
            )
    return cash


def _sell(book, positions, cash, code, qty, price, trade_date, reason, trades, fee_rate, stamp_tax) -> float:
    code = normalize_code(str(code))
    pos = positions.get(code)
    if pos is None or qty <= 0 or price <= 0:
        return cash
    qty = min(int(qty), pos.shares, pos.can_use if not is_t0(code) else pos.shares)
    if qty <= 0:
        return cash
    rate = 0.0 if is_t0(code) else (fee_rate if is_equity_etf(code) else fee_rate + stamp_tax)
    cash += qty * price * (1.0 - rate)
    pos.shares -= qty
    pos.can_use = max(0, pos.can_use - qty)
    if pos.shares <= 0:
        positions.pop(code, None)
    else:
        _sync_lot(book, code, pos)
    trades.append({"trade_date": trade_date, "code": code, "side": "sell", "volume": qty, "price": price, "reason": reason})
    return cash


def _buy(book, positions, cash, code, qty, price, trade_date, reason, trades, fee_rate) -> float:
    code = normalize_code(str(code))
    if qty <= 0 or price <= 0:
        return cash
    rate = 0.0 if is_t0(code) else fee_rate
    unit = price * (1.0 + rate)
    if unit <= 0:
        return cash
    affordable = int(cash / unit / LOT) * LOT
    qty = min(int(qty) // LOT * LOT, affordable)
    if qty <= 0:
        return cash
    cash -= qty * unit
    pos = positions.get(code)
    if pos is None:
        pos = SharePos(shares=0, cost=price, can_use=0)
        positions[code] = pos
    total = pos.shares + qty
    pos.cost = (pos.shares * pos.cost + qty * price) / total if total else price
    pos.shares = total
    if is_t0(code):
        pos.can_use += qty
    was_flat = code not in book.lots or book.lots[code].volume <= 0
    _sync_lot(book, code, pos)
    if was_flat and code in book.lots:
        book.lots[code].peak = price
        book.lots[code].day_open = price
        book.lots[code].profit_on = False
    trades.append({"trade_date": trade_date, "code": code, "side": "buy", "volume": qty, "price": price, "reason": reason})
    return cash


def _sync_lot(book: IntradayBook, code: str, pos: SharePos) -> None:
    book.sync(code, pos.shares, pos.can_use if not is_t0(code) else pos.shares, pos.cost)


def _push(book: IntradayBook, positions: Dict[str, SharePos]) -> None:
    for code in list(book.lots):
        if code in positions:
            continue
        lot = book.lots[code]
        if lot.rebuy > 0:
            lot.volume = 0
            lot.can_use = 0
            continue
        book.sync(code, 0, 0, 0)
    for code, pos in positions.items():
        _sync_lot(book, code, pos)


def _unlock(positions: Dict[str, SharePos]) -> None:
    for pos in positions.values():
        pos.can_use = pos.shares


def _lots(equity: float, price: float, weight: float) -> int:
    if equity <= 0 or price <= 0 or weight <= 0:
        return 0
    return int(equity * weight / price / LOT) * LOT


def _equity(positions: Dict[str, SharePos], cash: float, prices: Dict[str, float]) -> float:
    total = cash
    for code, pos in positions.items():
        px = prices.get(code)
        if px and px > 0:
            total += pos.shares * px
    return total


def _blocked_sell(code: str, price: float, pre: Optional[float], trade_date: str) -> bool:
    return bool(pre and pre > 0 and is_limit_down(code, pre, price, trade_date))


def _blocked_buy(code: str, price: float, pre: Optional[float], trade_date: str) -> bool:
    return bool(pre and pre > 0 and is_limit_up(code, pre, price, trade_date))


def _bar_volume(row: dict, code: str) -> float:
    px = row.get(code)
    return 0.0 if px is None else px.volume


@dataclass
class _Px:
    open: float
    close: float
    volume: float


def _day_bars(bars_by_code: Dict[str, pd.DataFrame], trade_date: str) -> Dict[datetime, Dict[str, _Px]]:
    out: Dict[datetime, Dict[str, _Px]] = {}
    day = trade_date[:10]
    for code, frame in bars_by_code.items():
        if frame is None or frame.empty or "trade_date" not in frame.columns:
            continue
        code = normalize_code(str(code))
        for rec in frame.itertuples(index=False):
            raw = str(getattr(rec, "trade_date", ""))
            if not raw.startswith(day):
                continue
            ts = _parse_ts(raw)
            if ts is None or not _in_session(ts):
                continue
            try:
                op = float(getattr(rec, "open"))
                cl = float(getattr(rec, "close"))
                vol = float(getattr(rec, "volume", 0) or 0)
            except (TypeError, ValueError):
                continue
            if not (cl > 0):
                continue
            # 部分 5 分钟 K 的开盘价是收盘价的两倍（拆分日前后的字段口径不一致）。
            # 成交和日内基准都只用收盘价能对上的那个开盘。
            if not (op > 0) or op > cl * 1.15 or op < cl * 0.87:
                op = cl
            out.setdefault(ts, {})[code] = _Px(op, cl, vol)
    return dict(sorted(out.items()))


def _parse_ts(raw: str) -> Optional[datetime]:
    text = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _in_session(ts: datetime) -> bool:
    t = ts.time()
    if time_obj(9, 30) < t <= time_obj(11, 30):
        return True
    return time_obj(13, 0) < t <= time_obj(15, 0)


def _decision_clock(ts: datetime) -> datetime:
    """K 线时间是这根 K 的结束时刻。11:30 和 15:00 要落在连续竞价里，规则才看得到。"""
    t = ts.time()
    if t == time_obj(11, 30):
        return ts.replace(hour=11, minute=29, second=59)
    if t == time_obj(15, 0):
        return ts.replace(hour=14, minute=59, second=59)
    return ts


def _last_prices(positions: Dict[str, SharePos], prev_close: Dict[str, float]) -> Dict[str, float]:
    return {code: float(prev_close.get(code) or pos.cost) for code, pos in positions.items()}


def _snap(book: IntradayBook) -> Dict[str, tuple]:
    return {
        code: (lot.volume, lot.can_use, lot.rebuy, lot.profit_on, lot.profit_peak)
        for code, lot in book.lots.items()
    }


def _restore(book: IntradayBook, code: str, saved: Optional[tuple]) -> None:
    lot = book.lots.get(code)
    if lot is None or saved is None:
        return
    lot.volume, lot.can_use, lot.rebuy, lot.profit_on, lot.profit_peak = saved

