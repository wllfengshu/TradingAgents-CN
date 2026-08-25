"""
mock xtquant 工具模块
"""
from __future__ import annotations

import logging
import sys
import time
from pydantic import BaseModel, Field
from typing import Dict, List, Optional
from app.models.model_xtquant import AccountInfo, Position

logger = logging.getLogger(__name__)


def get_xtquant_mock_client() -> MockQMTUtil:
    """
    获取 MockQMTUtil 单例实例（模拟交易客户端）

    注意：这是单例模式，多次调用返回同一个实例。
    用于开发/测试环境，无需安装 miniQMT。

    Returns:
        MockQMTUtil 单例实例

    Example:
        # 获取模拟客户端
        mock_client = get_xtquant_mock_client()
        mock_client.connect()

        # 查询账户信息（返回模拟数据）
        account = mock_client.get_account_info()
        positions = mock_client.get_positions()

        mock_client.disconnect()
    """
    return MockQMTUtil()


# ---------------------------------------------------------------------------
# Mock QMT（开发/测试环境使用，无需安装 miniQMT）
# ---------------------------------------------------------------------------

class MockQMTUtil:
    """
    QMTUtil 的模拟实现（单例模式），用于开发/测试环境。
    提供与 QMTUtil 完全一致的接口，返回预设的模拟数据。
    """
    _instance = None
    _lock = None

    def __new__(cls, **kwargs):
        if cls._instance is None:
            import threading
            if cls._lock is None:
                cls._lock = threading.Lock()
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(MockQMTUtil, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, **kwargs):
        # 避免重复初始化
        if self._initialized:
            return

        self._connected = False
        self._initialized = True
        logger.info("MockQMTUtil 初始化（模拟模式）")

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        logger.info("MockQMTUtil 模拟连接成功")
        self._connected = True
        return True

    def disconnect(self) -> None:
        logger.info("MockQMTUtil 模拟断开连接")
        self._connected = False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False

    def _require_connection(self) -> None:
        if not self._connected:
            raise RuntimeError("MockQMT 未连接，请先调用 connect()")

    # ------------------------------------------------------------------
    # 账户查询
    # ------------------------------------------------------------------

    def get_account_info(self) -> AccountInfo:
        """返回模拟账户信息"""
        self._require_connection()
        return AccountInfo(
            cash=100000.00,
            total_value=256800.00,
            frozen_cash=0.00,
        )

    def get_positions(self) -> List[Position]:
        """返回模拟持仓列表"""
        self._require_connection()
        return [
            Position(
                code="600519.SH",
                name="贵州茅台",
                volume=100,
                cost_price=1680.00,
                current_price=1725.50,
            ),
            Position(
                code="000858.SZ",
                name="五粮液",
                volume=300,
                cost_price=145.20,
                current_price=152.80,
            ),
            Position(
                code="601318.SH",
                name="中国平安",
                volume=500,
                cost_price=42.50,
                current_price=48.30,
            ),
        ]

    # ------------------------------------------------------------------
    # 行情查询
    # ------------------------------------------------------------------

    def get_realtime_quote(self, codes: List[str]) -> Dict[str, Dict]:
        """返回模拟实时行情（固定价格 10.00）"""
        self._require_connection()
        return {code: {'lastPrice': 10.00} for code in codes}

    # ------------------------------------------------------------------
    # 下单操作
    # ------------------------------------------------------------------

    def buy(
        self,
        code: str,
        amount: float,
        price: Optional[float] = None,
        remark: str = "AI量化买入",
        volume: Optional[int] = None,
    ) -> Optional[int]:
        """模拟买入操作"""
        self._require_connection()
        quote = self.get_realtime_quote([code])
        current_price = price or quote.get(code, {}).get("lastPrice", 10.0)
        if volume is None:
            volume = int(amount / current_price / 100) * 100
        else:
            volume = (int(volume) // 100) * 100
        if volume <= 0:
            logger.warning(f"[Mock] 买入金额 {amount} 不足以购买 1 手 {code}，跳过")
            return None
        import random
        order_id = random.randint(100000, 999999)
        logger.info(
            f"[Mock] 买入下单: {code} x{volume}股 @ {current_price:.2f}, "
            f"金额≈{volume * current_price:.0f}元, order_id={order_id}"
        )
        return order_id

    def sell(
        self,
        code: str,
        price: Optional[float] = None,
        volume: Optional[int] = None,
        remark: str = "AI量化卖出",
    ) -> Optional[int]:
        """模拟卖出操作"""
        self._require_connection()
        if volume is None:
            positions = self.get_positions()
            pos = next((p for p in positions if p.code == code), None)
            if pos is None or pos.volume <= 0:
                logger.warning(f"[Mock] 未持有 {code}，跳过卖出")
                return None
            volume = pos.volume
        quote = self.get_realtime_quote([code])
        sell_price = price or quote.get(code, {}).get("lastPrice", 10.0)
        import random
        order_id = random.randint(100000, 999999)
        logger.info(
            f"[Mock] 卖出下单: {code} x{volume}股 @ {sell_price:.2f}, "
            f"金额≈{volume * sell_price:.0f}元, order_id={order_id}"
        )
        return order_id

    def cancel_order(self, xt_order_id: int) -> bool:
        """模拟撤单操作"""
        self._require_connection()
        logger.info(f"[Mock] 撤单请求: xt_order_id={xt_order_id}")
        return True
