"""日内风控。策略层仍在 09:30 决定买什么；这里只决定盘中卖不卖、尾盘买不买回。

时刻（上海时间，每个交易日）：
  09:30 一次。调用方先跑策略，再把今天的目标代码交给 note_targets，并记下开盘价。
  每 5 分钟。持仓回撤、日内回撤、盈利回撤，命中就卖掉当前能卖的全部。
  14:00 一次。未成交委托按最新价撤了重挂。
  14:50 一次。先撤销全部未成交。今天策略本来要买、盘中被风控卖掉的，再下一笔买回。

T+1 当天买入的份额卖不掉，也不记回补。T+0 当天买入的可以卖。
行业 ETF、国债、黄金按代码各有一行。表里没有的代码用股票默认值。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as time_obj
from typing import Dict, Iterable, List, Optional

from zstock.common.config.strategy_config import load_strategy_params
from zstock.common.utils.common_utils import normalize_code

_OPEN = time_obj(9, 30)
_LUNCH_START = time_obj(11, 30)
_LUNCH_END = time_obj(13, 0)
_RESUBMIT = time_obj(14, 0)
_CANCEL = time_obj(14, 50)
_CLOSE = time_obj(15, 0)


@dataclass
class RiskRow:
    name: str
    settle: str
    position_dd: float
    intraday_dd: float
    profit_activate_cny: float
    profit_dd: float
    limit_pct: float
    code: str = ""


@dataclass
class RiskTable:
    default: RiskRow
    codes: Dict[str, RiskRow]


def _row_from(item: dict, code: str = "") -> RiskRow:
    return RiskRow(
        name=str(item.get("name") or code or "股票"),
        settle=str(item.get("settle") or "T1").upper(),
        position_dd=float(item["position_dd"]),
        intraday_dd=float(item["intraday_dd"]),
        profit_activate_cny=float(item["profit_activate_cny"]),
        profit_dd=float(item["profit_dd"]),
        limit_pct=float(item["limit_pct"]),
        code=normalize_code(code) if code else "",
    )


def load_risk_table(params: Optional[dict] = None) -> RiskTable:
    src = params if params is not None else load_strategy_params()
    block = (src or {}).get("intraday_risk") or {}
    default_raw = block.get("default")
    if not default_raw:
        raise KeyError("intraday_risk 缺少股票默认值 default")
    codes: Dict[str, RiskRow] = {}
    for item in block.get("codes") or []:
        code = normalize_code(str(item.get("code") or ""))
        if not code:
            raise KeyError("intraday_risk.codes 有一行没有代码")
        codes[code] = _row_from(item, code)
    return RiskTable(default=_row_from(default_raw), codes=codes)


def risk_row_for(code: str, table: RiskTable) -> RiskRow:
    return table.codes.get(normalize_code(str(code)), table.default)


@dataclass
class _Lot:
    volume: int
    can_use: int
    cost: float
    peak: float
    day_open: Optional[float] = None
    profit_on: bool = False
    profit_peak: float = 0.0
    rebuy: int = 0


@dataclass
class IntradayBook:
    """纯决策。行情和委托由调用方传入，这里不下单。"""

    rows: RiskTable = field(default_factory=load_risk_table)
    lots: Dict[str, _Lot] = field(default_factory=dict)
    day_target: set = field(default_factory=set)
    _day: str = ""
    _opened: bool = False
    _resubmitted: bool = False
    _cancelled: bool = False
    _last_slot: Optional[datetime] = None

    def note_targets(self, codes: Iterable[str]) -> None:
        """09:30 策略要持有的代码。盘中卖掉这些，14:50 才买回。"""
        self.day_target = {normalize_code(str(c)) for c in codes if str(c).strip()}

    def sync(self, code: str, volume: int, can_use: int, cost: float) -> None:
        code = normalize_code(str(code))
        volume = int(volume)
        can_use = int(can_use)
        if volume <= 0:
            self.lots.pop(code, None)
            return
        lot = self.lots.get(code)
        if lot is None:
            self.lots[code] = _Lot(
                volume=volume,
                can_use=can_use,
                cost=float(cost),
                peak=float(cost) if cost > 0 else 0.0,
                day_open=float(cost) if self._opened and cost > 0 else None,
            )
            return
        lot.volume = volume
        lot.can_use = can_use
        if cost > 0:
            lot.cost = float(cost)
        if lot.peak <= 0 and lot.cost > 0:
            lot.peak = lot.cost

    def on_open(self, prices: Dict[str, float], day: str) -> None:
        """09:30。记下开盘价，清掉昨天的盈利监听。持仓最高价留着。"""
        self._day = day
        self._opened = True
        self._resubmitted = False
        self._cancelled = False
        self._last_slot = None
        for code, lot in self.lots.items():
            px = prices.get(code)
            if px is None or px <= 0:
                continue
            lot.day_open = float(px)
            lot.peak = max(lot.peak, float(px))
            lot.profit_on = False
            lot.profit_peak = 0.0
            lot.rebuy = 0

    def tick(
        self,
        now: datetime,
        prices: Dict[str, float],
        open_orders: Optional[List[dict]] = None,
    ) -> List[dict]:
        """按时刻派发。09:30 的调仓不在这里，调用方先跑策略再 note_targets / on_open。"""
        open_orders = open_orders or []
        actions: List[dict] = []
        if self._session(now):
            actions.extend(self.on_prices(now, prices))
        t = now.time()
        if _RESUBMIT <= t < _CANCEL and not self._resubmitted:
            actions.extend(self.on_resubmit(open_orders, prices))
        if _CANCEL <= t < _CLOSE and not self._cancelled:
            actions.extend(self.on_cancel_and_rebuy(open_orders, prices))
        return actions

    def on_prices(self, now: datetime, prices: Dict[str, float]) -> List[dict]:
        """每 5 分钟。返回 sell 动作。午休和 09:30 之前不看。"""
        if not self._session(now) or self._slot(now) == self._last_slot:
            return []
        self._last_slot = self._slot(now)
        actions: List[dict] = []
        for code, lot in list(self.lots.items()):
            px = prices.get(code)
            if px is None or px <= 0 or lot.volume <= 0:
                continue
            px = float(px)
            lot.peak = max(lot.peak, px)
            reason = self._exit_reason(lot, px, risk_row_for(code, self.rows))
            if reason is None:
                continue
            sell_vol = self._sellable(code, lot)
            if sell_vol <= 0:
                continue
            actions.append(
                {"action": "sell", "code": code, "volume": sell_vol, "price": px, "reason": reason}
            )
            if code in self.day_target:
                lot.rebuy += sell_vol
            lot.can_use = max(0, lot.can_use - sell_vol)
            lot.volume = max(0, lot.volume - sell_vol)
            if lot.volume <= 0:
                lot.profit_on = False
        return actions

    def on_resubmit(self, orders: List[dict], prices: Dict[str, float]) -> List[dict]:
        """14:00。未成交单撤掉，按最新价再挂同样的方向和数量。"""
        if self._resubmitted:
            return []
        self._resubmitted = True
        actions: List[dict] = []
        for order in orders:
            code = normalize_code(str(order.get("code") or ""))
            px = prices.get(code)
            if not code or px is None or px <= 0:
                continue
            actions.append({"action": "cancel", "code": code, "order_id": order.get("order_id")})
            actions.append(
                {
                    "action": "resubmit",
                    "code": code,
                    "side": order.get("side"),
                    "volume": int(order.get("volume") or 0),
                    "price": float(px),
                }
            )
        return actions

    def on_cancel_and_rebuy(self, orders: List[dict], prices: Dict[str, float]) -> List[dict]:
        """14:50。先撤光未成交，再把今天策略要的、盘中卖掉的买回来。"""
        if self._cancelled:
            return []
        self._cancelled = True
        actions: List[dict] = []
        for order in orders:
            actions.append(
                {
                    "action": "cancel",
                    "code": normalize_code(str(order.get("code") or "")),
                    "order_id": order.get("order_id"),
                }
            )
        for code, lot in self.lots.items():
            if lot.rebuy <= 0 or code not in self.day_target:
                continue
            px = prices.get(code)
            if px is None or px <= 0:
                continue
            actions.append(
                {
                    "action": "buy",
                    "code": code,
                    "volume": int(lot.rebuy),
                    "price": float(px),
                    "reason": "尾盘买回",
                }
            )
            lot.rebuy = 0
        return actions

    def _exit_reason(self, lot: _Lot, price: float, row: RiskRow) -> Optional[str]:
        if lot.peak > 0 and (lot.peak - price) / lot.peak >= row.position_dd:
            return "持仓回撤"
        if lot.day_open and lot.day_open > 0 and (lot.day_open - price) / lot.day_open >= row.intraday_dd:
            return "日内回撤"
        if lot.cost > 0 and lot.volume > 0:
            pnl = (price - lot.cost) * lot.volume
            if not lot.profit_on:
                if pnl >= row.profit_activate_cny:
                    lot.profit_on = True
                    lot.profit_peak = price
                return None
            lot.profit_peak = max(lot.profit_peak, price)
            if lot.profit_peak > 0 and (lot.profit_peak - price) / lot.profit_peak >= row.profit_dd:
                return "盈利回撤"
        return None

    def _sellable(self, code: str, lot: _Lot) -> int:
        row = risk_row_for(code, self.rows)
        if row.settle == "T0":
            return lot.volume
        return max(0, min(lot.can_use, lot.volume))

    @staticmethod
    def _session(now: datetime) -> bool:
        t = now.time()
        if _OPEN <= t < _LUNCH_START:
            return True
        return _LUNCH_END <= t < _CLOSE

    @staticmethod
    def _slot(now: datetime) -> datetime:
        minute = (now.minute // 5) * 5
        return now.replace(minute=minute, second=0, microsecond=0)
