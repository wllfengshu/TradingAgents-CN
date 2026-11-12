"""
QMT 工具模块。交易和实时行情走本机 HTTP 桥，不连接 miniQMT / XtQuantTrader。

桥约定（与 D:\\GJZQqmt\\1-脚本\\HTTP桥_API.py 一致）：
    POST http://127.0.0.1:18766/rpc
    Header: X-Token
    Body: {"id":1,"method":"...","params":{...}}

历史 K 线和资金流不在桥上，由 stockdb_utils 读取。
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Dict, List, Optional

from zstock.common.entity.account_entity import AccountInfo, Position
from zstock.common.utils.common_utils import normalize_code

logger = logging.getLogger(__name__)

_BRIDGE_SCRIPT = os.environ.get(
    "QMT_BRIDGE_SCRIPT", r"D:\GJZQqmt\1-脚本\HTTP桥_API.py"
)
_DEFAULT_HOST = os.environ.get("QMT_BRIDGE_HOST", "127.0.0.1")
_DEFAULT_PORT = int(os.environ.get("QMT_BRIDGE_PORT", "18766"))

# xtquant 常量：买 23 / 卖 24；最新价 5 / 限价 11。与桥的 price_type 表一致。
_STOCK_BUY = 23
_STOCK_SELL = 24
_LATEST_PRICE = 5
_FIX_PRICE = 11


def _read_bridge_setting(name: str) -> str:
    env = os.environ.get(f"QMT_BRIDGE_{name}")
    if env:
        return env
    try:
        text = open(_BRIDGE_SCRIPT, "r", encoding="utf-8", errors="ignore").read()
    except OSError:
        return ""
    matched = re.search(rf"^{name}\s*=\s*['\"]([^'\"]+)['\"]", text, re.M)
    return matched.group(1) if matched else ""


def to_qmt_code(code: str) -> str:
    """6 位代码补成桥使用的 600000.SH / 159915.SZ。已带后缀的原样返回。"""
    raw = (code or "").strip().upper()
    if "." in raw:
        return raw
    c = normalize_code(raw)
    if c.startswith(("5", "6", "9", "11", "204")):
        return c + ".SH"
    if c.startswith(("4", "8")) and not c.startswith("399"):
        return c + ".BJ"
    return c + ".SZ"


class QMTUtil:
    """通过 HTTP 桥访问国金 QMT。使用前调用 connect()。"""

    _instance = None
    _lock = None

    def __new__(cls, install_path: str = None, account_id: str = None, session_id: int = 9999):
        if cls._instance is None:
            import threading
            if cls._lock is None:
                cls._lock = threading.Lock()
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(QMTUtil, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, install_path: str = None, account_id: str = None, session_id: int = 9999):
        if self._initialized:
            return
        self.install_path = install_path or r"D:\GJZQqmt\国金证券QMT交易端"
        self.account_id = account_id or _read_bridge_setting("ACCOUNT_ID")
        self.session_id = session_id
        self.host = _DEFAULT_HOST
        self.port = _DEFAULT_PORT
        self.token = os.environ.get("QMT_BRIDGE_TOKEN") or _read_bridge_setting("TOKEN")
        self._connected = False
        self._rpc_id = 0
        self._initialized = True

    def connect(self) -> bool:
        """确认桥在监听。桥进程由 QMT 端内策略提供，这里只发 ping。"""
        if not self.token:
            logger.error("未找到桥 Token。设置环境变量 QMT_BRIDGE_TOKEN，或保留 HTTP桥_API.py")
            return False
        try:
            payload = self.rpc("ping")
        except Exception as e:
            logger.error("QMT 桥连接失败: %s", e)
            self._connected = False
            return False
        self._connected = True
        logger.info(
            "QMT 桥已连接: http://%s:%s account=%s ping=%s",
            self.host, self.port, self.account_id, payload,
        )
        return True

    def disconnect(self) -> None:
        self._connected = False
        logger.info("QMT 桥客户端已断开（不关闭端内桥）")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False

    def _require_connection(self) -> None:
        if not self._connected:
            raise RuntimeError("QMT 桥未连接，请先调用 connect()")

    def rpc(self, method: str, params: Optional[dict] = None, timeout: float = 8.0) -> object:
        """POST /rpc。成功返回 result；桥返回 ok=false 时抛出 RuntimeError。"""
        self._rpc_id += 1
        body = json.dumps(
            {"id": self._rpc_id, "method": method, "params": params or {}},
            ensure_ascii=False,
        ).encode("utf-8")
        url = f"http://{self.host}:{self.port}/rpc"
        request = urllib.request.Request(url, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("X-Token", self.token)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"QMT 桥 HTTP {e.code}: {detail}") from e
        if not isinstance(raw, dict) or not raw.get("ok"):
            message = raw.get("error") if isinstance(raw, dict) else raw
            raise RuntimeError(f"QMT 桥 {method} 失败: {message}")
        return raw.get("result")

    def get_account_info(self) -> AccountInfo:
        self._require_connection()
        asset = self.rpc("query_stock_asset", {"account_id": self.account_id})
        if not isinstance(asset, dict):
            logger.warning("query_stock_asset 无数据，使用默认值")
            return AccountInfo(cash=0.0, total_value=0.0)
        return AccountInfo(
            cash=float(asset.get("cash") or 0.0),
            total_value=float(asset.get("total_asset") or asset.get("balance") or 0.0),
            frozen_cash=float(asset.get("frozen_cash") or 0.0),
        )

    def get_positions(self) -> List[Position]:
        self._require_connection()
        rows = self.rpc("query_stock_positions", {"account_id": self.account_id}) or []
        positions: List[Position] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            volume = int(float(row.get("volume") or 0))
            if volume <= 0:
                continue
            market_value = float(row.get("market_value") or 0.0)
            positions.append(Position(
                code=str(row.get("stock_code") or ""),
                name="",
                volume=volume,
                cost_price=float(row.get("open_price") or 0.0),
                current_price=(market_value / volume) if volume else 0.0,
            ))
        logger.info("查询持仓: %d 只", len(positions))
        return positions

    def get_realtime_quote(self, codes: List[str]) -> Dict[str, Dict]:
        self._require_connection()
        qmt_codes = [to_qmt_code(code) for code in codes]
        try:
            ticks = self.rpc("get_full_tick", {"codes": qmt_codes}) or {}
        except Exception as e:
            logger.error("获取实时行情失败: %s", e)
            return {}
        if not isinstance(ticks, dict):
            return {}
        result: Dict[str, Dict] = {}
        for original, qmt_code in zip(codes, qmt_codes):
            tick = ticks.get(qmt_code) or ticks.get(original) or {}
            if not isinstance(tick, dict):
                continue
            last = tick.get("lastPrice", tick.get("last_price", 0.0))
            result[original] = {"lastPrice": float(last or 0.0)}
        return result

    def query_orders(self, cancelable_only: bool = False) -> list:
        self._require_connection()
        return self.rpc(
            "query_stock_orders",
            {"account_id": self.account_id, "cancelable_only": cancelable_only},
        ) or []

    def query_trades(self) -> list:
        self._require_connection()
        return self.rpc("query_stock_trades", {"account_id": self.account_id}) or []

    def buy(
        self,
        code: str,
        amount: float,
        price: Optional[float] = None,
        remark: str = "AI量化买入",
        volume: Optional[int] = None,
    ) -> Optional[int]:
        self._require_connection()
        current_price = price
        if current_price is None:
            tick = self.get_realtime_quote([code]).get(code, {})
            current_price = float(tick.get("lastPrice") or 0.0)
            if current_price <= 0:
                logger.error("无法获取 %s 当前价格，取消买入", code)
                return None
        if volume is not None:
            volume = (int(volume) // 100) * 100
        else:
            volume = int(amount / current_price / 100) * 100
        if volume <= 0:
            logger.warning("买入金额 %s 不足以购买 1 手 %s（价格=%s），跳过", amount, code, current_price)
            return None
        price_type = _FIX_PRICE if price is not None else _LATEST_PRICE
        order_price = price if price is not None else current_price
        return self._submit_order(code, _STOCK_BUY, volume, price_type, order_price, remark)

    def sell(
        self,
        code: str,
        price: Optional[float] = None,
        volume: Optional[int] = None,
        remark: str = "AI量化卖出",
    ) -> Optional[int]:
        self._require_connection()
        qmt_code = to_qmt_code(code)
        if volume is None:
            pos = next((p for p in self.get_positions() if p.code in (code, qmt_code)), None)
            if pos is None or pos.volume <= 0:
                logger.warning("未持有 %s，跳过卖出", code)
                return None
            volume = pos.volume
        sell_price = price
        if sell_price is None:
            tick = self.get_realtime_quote([code]).get(code, {})
            sell_price = float(tick.get("lastPrice") or 0.0)
            if sell_price <= 0:
                logger.error("无法获取 %s 当前价格，取消卖出", code)
                return None
        price_type = _FIX_PRICE if price is not None else _LATEST_PRICE
        return self._submit_order(code, _STOCK_SELL, int(volume), price_type, sell_price, remark)

    def _submit_order(
        self,
        code: str,
        order_type: int,
        volume: int,
        price_type: int,
        price: float,
        remark: str,
    ) -> Optional[int]:
        try:
            result = self.rpc("order_stock", {
                "account_id": self.account_id,
                "stock_code": to_qmt_code(code),
                "order_type": order_type,
                "order_volume": int(volume),
                "price_type": price_type,
                "price": float(price),
                "strategy_name": "ai_quant",
                "order_remark": remark,
            })
        except Exception as e:
            logger.error("下单 %s 失败: %s", code, e)
            return None
        if not isinstance(result, dict):
            return None
        order_id = result.get("order_id")
        logger.info(
            "下单已入桥队列: %s x%s @ %.2f order_id=%s",
            code, volume, price, order_id,
        )
        try:
            return int(order_id)
        except (TypeError, ValueError):
            return None

    def cancel_order(self, xt_order_id: int) -> bool:
        self._require_connection()
        try:
            result = self.rpc("cancel_order_stock", {
                "account_id": self.account_id,
                "order_id": str(xt_order_id),
            })
            logger.info("撤单已入桥队列: order_id=%s result=%s", xt_order_id, result)
            return True
        except Exception as e:
            logger.error("撤单 %s 失败: %s", xt_order_id, e)
            return False

