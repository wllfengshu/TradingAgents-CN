# zstock 数据管理模块 (Data Management)

## 概述

这是 zstock 量化交易系统的数据管理层，提供统一的数据获取、降级与统计能力。

系统分层位置：

```text
第 0 层：治理与监控层
        ↓
第 1 层：数据层（data_management）  <- 当前模块
        ↓
第 2 层：研究层（factor_management）
        ↓
第 3 层：策略层（strategy_management）
        ↓
第 4 层：执行层（order_management）
```

## 每晚流水线

```text
stockdb → sync_ohlcv / sync_capital_flow → MongoDB
       → precompute_factors → zstock_factor_*
```

```bash
# 立即跑一轮：stockdb → Mongo → 因子预计算
python -m zstock.data_management.script.nightly_pipeline

# Python 定时调度（默认每天 21:00；Web API 启动时也会挂上）
python -m zstock.data_management.script.nightly_scheduler
python -m zstock.data_management.script.nightly_scheduler --time 21:00
```

数据同步脚本只保留 stockdb → MongoDB：`sync_ohlcv` / `sync_capital_flow`。

`sync_ohlcv` 支持 `--freq 1d|5m|1m`（可逗号组合）；落库 `period` 分别为 `D` / `5m` / `1m`。夜跑默认只同步日线。

## 核心组件

### 1) 查询服务：`query_service.py`

