# Factor Management 模块（研究层 / 第 2 层）

## 概述

`factor_management` 是量化交易系统的研究层，负责因子公式、因子目录与预计算入库。  
各层（M1~M4）按定义**独立**计算 raw；「板块过滤 → 龙头 → 合力」漏斗只存在于策略层。

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

```text
market_factors.py   M1 市场层
sector_factors.py   M2 板块层
dragon_factors.py   M3 龙头层
force_factors.py    M4 合力层
script/precompute_factors.py   全市场预计算入库
script/factor_evaluation.py    因子有效性测评 / 报告
```

宇宙准入过滤见 `zstock.common.prefilters.PreFilters`；选股漏斗见 `strategy_management`。

---

## 流程图

### 总览：因子层在系统中的位置

```mermaid
flowchart LR
  DM[数据层 Mongo<br/>OHLCV / 资金流 / 板块 / 基本面] --> PC[precompute_factors<br/>全市场 raw 入库]
  PC --> FC[(zstock_factor_*)]
  FC --> EV[factor_evaluation<br/>IC / 评级 / 报告]
  FC --> ST[策略层<br/>选股漏斗打分]
  CAT[web.backend.factor_catalog<br/>Web 勾选目录] --> ST
  M1[MarketFactors] --> PC
  M2[SectorFactors] --> PC
  M3[DragonFactors] --> PC
  M4[ForceFactors] --> PC
```

### 细节 1：预计算管道 `FactorPrecomputeService`

```mermaid
flowchart TD
  A[CLI main / nightly_pipeline] --> B{单日 or 区间?}
  B -->|单日| C[precompute_single_date]
  B -->|区间| D[precompute_date_range]

  C --> E[_preload_range_data<br/>拉 OHLCV/资金流/板块/LHB]
  D --> E2[区间一次预加载] --> F[按交易日切片 slice_preloaded_data]
  E --> G[_fundamental_for_date<br/>BPS / 股东户数 asof]
  F --> G2[每日 fund_provider]
  G --> H[_compute_day_sync]
  G2 --> H

  H --> I[compute_day_factor_raws<br/>M1/M2/M3/M4 各自独立]
  I --> J[_store_all]
  J --> J1[_store_market → zstock_factor_market]
  J --> J2[_store_sector → zstock_factor_sector]
  J --> J3[_store_dragon → zstock_factor_dragon]
  J --> J4[_store_force → zstock_factor_force]

  D --> K{进程池日批失败?}
  K -->|是| K1[线程池串行补算]
  K1 -->|仍失败| K2[抛 RuntimeError]
  K -->|否| L[返回各层写入条数]
  J --> L
```

### 细节 2：单日截面 `compute_day_factor_raws`（四层独立，无漏斗）

层间**不做**「M2 过滤 → M3 → M4」；那是策略层 `signal_funnel` 的事。这里同一 `compute_universe` 只作输入范围，各层按定义全算。

```mermaid
flowchart TD
  IN[输入: trade_date<br/>stock_ohlcv / flow / sectors<br/>index_ohlcv / fund_provider] --> U[compute_universe<br/>有行情个股]
  IN --> S{assume_sorted?}
  S -->|否| S1[ensure_ohlcv_sorted]
  S -->|是| SPLIT
  S1 --> SPLIT

  SPLIT --> M1
  SPLIT --> M2
  SPLIT --> M3
  SPLIT --> M4

  M1[M1 compute_market_raw<br/>指数 mf*]
  U --> M2
  U --> M3
  U --> M4

  M2[M2 全板块 f21~f30<br/>不读 M1]
  M3[M3 全成分+未归属<br/>不读 M2 得分]
  M4[M4 宇宙内每只股票<br/>不读 M3 输出]

  M1 --> OUT[打包 market/sector/dragon/force raw]
  M2 --> OUT
  M3 --> OUT
  M4 --> OUT
```

### 细节 3：M3 龙头批处理内部

```mermaid
flowchart LR
  A[sector_jobs<br/>sector→codes] --> B[全 codes 去重]
  B --> C[precompute_stock_features<br/>收益/成交额/连板/布林/换手…]
  C --> D[按板块 assemble]
  D --> E[fund_provider → f39/f40]
  E --> F["{sector: {code: raw_dict}}"]
```

### 细节 4：M4 合力单候选

```mermaid
flowchart TD
  A[candidate code + sector] --> B[_extract_candidate_metrics]
  B --> B1{注入 main_flow?}
  B1 -->|是| B2[用注入值]
  B1 -->|否| B3[资金流 day_docs<br/>缺则 OHLCV.amount 兜底]
  B2 --> C[算 fcoop1~8 / 持续性 / 背离等]
  B3 --> C
  C --> D[lhb_bonus]
  D --> E[输出该股 raw 字典]
```

### 细节 5：因子测评 `FactorEvaluation`

```mermaid
flowchart TD
  A[CLI: eval / compare / report] --> B{command?}

  B -->|eval| C[构造 FactorEvaluation]
  C --> D[run]
  D --> D1[_get_jobs 层×字段]
  D1 --> D2[_prewarm 价格/板块面板]
  D2 --> D3[并发 _eval_one]
  D3 --> D4{layer?}
  D4 -->|sector| D5[load_eval_bundle_sector]
  D4 -->|dragon/force| D6[load_eval_bundle_stock<br/>可选 universe 掩码 + 极性取反]
  D5 --> D7[_evaluate: IC / 分层 / 打分 / Grade]
  D6 --> D7
  D7 --> D8[写 summary.csv<br/>可选 plot / decay]

  B -->|compare| E[run_compare 多 period]
  E --> E1[write_compare_report]

  B -->|report| F[load_report_data 三年 summary]
  F --> F1[build_merged + append_compare]
  F1 --> F2[generate_report Markdown]
```

### 细节 6：测评单因子 `_eval_one` 数据流

```mermaid
flowchart LR
  A[Mongo 因子文档] --> B[宽表 factor<br/>日期 × 代码]
  C[Mongo OHLCV] --> D[price open/close 面板]
  B --> E[对齐日期]
  D --> E
  E --> F[IC 序列]
  F --> G[分位数收益 / 多空]
  G --> H[comprehensive_score<br/>→ Total_Score / Grade]
```

模块协作关系：

```mermaid
flowchart TB
  subgraph 公式层
    M1[market_factors]
    M2[sector_factors]
    M3[dragon_factors]
    M4[force_factors]
  end
  subgraph 脚本
    PC[precompute_factors]
    EV[factor_evaluation]
  end
  subgraph Web
    CAT[web.backend.factor_catalog]
  end
  M1 --> PC
  M2 --> PC
  M3 --> PC
  M4 --> PC
  PC --> DB[(Mongo 因子集合)]
  DB --> EV
  M1 -.->|factor 装饰器元数据| CAT
  M2 -.->|factor 装饰器元数据| CAT
  M3 -.->|factor 装饰器元数据| CAT
  M4 -.->|factor 装饰器元数据| CAT
```

---

## 公有方法一览

以下仅列**公有方法**（不含 `_` 前缀内部实现）。


### 1. `MarketFactors`（M1）

| 方法 | 作用 |
|------|------|
| `min_bars` | 指数最少 K 线根数要求 |
| `compute_market_raw` | 指数 OHLCV → `trend_gap`；数据不足时返回 `reason` |
| `trend_gap` | 收盘 / MA120 − 1 |
| `rank_autocorr` | 滚动收益排名的 1 阶 Spearman 自相关（风格特征） |

### 2. `SectorFactors`（M2）

| 方法 | 作用 |
|------|------|
| `aggregate_sector_ohlcv` | 成分股行情/资金流聚合为板块日线 OHLCV 与资金流 |
| `calculate_all_sector_factors_raw` | 批量计算 M2 全部子因子原始值 `{field: {sector: value}}` |
| `raw_from_docs` | 预计算板块文档 → `{field: {sector_code: value}}` |
| `f21_rps` | 板块窗口收益的截面平均秩百分位 0~100 |
| `f22_main_flow` | 板块主力净流入合计 |
| `f23_limit_up_density` | 涨停家数 / 成分数 |
| `f24_max_consecutive` | 板块内最大连板高度 |
| `f25_volume_slope` | 成交额占比 MA 近窗线性斜率（多窗口） |
| `f26_volume_growth` | 短均量 / 长均量 − 1 |
| `f27_new_high_ratio` | 创 N 日新高家数 / 成分数 |
| `f28_consistency` | 近 N 日排名 Top10% 天数（全市场板块排名） |
| `f29_sector_breadth` | 当日上涨家数 / 成分数 |
| `f30_sector_concentration` | Top3 成交额 / 板块总成交额 |

### 3. `DragonFactors`（M3）

| 方法 | 作用 |
|------|------|
| `calculate_all_dragon_factors_in_sector_raw` | 单板块龙头因子 raw `{code: raw_dict}` |
| `calculate_all_dragon_factors_raw` | 多板块：个股特征算一次再按板块组装 |
| `raw_from_doc` | 预计算龙头文档 → 单股 raw 字段字典 |
| `precompute_stock_features` | 个股级 M3 特征（与板块无关，供多板块复用） |
| `assemble_sector_raw_from_features` | 用预计算特征组装某板块成分股 raw |
| `apply_fundamental_factors` | 写入 f39_pb / f40_holder_change（依赖 fund_provider） |
| `sorted_ohlcv_map` | OHLCV 按日升序整理（可选 asof 截断） |
| `nd_returns` | 从 OHLCV 算 N 日收益率 |
| `daily_volumes` | 当日成交额（优先 amount） |
| `turnover_rates` | 当日换手率 |
| `f31_excess_return` | 个股收益 − 板块中位数 |
| `f31b_rps_percentile` | 个股收益在板块内百分位 0~100 |
| `f32_amount` | 当日成交额 |
| `f33_consecutive_boards` | 最近连续涨停天数 |
| `f34_resonance_pct` | 多窗口量价共振健康日占比 |
| `f35_bollinger_trend` | 布林趋势分 + pass 标志（一次算完） |
| `f36_identity_premium` | 个股换手 / 板块均值（无换手则用成交额比） |
| `f37_relative_strength` | 板块内收益 z-score |
| `f38_turnover_anomaly` | 当日换手 / 20 日均值 |
| `f39_pb` | 收盘价 / BPS |
| `f40_holder_change` | 股东户数相对变化 |

### 4. `ForceFactors`（M4）

| 方法 | 作用 |
|------|------|
| `apply_cooperative_force_raw` | 候选列表 → 合力子因子 raw（含多窗口与龙虎榜加分） |
| `fcoop1_main_net_ratio` | 主力净流入 / 成交额，流出为负 |
| `fcoop2_main_retail_ratio` | 主力 / (主力+\|散户\|) |
| `fcoop3_sustained_days` | 近 window 日主力净流入 > 0 的天数 |
| `fcoop4_turnover_quality` | 换手率质量分 |
| `fcoop5_main_flow_acceleration` | 主力净流入加速度 |
| `fcoop6_main_force_aggression` | 主力进攻性 |
| `fcoop7_super_large_net_ratio` | 超大单净流入占比 |
| `fcoop8_main_flow_trend_5d` | 近 5 日主力净流入趋势 |
| `limit_down_free_days` | 近 5 日未跌停天数 |
| `f_power_divergence` | 量价背离度 |
| `f_main_force_persistence` | 主力持续性 |
| `f_mean_reversion_signal` | 均值回归信号 |
| `sigmoid` | Sigmoid 映射工具 |
| `lhb_bonus` | 龙虎榜加分（机构/游资等） |

### 5. `script.precompute_factors`

#### 当日 raw 打包（脚本内，非策略编排）

| 方法 | 作用 |
|------|------|
| `compute_day_factor_raws` | 同一日对 M1~M4 **各自独立**算 raw 并打包；层间无过滤 |

#### `FactorPrecomputeService`

| 方法 | 作用 |
|------|------|
| `__init__` | 绑定 QueryService，按资源预算设 compute/load/query 并发 |
| `precompute_single_date` | 单日：预加载 → `compute_day_factor_raws` → 写入 Mongo |
| `precompute_date_range` | 区间批量预计算（进程池/线程池 + 失败日补算） |
| `slice_preloaded_data` | 从预加载大表切出单日 lookback 窗口输入 |

#### 模块入口

| 方法 | 作用 |
|------|------|
| `main` | CLI：按日期区间跑 `FactorPrecomputeService` |

### 7. `script.factor_evaluation.FactorEvaluation`

| 方法 | 作用 |
|------|------|
| `__init__` | 配置测评窗口、分层数、宇宙、极性取反、并发与输出目录 |
| `load_stock_factor_panel` | 从 Mongo 拉个股因子宽表（日期×代码） |
| `load_sector_factor_panel` | 从 Mongo 拉板块因子宽表 |
| `load_price_panel` | 加载个股 open/close 价格面板（带缓存） |
| `load_sector_price_panel` | 由成分股收益合成板块指数价格面板 |
| `apply_universe_mask` | 按日宇宙掩码，宇宙外置 NaN |
| `apply_polarity_invert` | 对注册表负 IC 字段取反 |
| `load_eval_bundle_stock` | 个股层：因子面板 + 价格面板打包 |
| `load_eval_bundle_sector` | 板块层：因子面板 + 板块价格打包 |
| `run` | 执行测评任务列表，写 `summary.csv`；可选进度回调 |
| `grade_letter` | Grade 字符串规范为 A/B/C/D/N/A |
| `year_eval_window` | 自然年测评起止日 |
| `run_batch_years` | 多年批量测评 |
| `run_compare` | 多口径（period）对照测评 |
| `write_compare_report` | 对照结果写 Markdown 报告 |
| `fmt_cell` / `fmt_ric` | 报告单元格 / RankIC 格式化 |
| `load_report_data` | 读三年 summary.csv |
| `build_merged` | 三年因子 key 对齐合并表 |
| `load_compare_summaries` | 扫描对照目录下的 summary 路径 |
| `build_compare_detail` | 历史 A 因子对照明细表 |
| `append_compare_sections` | 向主报告追加多口径对照章节 |
| `generate_report` | 生成 2024/2025/2026 对比报告 Markdown |
| `build_parser` | CLI 参数（eval / compare / report） |
| `async_main` | 异步 CLI 主流程 |
| `main` | 同步入口（`asyncio.run`） |

模块级别名：`build_parser` / `async_main` / `main` → 转调类方法。
