# Order Management 模块（执行层 / 第 4 层）

## 概述

`order_management` 是量化交易系统的执行层：目标持仓 → 订单 → 按时段提交 QMT → 对账。

系统分层位置：

```text
第 0 层：治理与监控层
        ↓
第 1 层：数据层（data_management）
        ↓
第 2 层：研究层（factor_management）
        ↓
第 3 层：策略层（strategy_management）
        ↓
第 4 层：执行层（order_management）  <- 当前模块
```

```text
orders.py     订单生成与成交对账
broker.py     QMT 桥客户端与下单 / 回报
timing.py     集合竞价 / TWAP / 尾盘时段策略
intraday.py   盘中回撤卖出、14:00 重挂、14:50 撤单和尾盘买回
pipeline.py   流程编排
```

日内阈值在 `strategy_params.json` 的 `intraday_risk`。`default` 是全部股票的统一参数。行业 ETF、国债、黄金在 `codes` 里按代码各写一行，例如 `518880`。表里没有的代码用 `default`。09:30 仍由策略层给出目标持仓，再交给 `IntradayBook.note_targets` 和 `on_open`。之后每 5 分钟调用 `tick`。

典型串联：

```text
StrategyPipeline → final_holdings (code + weight)
                → OrderManagementPipeline.execute_full_pipeline(...)
```

工作流：订单生成 → 按时段执行 → XtQuant 提交 → 目标股数 vs 券商持仓对账。

订单集合：`zstock_orders`（`created` → `pending` → `filled` / `partial_filled` / `cancelled` / `rejected`）。

---

## 流程图

### 总览：`execute_full_pipeline`

```mermaid
flowchart TD
  A[策略层 final_holdings<br/>code + weight] --> B[OrderManagementPipeline.execute_full_pipeline]

  B --> C{目标持仓为空?}
  C -->|是| C1[返回 no_target]
  C -->|否| D{mock_mode 且<br/>allow_mock=False?}
  D -->|是| D1[返回 failed<br/>拒绝纸面下单]
  D -->|否| E[XtQuantExecutor.get_account_info]

  E --> F{total_capital 未传入?}
  F -->|是| F1{账户资金有效?}
  F1 -->|否| F2[返回 failed<br/>拒绝默认资金下单]
  F1 -->|是| G
  F -->|否| G[XtQuantExecutor.get_positions]

  G --> H{券商持仓查询?}
  H -->|None| H1[返回 failed<br/>拒绝当成空仓]
  H -->|OK| I{current_positions<br/>未传或为空?}
  I -->|是| I1[用券商持仓填 current]
  I -->|否| J
  I1 --> J{price_map 未传入?}
  J -->|是| J1[get_price_map 目标+当前代码]
  J -->|否| K
  J1 --> K[TradeSettlement.sync_positions_from_broker]

  K --> L[OrderGenerator.generate_orders]
  L --> M[OrderGenerator.lots_from_positions]
  M --> N{有订单?}
  N -->|否| N1[reconcile_target_vs_broker<br/>返回 no_orders]
  N -->|是| O[persist_orders → Mongo created]

  O --> P[ExecutionStrategy.get_current_strategy<br/>或入参 strategy]
  P --> Q{strategy == closed?}
  Q -->|是| Q1[返回 failed<br/>休市/静默期]
  Q -->|否| R[execute_with_strategy<br/>→ XtQuantExecutor.submit_order]

  R --> S[query_all_orders]
  S --> T[get_account_info]
  T --> U[get_positions 刷新]
  U --> V[reconcile_target_vs_broker]
  V --> W{汇总 status}
  W -->|failed_count>0| W1[failed]
  W -->|deferred_count>0| W2[partial<br/>需换时段再跑]
  W -->|submitted>0| W3[success]
  W -->|否则| W4[failed 提交 0 笔]
```

### 细节 1：订单生成 `OrderGenerator.generate_orders`

```mermaid
flowchart TD
  A[目标持仓 DF + 当前持仓 DF<br/>+ price_map + total_capital] --> B[规范为 stock_code 6 位]
  B --> C[当前持仓 → current_map]
  C --> D[集合差分]
  D --> D1[卖出: 当前有、目标无]
  D --> D2[买入: 目标有、当前无]
  D --> D3[调仓: 交集]

  D1 --> E1[卖单 volume=当前股数]
  D2 --> E2[目标股数: target_shares<br/>或 weight×资金/价 整手]
  D3 --> E3[目标股数 − 当前股数<br/>>0 买 / <0 卖]

  E2 --> F{有有效行情价?}
  E3 --> F
  F -->|否| F1[跳过该标的<br/>保留当前仓 不误清仓]
  F -->|是| G[构造 Order<br/>direction / volume / price]
  E1 --> G
  F1 --> H[返回订单列表]
  G --> H
```

### 细节 2：时段策略 `ExecutionStrategy`

```mermaid
flowchart TD
  A[execute_with_strategy] --> B{strategy?}

  B -->|auction_sell<br/>09:15-09:25| C[只提交卖单 强制限价]
  C --> C1[买单 → deferred<br/>等连续竞价]
  C --> C2[submit_order 卖单<br/>auction_sell_count++]

  B -->|twap_buy<br/>09:30-11:30 / 13:00-14:57| D[卖单可整笔提交<br/>twap_sell_count++]
  D --> D1[买单按手数拆 N 批<br/>批间隔 sleep]
  D1 --> D2[每批 submit_order<br/>twap_buy_count++]

  B -->|final<br/>14:57-15:00| E[剩余买卖整笔提交<br/>强制限价]
  E --> E1[final_buy_count++ / 卖同理]

  B -->|closed<br/>静默/午休/盘外| F[全部 failed<br/>不发单]

  C2 --> G[返回 submitted / deferred / failed]
  C1 --> G
  D2 --> G
  E1 --> G
  F --> G
```

### 细节 3：券商下单与回报 `XtQuantExecutor`

```mermaid
flowchart TD
  subgraph 提交
    S1[submit_order] --> S2{DB 已有同 order_id<br/>且已 pending/终态?}
    S2 -->|是| S3[幂等跳过<br/>返回已有 xt_order_id]
    S2 -->|否| S4[6位码 → to_qmt_code]
    S4 --> S5{direction?}
    S5 -->|buy| S6[QMTUtil.buy]
    S5 -->|sell| S7[QMTUtil.sell]
    S6 --> S8{xt_order_id > 0?}
    S7 --> S8
    S8 -->|是| S9[upsert zstock_orders<br/>status=pending]
    S8 -->|否| S10[返回失败]
  end

  subgraph 撤单
    C1[cancel_order] --> C2{已终态?}
    C2 -->|是| C3[忽略]
    C2 -->|否| C4[QMTUtil.cancel_order]
    C4 --> C5[DB → pending_cancel<br/>成败等回报]
  end

  subgraph 回报
    R1[on_order_status] --> R2[映射 broker 状态<br/>accepted→pending 等]
    R2 --> R3{本地已终态?}
    R3 -->|是| R4[忽略回退]
    R3 -->|否| R5[更新 status / filled_volume]

    R6[on_trade] --> R7{broker_trade_id<br/>已记过?}
    R7 -->|是| R8[幂等忽略]
    R7 -->|否| R9[$push fills<br/>累加 filled_volume]
    R9 --> R10{满量?}
    R10 -->|是| R11[status=filled]
    R10 -->|否| R12[status=partial_filled]
  end
```

### 细节 4：订单状态机（`zstock_orders`）

```mermaid
stateDiagram-v2
  [*] --> created: persist_orders
  created --> pending: submit_order 成功
  pending --> partial_filled: on_trade 部分成交
  pending --> filled: on_trade / on_order_status 全成
  pending --> pending_cancel: cancel_order 已发请求
  partial_filled --> filled: 继续成交满量
  partial_filled --> pending_cancel: cancel_order
  pending_cancel --> cancelled: on_order_status cancelled
  pending_cancel --> filled: 撤单前已全成
  pending_cancel --> partial_filled: 部分成交后仍挂着
  pending --> rejected: on_order_status rejected
  pending --> cancelled: on_order_status cancelled
  filled --> [*]
  cancelled --> [*]
  rejected --> [*]
```

### 细节 5：对账 `TradeSettlement.reconcile_target_vs_broker`

```mermaid
flowchart LR
  A[target_lots<br/>stock_code + volume] --> C[逐码对比]
  B[券商 positions<br/>code + volume] --> C
  C --> D[matched 股数一致]
  C --> E[discrepancies 不一致<br/>或单边有仓]
  D --> F[status: ok / warning / error]
  E --> F
```

模块协作关系：

```mermaid
flowchart LR
  P[pipeline<br/>编排] --> OG[orders<br/>生成/落库]
  P --> ES[timing<br/>选时段]
  P --> XE[broker<br/>下单/查账]
  P --> TS[orders<br/>对账]
  ES -->|submit_order| XE
  OG -->|persist created| DB[(Mongo<br/>zstock_orders)]
  XE -->|pending/fills| DB
  XE <-->|HTTP 桥| QMT[QMT]
  TS -->|对比| XE
```

---

## 公有方法一览

### 1. `OrderGenerator`（`orders.py`）

| 方法 | 作用 |
|------|------|
| `__init__` | 初始化内存订单簿与序号 |
| `generate_orders` | 目标/当前持仓 → 买卖/调仓订单列表；缺行情跳过该标的，不误清仓 |
| `lots_from_positions` | 目标持仓 → `stock_code` + `volume`（股）；换算失败的标的省略 |
| `persist_orders` | 订单幂等落库为 `created`（`$setOnInsert`，不覆盖已有文档） |

### 2. `TradeSettlement`（`orders.py`）

| 方法 | 作用 |
|------|------|
| `__init__` | 初始化成交记录与本地持仓账本 |
| `sync_positions_from_broker` | 用券商持仓覆盖本地账本（对账前） |
| `reconcile_target_vs_broker` | 目标股数 vs 券商实际持仓对账，返回 matched / discrepancies |

### 3. `QMTClientFactory` / `QMTConnectionError`（`broker.py`）

| 方法 / 类型 | 作用 |
|------|------|
| `QMTConnectionError` | HTTP 桥不可用时抛出的异常 |
| `QMTClientFactory.create` | 创建并连接 QMT 桥客户端；连不上抛 `QMTConnectionError` |

### 4. `XtQuantExecutor`（`broker.py`）

| 方法 | 作用 |
|------|------|
| `__init__` | 绑定 `QMTUtil`（可注入）；`allow_mock` 控制是否允许纸面客户端 |
| `submit_order` | 提交买卖单并写/更新 `zstock_orders`；同 `order_id` 幂等不重发 |
| `query_order` | 按 `order_id` 查库中订单文档 |
| `query_all_orders` | 查全部订单（可按 status 过滤） |
| `cancel_order` | 发撤单请求，状态置 `pending_cancel`（终态由回报推进） |
| `on_order_status` | 券商订单状态回报 → 更新 DB（终态不可回退） |
| `on_trade` | 券商成交回报 → 记 fills、累加成交量并推进 partial/filled（按 `broker_trade_id` 幂等） |
| `get_account_info` | 现金 / 总资产 / 冻结资金 |
| `get_positions` | 券商持仓列表（6 位 code）；查询失败返回 `None` |
| `get_price_map` | 批量最新价 `{code: price}` |
| `is_connected` | QMT 连接是否活跃 |

### 5. `ExecutionStrategy`（`timing.py`）

| 方法 | 作用 |
|------|------|
| `__init__` | 配置 TWAP 批次数与批间隔 |
| `get_current_strategy` | 按 Asia/Shanghai 时段返回 `auction_sell` / `twap_buy` / `final` / `closed` |
| `execute_with_strategy` | 按策略提交订单；返回 `submitted` / `deferred` / `failed` 计数 |

时段要点：

| 时段 | 策略 |
|------|------|
| 09:15–09:25 | `auction_sell`（仅限价卖；买单 deferred） |
| 09:25–09:30 / 午休 / 盘外 | `closed` |
| 09:30–11:30、13:00–14:57 | `twap_buy`（连续竞价分批） |
| 14:57–15:00 | `final`（收盘集合竞价，仅限价） |

### 6. `OrderManagementPipeline`（`pipeline.py`）

| 方法 | 作用 |
|------|------|
| `__init__` | 组装 OrderGenerator / XtQuantExecutor / TradeSettlement / ExecutionStrategy |
| `execute_full_pipeline` | 完整管道：补行情/持仓/资金 → 生成订单 → 时段执行 → 落库 → 对账；返回摘要 Dict |

导出见包 `__init__`：`OrderGenerator`、`TradeSettlement`、`XtQuantExecutor`、`QMTClientFactory`、`QMTConnectionError`、`ExecutionStrategy`、`OrderManagementPipeline`。
