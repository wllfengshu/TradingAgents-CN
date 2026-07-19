# zstock 数据管理模块 (Data Management)

## 概述

这是 zstock 量化交易系统的数据管理层，提供统一的数据获取、缓存、降级与统计能力。

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

## 核心组件

### 1) 缓存管理器：`cache_service.py`

### 2) 数据源管理器：`database_service.py`

### 3) 查询服务：`query_service.py`

