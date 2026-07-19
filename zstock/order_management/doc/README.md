# Order Management 模块（执行层 / 第 4 层）

## 概述

`order_management` 是量化交易系统的**执行层**，负责将策略层输出的目标持仓转化为可执行订单，提交给交易接口（XtQuant），并完成成交处理与持仓对账。

系统分层位置：

```
第 0 层：治理与监控层
        ↓
第 1 层：数据层 (data_management)
        ↓
第 2 层：研究层 (factor_management)
        ↓
第 3 层：策略层 (strategy_management)
        ↓
第 4 层：执行层 (order_management)  <-- 当前模块
```

## 核心流程

```
目标持仓
    ↓
订单生成 (OrderGenerator)
    ↓
策略选择 (ExecutionStrategy)
    ↓
订单执行 (XtQuantExecutor)
    ↓
成交处理 (TradeSettlement)
    ↓
持仓对账
    ↓
执行完成
```

## 核心职责

| 模块 | 职责 | 输入 | 输出 |
|------|------|------|------|
| `OrderGenerator` | 生成订单 | 目标/当前持仓 | 订单列表 |
| `ExecutionStrategy` | 按时段分配执行策略 | 订单 + 当前时间 | 待执行子订单 |
| `XtQuantExecutor` | 提交与跟踪订单 | 订单 | 订单状态/成交回报 |
| `TradeSettlement` | 成交处理与持仓更新 | 成交信息 | 本地持仓/成交记录 |
| `OrderManagementPipeline` | 串联完整执行流程 | 目标持仓 | 执行结果摘要 |

## 项目结构

```
zstock/order_management/
├── __init__.py
├── order_generator.py
├── xtquant_executor.py
├── trade_settlement.py
├── execution_strategy.py
├── pipeline.py
├── test_order_management.py
├── verify.py
└── doc/
    └── README.md
```

## 模块说明

### 1) `OrderGenerator`

**作用**：比较目标持仓与当前持仓，生成买卖订单。

**关键能力**：
- 持仓差异计算（卖出、买入、调仓）
- 订单序列号管理
- 订单状态更新
- 多格式导出（`DataFrame` / `List` / `Dict`）

**示例**：

```python
from zstock.order_management import OrderGenerator

gen = OrderGenerator()
orders = gen.generate_orders(
    target_positions=target_positions,
    current_positions=current_positions,
    trade_date='2024-01-01',
)

gen.update_order_status(orders[0].order_id, 'filled')
orders_df = gen.export_orders(format='dataframe')
```

### 2) `XtQuantExecutor`

**作用**：执行订单、查询账户和持仓、管理订单生命周期。

**关键能力**：
- 自动识别 QMT 环境
- Mock 模式（无 QMT 时可开发调试）
- 订单提交/查询/撤销
- 账户信息与持仓查询

**示例**：

```python
from zstock.order_management import XtQuantExecutor

executor = XtQuantExecutor(qmt_util=qmt_client)  # 无 qmt_client 时可自动进入 mock
xt_order_id = executor.submit_order(order)
order_info = executor.query_order(order.order_id)
account = executor.get_account_info()
positions = executor.get_positions()
executor.cancel_order(order.order_id)
```

### 3) `TradeSettlement`

**作用**：处理成交回报，更新本地持仓，并执行对账。

**关键能力**：
- 成交回报处理
- 持仓自动更新
- 成交记录留存
- 本地 vs XtQuant 持仓对账
- 滑点计算

**示例**：

```python
from zstock.order_management import TradeSettlement

settlement = TradeSettlement()
settlement.handle_trade_report(
    order_id='20240101001',
    filled_volume=1000,
    filled_price=10.5,
    order_direction='buy',
    stock_code='SH600000',
)

reconcile_result = settlement.reconcile_positions(xtquant_positions)
slippage = settlement.calculate_slippage(order_price=10.0, filled_price=10.05)
```

### 4) `ExecutionStrategy`

**作用**：按市场时间段选择执行策略，并控制订单执行节奏。

**策略类型**：
1. `auction_sell`（集合竞价卖出）
   - 时间：09:25-09:30
2. `twap_buy`（TWAP 分批买入）
   - 时间：09:30-14:55
3. `final`（尾盘补单）
   - 时间：14:55-15:00
4. `closed`（市场关闭）
   - 交易时段外

**示例**：

```python
from zstock.order_management import ExecutionStrategy

strategy = ExecutionStrategy()
current = strategy.get_current_strategy()
submitted_count = strategy.execute_with_strategy(
    orders=orders,
    executor=executor,
    strategy=None,  # None 表示自动按当前时段选择
)
stats = strategy.get_strategy_stats()
```

### 5) `OrderManagementPipeline`

**作用**：把订单生成、策略执行、执行器查询与对账集成为一条完整管道。

**典型步骤**：
1. 生成订单
2. 选择并执行策略
3. 查询账户信息
4. 查询持仓
5. 对账验证
6. 产出执行摘要

## 快速开始

### 一体化执行

```python
from zstock.order_management.pipeline import OrderManagementPipeline
import pandas as pd

pipeline = OrderManagementPipeline()

target_positions = pd.DataFrame({
    'stock_code': ['SH600000', 'SH600001'],
    'target_shares': [1000, 500],
})

result = pipeline.execute_full_pipeline(target_positions)

print(f"状态: {result['status']}")
print(f"生成订单: {result['statistics']['orders_generated']}")
print(f"已提交: {result['statistics']['orders_submitted']}")
```

### 分步执行

```python
from zstock.order_management import OrderGenerator, XtQuantExecutor, TradeSettlement

# 1) 生成订单
orders = OrderGenerator().generate_orders(target_positions)

# 2) 执行订单
executor = XtQuantExecutor()
for order in orders:
    executor.submit_order(order)

# 3) 处理成交
settlement = TradeSettlement()
settlement.handle_trade_report(
    order_id=orders[0].order_id,
    filled_volume=1000,
    filled_price=10.5,
    order_direction='buy',
    stock_code='SH600000',
)

# 4) 对账
positions = executor.get_positions()
result = settlement.reconcile_positions(positions)
```

## 测试与验证

### 单元测试

当前用例覆盖：
- `TestOrderGenerator`（2 个）
- `TestXtQuantExecutor`（3 个）
- `TestTradeSettlement`（2 个）
- `TestExecutionStrategy`（1 个）
- `TestOrderManagementPipeline`（1 个）

合计 **9 个测试**。

运行方式：

```bash
cd zstock/order_management
python test_order_management.py
```

### 验证脚本

`verify.py` 可用于演示完整的订单生成、执行、成交处理和对账流程，并输出执行统计。

## 与上下游集成

### 上游输入（策略层）

```
StrategyPipeline 生成目标持仓
        ↓
OrderManagementPipeline.execute_full_pipeline()
```

### 下游输出（监控层）

```
订单状态、成交记录、持仓更新
        ↓
MonitorManagement
```

## 关键参数

### TWAP 参数

```python
TWAP_SLICES = 5      # 分成 5 批
TWAP_INTERVAL = 30   # 每 30 秒一批
```

### 市场时间段

```text
09:15-09:30   集合竞价
09:30-14:55   连续交易
14:55-15:00   尾盘集合
其他时间      市场关闭
```

## 最佳实践

1. 开发阶段优先使用 Mock 模式，降低外部依赖。
2. 每次交易后执行持仓对账，及时发现偏差。
3. 持续监控滑点，优化执行成本。
4. 对下单失败实现重试和告警机制。

## 注意事项

1. Mock 模式仅用于开发测试，生产必须连接真实交易环境。
2. 执行策略依赖系统时间，需保证服务器时钟同步。
3. 出现本地与实盘持仓不一致时，应先停止自动交易并人工核验。

## 阶段完成状态（Phase 5）

- 已完成订单生成、执行器、成交处理、执行策略与管道集成。
- 已完成单元测试与验证脚本。
- 当前阶段目标：**执行层开发完成（100%）**。

后续建议：
1. Phase 6：监控层（事前治理、事中监控、事后归因）。
2. Phase 8：全链路集成与调度（含定时任务）。
3. Phase 9：模拟盘运行与稳定性观察。

---

**状态**: 可用于联调与模拟环境验证  
**版本**: 1.0  
**最后更新**: 2026-06-20
