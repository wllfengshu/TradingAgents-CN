"""
xtquant 工具模块 — 封装 miniQMT 连接、账户查询、下单操作。不包含其他的操作！
基于迅投 QMT xtquant SDK（通过动态 sys.path 注入加载）
"""
from __future__ import annotations

import logging
import sys
import time
from pydantic import BaseModel, Field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 账户 & 持仓
# ---------------------------------------------------------------------------

class AccountInfo(BaseModel):
    cash: float = Field(description="可用资金(元)")
    total_value: float = Field(description="总资产(元)")
    frozen_cash: float = Field(default=0.0, description="冻结资金(元)")


class Position(BaseModel):
    code: str = Field(description="股票代码，如 600000.SH")
    name: str = Field(default="", description="股票名称")
    volume: int = Field(description="持仓数量(股)")
    cost_price: float = Field(description="持仓成本价")
    current_price: float = Field(default=0.0, description="当前价格")

    @property
    def market_value(self) -> float:
        return self.volume * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        return (self.current_price - self.cost_price) * self.volume



def _ensure_xtquant_importable(install_path: str) -> None:
    """将 QMT 安装目录下的 xtquant 注入 sys.path（确保优先加载QMT版本）"""
    import os

    candidates = [
        os.path.join(install_path, "userdata_mini"),
        os.path.join(install_path, "userdata"),
        install_path,
    ]
    
    for path in candidates:
        if os.path.isdir(path):
            xtquant_path = os.path.join(path, "xtquant")
            if os.path.isdir(xtquant_path):
                sys.path.insert(0, path)
                logger.debug(f"已将 QMT xtquant 路径注入 sys.path: {path}")
                return
    
    for path in candidates:
        if path not in sys.path and os.path.isdir(path):
            sys.path.insert(0, path)
            logger.debug(f"已将 {path} 注入 sys.path")


class QMTUtil:
    """
    miniQMT 操作封装。
    使用前必须调用 connect()，或通过上下文管理器（with QMTUtil(...) as q:）使用。
    """

    def __init__(self, install_path: str, account_id: str, session_id: int = 9999):
        self._account = None
        self.install_path = install_path
        self.account_id = account_id
        self.session_id = session_id
        self._trader = None
        self._connected = False

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """
        初始化 XtQuantTrader 连接。
        Returns:
            True=连接成功，False=失败
        """
        try:
            import os
            _ensure_xtquant_importable(self.install_path)
            from xtquant.xttrader import XtQuantTrader
            from xtquant.xttype import StockAccount

            userdata_path = os.path.join(self.install_path, "userdata_mini")
            if not os.path.isdir(userdata_path):
                userdata_path = os.path.join(self.install_path, "userdata")
            if not os.path.isdir(userdata_path):
                userdata_path = self.install_path

            logger.info(f"QMT 连接参数: install_path={self.install_path}, userdata_path={userdata_path}, account_id={self.account_id}, session_id={self.session_id}")
            self._trader = XtQuantTrader(userdata_path, self.session_id)
            self._trader.start()
            time.sleep(0.5)
            self._account = StockAccount(self.account_id, 'STOCK')

            connect_result = self._trader.connect()
            logger.info(f"QMT connect 结果: {connect_result}")
            if connect_result != 0:
                logger.error(f"QMT 连接失败，错误码: {connect_result}")
                return False

            subscribe_result = self._trader.subscribe(self._account)
            logger.info(f"QMT subscribe 结果: {subscribe_result}")
            if subscribe_result != 0:
                logger.error(f"QMT 账户订阅失败，错误码: {subscribe_result}")
                return False

            self._connected = True
            logger.info(f"QMT 连接成功: account={self.account_id}")
            return True

        except ImportError as e:
            logger.error(f"xtquant 导入失败，请检查安装路径: {e}")
            return False
        except Exception as e:
            logger.error(f"QMT 连接异常: {e}", exc_info=True)
            return False

    def disconnect(self) -> None:
        if self._trader and self._connected:
            try:
                self._trader.stop()
                logger.info("QMT 已断开连接")
            except Exception as e:
                logger.warning(f"QMT 断开连接时异常: {e}")
        self._connected = False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False

    def _require_connection(self) -> None:
        if not self._connected:
            raise RuntimeError("QMT 未连接，请先调用 connect()")

    # ------------------------------------------------------------------
    # 账户查询
    # ------------------------------------------------------------------

    def get_account_info(self) -> AccountInfo:
        """查询账户资产（可用资金、总资产）"""
        self._require_connection()
        asset = self._trader.query_stock_asset(self._account)
        if asset is None:
            logger.warning("query_stock_asset 返回 None，使用默认值")
            return AccountInfo(cash=0.0, total_value=0.0)

        return AccountInfo(
            cash=float(asset.cash),
            total_value=float(asset.total_asset),
            frozen_cash=float(getattr(asset, "frozen_cash", 0.0)),
        )

    def get_positions(self) -> List[Position]:
        """查询当前持仓列表"""
        self._require_connection()
        raw_positions = self._trader.query_stock_positions(self._account)
        if not raw_positions:
            return []

        positions = []
        for p in raw_positions:
            # 只包含有持仓量的股票
            vol = int(getattr(p, "volume", 0))
            if vol <= 0:
                continue
            positions.append(Position(
                code=p.stock_code,
                name=getattr(p, "stock_name", ""),
                volume=vol,
                cost_price=float(getattr(p, "open_price", 0.0)),
                current_price=float(getattr(p, "market_price", 0.0)),
            ))

        logger.info(f"查询持仓: {len(positions)} 只股票")
        return positions

    # ------------------------------------------------------------------
    # 实时行情
    # ------------------------------------------------------------------

    def get_realtime_quote(self, codes: List[str]) -> Dict[str, Dict]:
        """
        获取实时全档行情（使用 xtdata）
        Args:
            codes: 股票代码列表，格式如 ["600000.SH", "000001.SZ"]
        Returns:
            {code: {lastPrice, open, high, low, volume, amount, ...}}
        """
        self._require_connection()
        try:
            from xtquant import xtdata
            ticks = xtdata.get_full_tick(codes)
            return ticks or {}
        except Exception as e:
            logger.error(f"获取实时行情失败: {e}")
            return {}

    # ------------------------------------------------------------------
    # 下单操作
    # ------------------------------------------------------------------

    def buy(
        self,
        code: str,
        amount: float,
        price: Optional[float] = None,
        remark: str = "AI量化买入",
    ) -> Optional[int]:
        """
        买入股票。
        Args:
            code:   股票代码（含市场后缀，如 600000.SH）
            amount: 买入金额（元）
            price:  限价价格，None=市价
            remark: 备注
        Returns:
            订单 ID（失败返回 None）
        """
        self._require_connection()

        # 获取当前价格以计算股数
        quote = self.get_realtime_quote([code])
        current_price = price
        if current_price is None:
            tick = quote.get(code, {})
            current_price = tick.get("lastPrice", 0.0)
            if current_price <= 0:
                logger.error(f"无法获取 {code} 当前价格，取消买入")
                return None

        # 计算买入手数（100股为一手，向下取整）
        volume = int(amount / current_price / 100) * 100
        if volume <= 0:
            logger.warning(f"买入金额 {amount} 不足以购买 1 手 {code}（价格={current_price}），跳过")
            return None

        try:
            from xtquant.xtconstant import (
                FIX_PRICE,          # 限价
                LATEST_PRICE,       # 最新价
                STOCK_BUY,
            )

            price_type = FIX_PRICE if price is not None else LATEST_PRICE
            order_price = price if price is not None else current_price

            order_id = self._trader.order_stock(
                self._account,
                code,
                STOCK_BUY,
                volume,
                price_type,
                order_price,
                "ai_quant",
                remark,
            )
            logger.info(
                f"买入下单: {code} x{volume}股 @ {order_price:.2f}, "
                f"金额≈{volume * order_price:.0f}元, order_id={order_id}"
            )
            return order_id

        except Exception as e:
            logger.error(f"买入 {code} 失败: {e}")
            return None

    def sell(
        self,
        code: str,
        price: Optional[float] = None,
        volume: Optional[int] = None,
        remark: str = "AI量化卖出",
    ) -> Optional[int]:
        """
        卖出股票（默认卖出全部持仓）。
        Args:
            code:   股票代码
            price:  限价价格，None=市价
            volume: 卖出数量，None=卖出全部
            remark: 备注
        """
        self._require_connection()

        # 查询持仓数量
        if volume is None:
            positions = self.get_positions()
            pos = next((p for p in positions if p.code == code), None)
            if pos is None or pos.volume <= 0:
                logger.warning(f"未持有 {code}，跳过卖出")
                return None
            volume = pos.volume

        # 获取当前价格（用于市价单）
        sell_price = price
        if sell_price is None:
            quote = self.get_realtime_quote([code])
            tick = quote.get(code, {})
            sell_price = tick.get("lastPrice", 0.0)
            if sell_price <= 0:
                logger.error(f"无法获取 {code} 当前价格，取消卖出")
                return None

        try:
            from xtquant.xtconstant import (
                FIX_PRICE,
                LATEST_PRICE,
                STOCK_SELL,
            )

            price_type = FIX_PRICE if price is not None else LATEST_PRICE

            order_id = self._trader.order_stock(
                self._account,
                code,
                STOCK_SELL,
                volume,
                price_type,
                sell_price,
                "ai_quant",
                remark,
            )
            logger.info(
                f"卖出下单: {code} x{volume}股 @ {sell_price:.2f}, "
                f"金额≈{volume * sell_price:.0f}元, order_id={order_id}"
            )
            return order_id

        except Exception as e:
            logger.error(f"卖出 {code} 失败: {e}")
            return None


def create_qmt_util_from_settings() -> QMTUtil:
    """从全局 settings 创建 QMTUtil 实例"""
    from config.settings import settings

    cfg = settings.qmt
    return QMTUtil(
        install_path=cfg.install_path,
        account_id=cfg.account_id,
        session_id=cfg.session_id,
    )
