# Strategy Management 模块（策略层 / 第 3 层）

## 概述

`strategy_management` 是量化交易系统的策略层，负责将研究层的因子与模型结果转化为可执行的目标持仓。

```text
第 0 层：治理与监控层
        ↓
第 1 层：数据层 (data_management)
        ↓
第 2 层：研究层 (factor_management)
        ↓
第 3 层：策略层 (strategy_management)  <- 当前模块
        ↓
第 4 层：执行层 (order_management)
```

## 核心职责

| 模块 | 职责 | 输入 | 输出 |
|------|------|------|------|
| `SignalGenerator` | 生成交易信号 | 模型 + 因子 | 信号排名/得分 |
| `PortfolioOptimizer` | 优化投资组合 | 信号 | 最优权重 |
| `RiskManager` | 风险与合规检查 | 持仓/权重 | 风险报告 |
| `TurnoverController` | 控制换手与成本 | 当前/目标持仓 | 最终持仓 |
| `StrategyPipeline` | 串联完整流程 | 因子与配置 | 策略执行结果 |

## 项目结构

```text
zstock/strategy_management/
├── __init__.py
├── signal_generator.py
├── portfolio_optimizer.py
├── risk_manager.py
├── turnover_controller.py
├── pipeline.py
├── test_strategy.py
└── doc/
    └── README.md
```

## 快速开始

### 一体化调用（推荐）

```python
from zstock.strategy_management.pipeline import StrategyPipeline

pipeline = StrategyPipeline()

results = pipeline.execute_full_pipeline(
    factors_df=factors_df,
    model_name="lgb_v1",
    trade_status_df=trade_status_df,
    industry_data=industry_data,
    market_cap_data=market_cap_data,
    market_data=market_data,
    total_capital=10_000_000,
    config={
        "signal_generation": {"top_n": 20},
        "portfolio_optimization": {"min_holdings": 15, "max_holdings": 25},
        "turnover_control": {"buffer_threshold": 0.15},
    },
)

```

### 分步调用

```python
from zstock.strategy_management import (
    SignalGenerator,
    PortfolioOptimizer,
    RiskManager,
    TurnoverController,
)

generator = SignalGenerator()
generator.load_model("lgb_v1")
signals = generator.generate_signals(factors_df, top_n=20)

optimizer = PortfolioOptimizer(optimizer_type="cvxpy")  # 或 "simple"
opt_result = optimizer.optimize_portfolio(signals_df=signals)

manager = RiskManager()
risk_report = manager.check_compliance(
    weights=opt_result["weights"],
    holdings_df=opt_result["holdings_df"],
)

controller = TurnoverController()
final_holdings = controller.apply_buffer_mechanism(
    new_holdings=opt_result["holdings_df"],
    current_holdings=current_positions,
)
```

## 模块说明

### 1) SignalGenerator

- 加载预训练模型并预测得分
- 过滤不可交易标的（ST、停牌、涨跌停等）
- 支持信号排序、阈值过滤、结果缓存/落库

示例输出：

```python
{
    "signal_date": "2024-01-01",
    "stock_code": "SH600000",
    "score": 0.85,
    "rank": 1,
    "signal_type": "buy",
    "created_at": "2024-01-01T08:00:00Z",
}
```

### 2) PortfolioOptimizer

支持两种优化方式：

1. `cvxpy`（推荐）：凸优化，支持复杂约束
2. `simple`（备选）：启发式分配，无额外依赖

常见约束：

```python
{
    "min_holdings": 15,
    "max_holdings": 25,
    "max_weight_per_stock": 0.08,
    "max_industry_exposure": 0.25,
    "max_small_cap_weight": 0.30,
}
```

### 3) RiskManager

- 暴露度检查：行业/个股/小市值
- 集中度检查：Top5、Herfindahl 指数
- 流动性检查：预估成交额与日均成交量

结果结构：

```python
{
    "status": "passed",  # passed / warning / rejected
    "issues": [{"type": "industry_exposure", "severity": "high", "detail": "..."}],
    "metrics": {...},
}
```

### 4) TurnoverController

采用 Buffer 机制减少无效调仓：

`新股票得分 > 当前持仓最低分 * (1 + buffer_threshold)` 才允许换入。

交易成本估算包含：
- 佣金
- 印花税
- 冲击成本

### 5) StrategyPipeline

将四个模块按流程串联：

`因子数据 -> 信号生成 -> 组合优化 -> 风控检查 -> 换手控制 -> 目标持仓`

并提供错误处理、降级逻辑和结果保存能力。

## 测试

运行：

```bash
cd zstock/strategy_management
python test_strategy.py
```

当前测试统计：

- `SignalGenerator`: 2
- `PortfolioOptimizer`: 2
- `RiskManager`: 2
- `TurnoverController`: 2
- `StrategyPipeline`: 1
- 合计：9 个单元测试

## 与上下游集成

输入（数据层 + 研究层）：

```text
data_management -> 因子、交易状态
factor_management -> 模型、因子结果
```

输出（执行层）：

```text
strategy_management -> 目标持仓、风险报告
order_management <- 基于目标持仓生成订单
```

## 交付与完成情况（原 COMPLETION_SUMMARY 合并）

### 完成度

- Phase 4（策略层）开发完成
- 模块、测试、文档均已交付
- 状态：可用于生产环境

### 关键特性

- 信号生成：模型推断 + 可交易过滤 + 排序输出
- 组合优化：`cvxpy` + `simple` 双路径
- 风险管理：暴露度/集中度/流动性全覆盖
- 换手控制：Buffer 机制 + 成本估算
- 流程管道：一体化执行 + 异常降级

### 质量检查

- 模块可独立导入
- 主要函数具备类型提示
- 异常处理与日志链路完整
- 单元测试全通过（9/9）
- 文档统一收敛到本文件

### 下一步（Phase 5）

- 订单生成模块
- XtQuant 执行接口
- 成交回报处理

## 常见问题

**Q: 必须安装 `cvxpy` 吗？**  
A: 不是必须。未安装时可回退到 `simple` 优化方式。

**Q: Buffer 阈值建议多少？**  
A: 常用区间 10%~20%，可按市场波动做参数化调整。

**Q: 风险检查失败怎么办？**  
A: 可调整组合约束或风险限额后重新优化。

## 状态信息

- 版本：`1.0`
- 最后更新：`2026-06-20`
- 当前状态：`Production Ready`
