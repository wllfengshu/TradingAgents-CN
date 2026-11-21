# strategy_management（策略层 / 第 3 层）

## 概述

`strategy_management` 是量化交易系统的**策略层**：消费因子层 raw，产出买卖信号与目标持仓，再交给执行层下单。

系统分层：

```text
第 0 层：治理与监控层
        ↓
第 1 层：数据层（data_management）
        ↓
第 2 层：研究层（factor_management）—— 因子公式 / 预计算
        ↓
第 3 层：策略层（strategy_management）  ← 当前模块
        ↓
第 4 层：执行层（order_management）
```

配置单一真实源（SSOT）：`zstock/common/config/strategy_params.json`，经 `strategy_config.resolve_runtime_config` 派生运行态；信号与执行共用同一份参数，禁止旁路硬编码。

---

## 模块与类职责

### 编排入口

| 类 / 模块 | 作用 |
|---|---|
| **StrategyPipeline** | 策略总编排：取数 → 情绪+选股漏斗 → 红黄绿账户 → 组合/风控/换手。持有 `config`（原始 JSON）与 `runtime`（执行态）。 |
| **SignalFunnelMixin** | 挂在 Pipeline 上的信号漏斗：M1→M5 共用编排（现场算因子 / 读预计算文档走同一套打分与门禁）。 |

### 领域：情绪 · 选股 · 账户

| 类 / 模块 | 作用 |
|---|---|
| **sentiment** | 市场情绪：由市场因子算红/黄/绿、仓位缩放；`detect_style` 判动量/反转/中性。 |
| **selection** | 截面选股：板块打分(M2)、龙头打分(M3)、合力门禁(M4)、最终加权合成(M5)。 |
| **MarketBook** | 跨日账户状态机：红→防守、黄→中性、动量绿灯确认后→进攻；中性或反转绿灯留在行业 ETF。 |
| **BookDecision** | 单日账户决策：`state` / `weights` / `grade`。 |
| **BookMarketInputs** | 账户判定用的指数与资产收盘序列（截至上一交易日）。 |

### 组合执行

| 类 / 模块 | 作用 |
|---|---|
| **execution** | 进攻日股票执行管线：组合优化 → 风控 → 趋势/排名退出衔接 → 换手 → 仓位缩放；防守/中性日直接落账户权重。 |
| **PortfolioOptimizer** | 按 `final_score` 在持仓名额内分配权重（等权 / 分数 / softmax），单股上限后再归一化。 |
| **RiskManager** | 持仓合规检查与纠偏：权重和、单股上限、集中度、持仓数、行业暴露。 |
| **TurnoverController** | Buffer 换手 + 最短持有 + 成本估算，抑制无意义调仓。 |
| **exit_policy** | 退出规则：排名分位强制出、趋势/布林弱势退出（供回测日与执行层调用）。 |

### 信号契约与宇宙

| 类 / 模块 | 作用 |
|---|---|
| **signal_schema** | 信号 DataFrame 列约定、空表、候选去重与结果转表。 |
| **universe_builder** | 按日构建测评/隔夜宇宙：`full` / `prefilter` / `funnel`（漏斗按日算 M3，与实盘一致）。 |

### 脚本（回测 / 网格 / 隔夜）

| 类 / 模块 | 作用 |
|---|---|
| **Backtester** | 日频再平衡回测：拉 OHLCV、调 Pipeline、记账净值与换手。 |
| **BacktestResult** | 回测结果：净值曲线、回撤、成交、持仓日志、指标与导出。 |
| **RealGridSearchOptimizer** | 预计算路径上的参数网格搜索，整包 `strategy_params` 派生，无旁路 runtime。 |
| **overnight_backtest** | 隔夜批跑：预检、备份参数、多区间回测/敏感度/网格、报告。 |
| **run_style_detector** | 单独跑风格检测，便于诊断。 |

---

## 完整策略流程

### 1. 总览（单日）

```mermaid
flowchart TB
    A[交易日开始] --> B[加载配置<br/>strategy_params → runtime]
    B --> C{信号来源}
    C -->|现场| D[拉主板宇宙 / OHLCV / 资金流]
    C -->|预计算| E[读 Mongo 因子截面]
    D --> F[信号漏斗 M1→M5]
    E --> F
    F --> G[信号表 signals_df<br/>含灯色 / 风格 / TopK]
    G --> H{账户层启用?<br/>book_regime.enabled}
    H -->|否| I[股票执行管线]
    H -->|是| J[MarketBook.step<br/>红黄绿 → 防守/中性/进攻]
    J --> K{账户状态}
    K -->|防守 defense| L[债 / 黄金等防守权重]
    K -->|中性 neutral| M[行业 ETF 权重<br/>不开股票新仓]
    K -->|进攻 attack| I
    I --> N[组合优化 → 风控 → 退出 → 换手 → 仓位缩放<br/>开仓和账户状态用同一盏 M1 灯]
    L --> O[目标持仓]
    M --> O
    N --> O
    O --> P[回测记账 / 或交给执行层下单]
```

> **信号灯**：M1 只算一次。账户状态（防守/中性/进攻）和仓位缩放都读这盏灯。风格（动量/反转/中性）不是灯。


### 2. 信号漏斗（M1 → M5）

现场与预计算共用同一套打分；仅 raw 来源不同。

```mermaid
flowchart TB
    S0[入口：trade_date + 宇宙] --> M1

    subgraph M1[M1 市场情绪]
        M1A[市场因子 raw] --> M1B[合成综合分]
        M1B --> M1C{灯色}
        M1C -->|红且 stops_on_red| M1X[提前退出<br/>空信号 / 仓位 0]
        M1C -->|黄 / 绿| M1D[仓位缩放因子]
    end

    M1D --> ST[风格检测<br/>动量 / 反转 / 中性]
    ST --> U{宇宙模式}
    U -->|full| U1[全量候选]
    U -->|prefilter / funnel| U2[主板·非ST·黑名单等过滤]

    U1 --> M2
    U2 --> M2

    subgraph M2[M2 板块层]
        M2A[板块因子 raw] --> M2B[按风格画像打分]
        M2B --> M2C[取 Top 板块]
    end

    M2C --> M3

    subgraph M3[M3 龙头层]
        M3A[各板块内个股因子] --> M3B[龙头打分]
        M3B --> M3C[每板块 Top N 候选]
        M3C --> M3D{有候选?}
        M3D -->|否| M3X[提前退出 no_candidates]
        M3D -->|是| M3E[候选去重]
    end

    M3E --> M4

    subgraph M4[M4 合力门禁]
        M4A[合力 / 资金流文档] --> M4B{过流入流出门槛?}
        M4B -->|缺文档| M4W[空文档 + 告警<br/>不静默丢票]
        M4B -->|未过| M4D[剔除]
        M4B -->|通过| M4E[保留]
        M4W --> M4E
    end

    M4E --> M5

    subgraph M5[M5 最终合成]
        M5A[板块分 + 龙头分 + 合力分<br/>按风格权重加权] --> M5B[排序 ranked_all]
        M5B --> M5C[按 regime 取 top_k<br/>写入买入信号]
    end

    M5C --> OUT[信号表 DataFrame]
    M1X --> OUT0[空表 + 红灯属性]
    M3X --> OUT0
```

### 3. 红黄绿账户（MarketBook）

判定用**截至上一交易日**的指数与资产收盘；目标权重加总为 1。行业仓波动高于 `vol_target` 时缩到目标，缩掉的部分买国债。进攻日股票以外的权重按同一套行业配置填充。

```mermaid
flowchart TB
    IN[当日灯色 grade<br/>+ 指数序列 + 净值] --> G{grade}
    G -->|红| DEF
    G -->|黄| CHK
    G -->|绿| GS[绿灯连日 +1]

    GS --> CONF{连日 ≥ 确认天数?}
    CONF -->|否| CHK
    CONF -->|是| ATK

    REG[均线防守 / 回撤锁] --> FORCED{强制防守?}
    FORCED -->|是| DEF
    FORCED -->|否| CHK2

    CHK --> NEU
    CHK2 --> NEU

    subgraph DEF[防守 defense]
        D1[政金债 40%<br/>银行达标 30% 否则国债<br/>黄金达标 30% 否则国债]
    end

    subgraph NEU[中性 neutral]
        N1[股票类行业 ETF<br/>或债；不开股票新仓]
    end

    subgraph ATK[进攻 attack]
        A1[仅动量绿灯<br/>交给截面股票信号]
        A2[股票以外按行业 ETF 配置]
    end

    DEF --> BD[BookDecision]
    NEU --> BD
    ATK --> BD
```

### 4. 进攻日股票执行（execution）

```mermaid
flowchart LR
    SIG[买入信号 buy] --> RO{红灯禁开新仓?}
    RO -->|是| EMP[清空新开<br/>保留观察池供退出]
    RO -->|否| OPT

    EMP --> OPT2[空目标或仅退出]
    OPT[PortfolioOptimizer<br/>名额 + 按分加权] --> RISK
    OPT2 --> RISK

    RISK[RiskManager<br/>合规纠偏] --> EXIT
    EXIT[趋势退出 / 与排名退出衔接] --> TOV
    TOV[TurnoverController<br/>Buffer + 最短持有] --> SCALE
    SCALE[按灯色 / 风格缩放仓位<br/>余量按行业 ETF] --> HOLD[最终持仓]
```

### 5. 回测日循环（Backtester）

```mermaid
flowchart TB
    START[区间内每个交易日] --> NAV[用当日价更新净值]
    NAV --> NEED{到达再平衡日?<br/>或自适应频率触发}
    NEED -->|否| HOLD[继续持有]
    NEED -->|是| BOOK

    BOOK{账户启用?} -->|是| STEP[Book.step<br/>可传入已算好的 book_decision]
    BOOK -->|否| PIPE
    STEP --> STATE{状态}
    STATE -->|防守/中性| BW[直接用账户权重]
    STATE -->|进攻| PIPE

    PIPE[execute_full_pipeline<br/>预计算或现场信号] --> EX[执行管线出目标仓]
    EX --> RANK[排名强制退出<br/>不在信号表的持仓强制出]
    BW --> RANK
    RANK --> COST[换手成本 / 成交记录]
    COST --> NEXT[下一交易日]
    HOLD --> NEXT
```

### 6. 配置流（SSOT，无双轨）

```mermaid
flowchart LR
    JSON[strategy_params.json] --> LOAD[load_strategy_params]
    LOAD --> PIPE_CFG[StrategyPipeline.config]
    PIPE_CFG --> RT[build_runtime_config<br/>→ pipeline.runtime]
    JSON --> RES[resolve_runtime_config]
    RES --> BT[Backtester / 隔夜 / 网格]
    BT -->|config=| EXEC[execute_full_pipeline<br/>与 self.config 同源]
    RT --> EXEC
```

---

## 关键文件索引

| 路径 | 说明 |
|---|---|
| `pipeline.py` | StrategyPipeline |
| `signal_funnel.py` | SignalFunnelMixin（M1–M5） |
| `sentiment.py` | 情绪与风格 |
| `selection.py` | 板块 / 龙头 / 合力 / 合成 |
| `market_book.py` | MarketBook / BookDecision |
| `execution.py` | 进攻日执行阶段 |
| `exit_policy.py` | 排名与趋势退出 |
| `portfolio_optimizer.py` | 组合权重 |
| `risk_manager.py` | 风控 |
| `turnover_controller.py` | 换手 |
| `signal_schema.py` | 信号表契约 |
| `universe_builder.py` | 按日宇宙 |
| `script/backtester.py` | 回测器 |
| `script/grid_search_real.py` | 网格搜索 |
| `script/overnight_backtest.py` | 隔夜批跑 |

因子公式与预计算落库在 `factor_management`；本包**只消费 raw**，不重复定义因子。
