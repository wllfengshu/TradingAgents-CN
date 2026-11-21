"""
zstock 订单管理模块（执行层）

目标持仓 → 订单 → 按时段提交 QMT → 对账。

模块：
- orders: 订单生成与对账
- broker: QMT 桥客户端与下单
- timing: 集合竞价 / TWAP / 尾盘
- intraday: 09:30 之后的持仓回撤、日内回撤、盈利回撤和尾盘买回
- pipeline: 流程编排
"""

from .intraday import IntradayBook
from .orders import OrderGenerator, TradeSettlement
from .broker import XtQuantExecutor, QMTClientFactory, QMTConnectionError
from .timing import ExecutionStrategy
from .pipeline import OrderManagementPipeline

__all__ = [
    "OrderGenerator",
    "TradeSettlement",
    "XtQuantExecutor",
    "QMTClientFactory",
    "QMTConnectionError",
    "ExecutionStrategy",
    "IntradayBook",
    "OrderManagementPipeline",
]
