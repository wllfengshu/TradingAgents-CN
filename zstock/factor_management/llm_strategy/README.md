# Factor Management 模块（研究层 / 第 2 层）


# qlib安装教程，注意，是安装当前目录下的qlib

cd E:\02Learn\09\TradingAgents-CN\qlib

# 确保工具链够新
python -m pip install --upgrade pip setuptools wheel
python -m pip install setuptools-scm

# 重新安装（不要用 pip install . 而是显式 python -m pip）
python -m pip install .

## 概述

`factor_management` 是量化交易系统的研究层，负责从市场数据出发，完成因子计算、因子预处理、模型训练与回测验证，并向策略层提供可直接消费的模型与指标。

系统分层位置：

```text
第 0 层：治理与监控层
        ↓
第 1 层：数据层（data_management）
        ↓
第 2 层：研究层（factor_management）  <- 当前模块
        ↓
第 3 层：策略层（strategy_management）
        ↓
第 4 层：执行层（order_management）
```

---

## 当前状态

- 完成度：`100%`
- 交付状态：`Production Ready`
- 核心模块：`FactorCalculator`、`FactorPreprocessor`、`ModelTrainer`、`BacktestEngine`
- 集成能力：已提供 `FactorManagementPipeline` 一键串联流程
- 测试现状（按文档记录）：单测与集成测试均已覆盖，整体覆盖率目标 `> 85%`
---

## 核心职责

| 模块 | 职责 | 输入 | 输出 |
|------|------|------|------|
| `FactorCalculator` | 计算因子（Alpha158/自定义） | 市场数据 | 因子矩阵 |
| `FactorPreprocessor` | 因子清洗与标准化 | 原始因子 | 可训练因子 |
| `ModelTrainer` | 模型训练与版本管理 | 因子 + 标签 | 模型文件 + 训练指标 |
| `BacktestEngine` | 回测与绩效评估 | 模型 + 因子 + 行情 | 收益曲线 + 风险指标 |
| `FactorManagementPipeline` | 端到端流程编排 | 配置 + 时间区间 | 全流程结果 |

---

## 项目结构

```text
zstock/factor_management/
├── __init__.py
├── factor_calculator.py
├── factor_preprocessor.py
├── model_trainer.py
├── backtest_engine.py
├── pipeline.py
├── test/test_factor_management.py
└── doc/README.md
```

---

## 快速开始

### 1) 安装依赖

```bash
pip install pandas numpy scikit-learn lightgbm scipy
```

可选（Alpha158 需要 Qlib）：

```bash
pip install qlib
```

### 2) 最小可运行示例

```python
from zstock.factor_management import (
    FactorCalculator,
    FactorPreprocessor,
    ModelTrainer,
    BacktestEngine,
)
import numpy as np
import pandas as pd

# 1. 准备市场数据
market_data = pd.DataFrame({
    'stock_code': ['SH600000', 'SH600001'] * 50,
    'date': pd.date_range('2023-01-01', periods=100),
    'close': np.random.randn(100).cumsum() + 100,
    'volume': np.random.randint(1_000_000, 10_000_000, 100),
})

# 2. 因子计算
calculator = FactorCalculator()
factors = calculator.calculate_custom_factors(
    market_data=market_data,
    config={
        'momentum_20d': {'period': 20},
        'volatility_20d': {'period': 20},
    },
)

# 3. 因子预处理
preprocessor = FactorPreprocessor()
processed_factors, report = preprocessor.process_pipeline(
    factors,
    config={
        'handle_missing': {'method': 'forward_fill'},
        'remove_outliers': {'method': 'mad', 'threshold': 3.0},
        'standardize': {'method': 'zscore'},
    },
)

# 4. 模型训练
trainer = ModelTrainer()
X_train = processed_factors.values
y_train = np.random.randn(len(X_train)) * 0.1
model = trainer.train_lightgbm(X_train, y_train)

# 5. 回测指标
engine = BacktestEngine()
metrics = engine.calculate_performance_metrics([1.0, 1.01, 1.02, 1.03, 1.04, 1.05])
print('Sharpe:', metrics['sharpe_ratio'])
print('Quality:', report['quality_report']['overall_quality'])
```

### 3) 一键执行完整流程

```python
from zstock.factor_management.llm_strategy.pipeline import FactorManagementPipeline

pipeline = FactorManagementPipeline()
results = pipeline.execute_full_pipeline(
    start_date='2023-01-01',
    end_date='2024-01-01',
    config={
        'factor_calculation': {'use_alpha158': True, 'use_custom': True},
        'model_training': {'model_type': 'lightgbm'},
    },
)

print('Pipeline finished, keys:', list(results.keys()))
```

---

## 关键能力

### 1) 因子计算（`FactorCalculator`）

- 支持 Alpha158（Qlib）
- 支持自定义因子（如 `momentum_Nd`、`volatility_Nd`）
- 支持因子落库（MongoDB）与缓存（Redis）

常用入口：

- `initialize_qlib(...)`
- `calculate_alpha158_factors(...)`
- `calculate_custom_factors(...)`
- `save_factors_to_mongodb(...)`
- `cache_factors_to_redis(...)`

### 2) 因子预处理（`FactorPreprocessor`）

- 缺失值处理：`forward_fill` / `backward_fill` / `drop` / `mean` / `median`
- 极值处理：`3sigma` / `mad` / `percentile`
- 中性化：行业与市值中性化
- 标准化：`zscore` / `minmax` / `rank`
- 质量检验：输出因子质量报告

推荐默认：

```python
config = {
    'handle_missing': {'method': 'forward_fill', 'threshold': 0.8},
    'remove_outliers': {'method': 'mad', 'threshold': 3.0},
    'neutralize': {'enable': False},
    'standardize': {'method': 'zscore', 'group_by_date': True},
}
```

### 3) 模型训练（`ModelTrainer`）

- 模型类型：LightGBM / Linear（OLS、Ridge、Lasso）/ MLP
- 支持滚动训练（更贴近实盘）
- 支持模型存储、加载与版本管理

推荐优先级：

1. `LightGBM`（性能与速度平衡）
2. `Linear`（解释性强）
3. `MLP`（复杂非线性场景）

### 4) 回测验证（`BacktestEngine`）

- 可配置回测宇宙过滤（ST、停牌、流动性）
- 输出核心绩效指标（年化、波动、Sharpe、最大回撤、胜率）
- 支持参数敏感性分析

核心指标目标建议：

- Sharpe > `1.0`：良好
- Sharpe > `1.5`：优秀
- 最大回撤 < `15%`：更稳健

---

## 测试

运行方式：

```bash
cd zstock/factor_management
python -m pytest test/test_llm_pipeline.py -v
```

或：

```bash
python test_llm_pipeline.py
```

文档口径（历史记录）：

- 单元测试与集成测试均已覆盖
- 覆盖范围包括：因子计算、预处理、训练、回测、集成流程

---

## 与上下游集成

### 与数据层（`zstock/data_management`）

```python
from zstock.data_management import get_market_data

market_data = get_market_data(
    stock_codes=['SH600000', 'SH600001'],
    start_date='2023-01-01',
    end_date='2024-01-01',
)
```

### 向策略层（`zstock/strategy_management`）输出

- 训练后的模型文件
- 标准化因子数据
- 回测绩效指标
- 参数与版本元信息

---

## 参数调优速查

### 预处理参数

- 更保守（保留更多数据）：`threshold=0.9`、`mad=3.5`
- 更激进（更强去噪）：`threshold=0.7`、`mad=2.5`

### LightGBM 参数

- 防过拟合：较小 `num_leaves`、较低 `learning_rate`
- 提升表达：较大 `num_leaves`、更多 `n_estimators`

### 回测参数

- 激进：高频调仓 + 更分散持仓
- 保守：低频调仓 + 更集中持仓

---

## 典型工作流

```text
市场数据（数据层）
    ↓
因子计算（Alpha158 + 自定义）
    ↓
因子预处理（缺失/极值/中性化/标准化）
    ↓
模型训练（LightGBM/Linear/MLP）
    ↓
回测验证（收益与风险指标）
    ↓
最优模型输出给策略层
```

---

## 常见问题（FAQ）

### Q1: 缺失值如何处理？
推荐先用 `forward_fill`，再根据缺失率阈值删列。

### Q2: 标准化用哪种方法？
默认建议 `zscore + group_by_date=True`。

### Q3: 首选模型是什么？
一般先试 `LightGBM`，再按可解释性或数据规模切换。

### Q4: Sharpe 偏低怎么排查？
优先看因子有效性（IC）、过拟合程度、交易参数和交易成本设置。

---

## 路线图建议

1. 扩展自定义因子库（技术面/基本面/微观结构）
2. 做滚动重训与线上监控（IC、Sharpe、漂移）
3. 与策略层打通信号生成和组合优化
4. 持续做参数敏感性分析与稳健性回测

---

## 支持文档

- `zstock/项目说明文档.md`
- `zstock/项目开发计划.md`
- `zstock/项目说明文档.md`（系统分层架构章节）

---

更新日期：`2026-06-20`
维护者：`Liang`
状态：`Production Ready`
