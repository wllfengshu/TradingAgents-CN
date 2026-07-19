"""
zstock 订单管理模块 (执行层)

这是系统的第4层（执行层），负责：
1. 订单生成：根据目标持仓生成订单
2. XtQuant 执行：通过 XtQuant 提交订单到券商
3. 成交回报处理：处理订单成交、持仓更新
4. 执行策略：集合竞价、TWAP、尾盘补单等

模块结构：
- order_generator: 订单生成
- xtquant_executor: XtQuant 执行接口
- trade_settlement: 成交回报处理
- execution_strategy: 执行策略
"""

# 核心类
from .order_generator import OrderGenerator
from .xtquant_executor import XtQuantExecutor
from .trade_settlement import TradeSettlement
from .execution_strategy import ExecutionStrategy

__all__ = [
    'OrderGenerator',
    'XtQuantExecutor',
    'TradeSettlement',
    'ExecutionStrategy',
]
