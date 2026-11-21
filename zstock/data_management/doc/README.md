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

模块导出：`DataQueryService`、`strategy_store`。

---

## 方法一览

### 1. `query_service.DataQueryService`

统一 Mongo 读写与业务查询入口（进程内单例）。

#### 单例 / 连接

| 方法 | 作用 |
|------|------|
| `get_data_query_service()` | 返回进程内单例 |
| `reset_instance()` | 清空单例（连接关闭后调用，避免持有失效 db） |
| `db`（属性） | 懒加载 Mongo 数据库句柄；可注入 FakeDB 供测试 |

#### 通用 CRUD

| 方法 | 作用 |
|------|------|
| `query` | 按条件查多条；支持 sort / projection / batch_size 等 |
| `query_one` | 查单条，未命中返回 `None` |
| `insert_one` | 插入单条，返回文档 ID |
| `insert_many` | 批量插入（默认 `ordered=False`，单条冲突不丢整批） |
| `bulk_upsert` | 按 filter 批量 upsert |
| `update_one` / `update_many` | 更新文档（自动包装 `$set`） |
| `replace_one` | 整文档替换 |
| `delete_one` / `delete_many` | 删除文档 |
| `count` | 计数 |
| `drop_collection` | 删除集合 |
| `list_collections` | 列出集合名 |
| `ensure_indexes` | 创建/校正行情、资金流、板块、因子等索引 |

#### OHLCV / 股票元数据

| 方法 | 作用 |
|------|------|
| `get_ohlcv` | 单票区间行情；无数据抛 `ValueError` |
| `get_ohlcv_batch` | 多票分批×日期窗口并发查 OHLCV，返回 `{code: DataFrame}` |
| `get_stock_info` | 单票基本信息 |
| `get_all_stocks` | 全 A 股列表（含创业板/科创板/北交所/ST） |

#### 资金流

| 方法 | 作用 |
|------|------|
| `get_capital_flow` | 单票单日资金流 |
| `get_capital_flow_recent_days` | 多票截至某日各取最近 N 条资金流 |
| `get_capital_flow_range` | 多票日期区间资金流，按 code 分组升序 |

#### 龙虎榜

| 方法 | 作用 |
|------|------|
| `get_lhb` | 单票单日龙虎榜 |
| `get_lhb_recent` | 单票截至某日近 N 日龙虎榜 |
| `batch_get_lhb` | 多票单日龙虎榜批量 |
| `batch_get_lhb_recent` | 多票近 N 日龙虎榜批量 |

#### 板块

| 方法 | 作用 |
|------|------|
| `get_sector_list` | 板块元数据列表（默认 `SW2` 前缀） |
| `get_sector_stocks` | 单板块成分股代码 |
| `get_sector_stocks_batch` | 多板块成分股 `{sector_code: [codes]}` |

#### 因子预计算（单日 / 区间）

| 方法 | 作用 |
|------|------|
| `get_factor_market` | M1 市场因子单日 |
| `get_factor_sectors` | M2 板块因子单日（全板块） |
| `get_factor_dragons` | M3 龙头因子单日（可按板块过滤） |
| `get_factor_forces` | M4 合力因子单日 |
| `get_factor_market_range` | M1 区间（可 sink 流式消费） |
| `get_factor_sectors_range` | M2 区间 |
| `get_factor_dragons_range` | M3 区间（窗口并发偏低防 OOM） |
| `get_factor_forces_range` | M4 区间 |

#### 基本面

| 方法 | 作用 |
|------|------|
| `get_all_fundamental_pershare` | 全量每股指标（含 bps） |
| `get_all_fundamental_holder` | 全量股东户数历史 |
| `get_fundamental_asof` | 截至公告日的最新 BPS + 股东户数环比 `{code: {bps, holder_change}}` |

#### 内部辅助（一般不直接调用）

| 方法 | 作用 |
|------|------|
| `_iter_date_windows` | 日期区间切窗，避免单次查询过大 |
| `_finite_float` | 安全转 float |
| `_fundamental_asof_map` | 由原始文档拼 asof 映射 |
| `_prepare_update` | 更新字典包装为 `$set` |
| `_factor_range_concurrency` | 读环境变量决定因子区间查询并发 |
| `_query_window_with_retry` | 单窗口查询 + 超时/断连重试 |
| `_query_docs_by_date_windows` | 多窗口并发拉文档（因子 range 共用） |

---

### 2. `strategy_store`（策略 / 回测文档 CRUD）

集合：`zstock_strategies`、`zstock_backtests`。

| 方法 | 作用 |
|------|------|
| `ensure_strategy_indexes` | 创建策略名、更新时间、回测 strategy_id 索引 |
| `default_template_params` | 深拷贝磁盘 `strategy_params.json` 作新建模板 |
| `count_active_factors` | 统计三层 momentum/reversal 因子条数 |
| `create_strategy` | 新建策略文档 |
| `list_strategies` | 策略列表（摘要，不含完整 params） |
| `get_strategy` | 按 id 取策略全文 |
| `update_strategy` | 更新名称/描述/active_factors/backtest/params |
| `delete_strategy` | 删除策略 |
| `create_backtest_record` | 创建回测运行记录（status=running 等） |
| `finish_backtest_record` | 回写 metrics / equity / error 并标记结束 |
| `get_backtest` | 按 id 取回测记录 |
| `list_backtests` | 回测列表（可按 strategy_id 过滤；省略长 equity） |

内部：`_now` / `_oid` / `_serialize`（时间戳与 ObjectId 序列化）。

---

### 3. 脚本：`script/`

#### `sync_ohlcv`

stockdb → Mongo `zstock_ohlcv`。

| 方法 | 作用 |
|------|------|
| `main` | CLI 入口：按 `--freq` / 日期区间同步 |
| `_sync_one_freq` | 同步单个频率（1d/5m/1m） |
| `_persist_ohlcv` / `_write_ohlcv_df` | DataFrame 落库（upsert） |
| `_enrich_turnover_rate` | 日线补换手率 |
| `_load_bars` | 从 stockdb 拉 K 线 |
| `_iter_year_windows` / `_iter_day_windows` / `_iter_windows` | 按年/日切同步窗口 |
| `_parse_freqs` / `_parse_args` | 解析频率与命令行 |

#### `sync_capital_flow`

stockdb → Mongo 资金流集合。

| 方法 | 作用 |
|------|------|
| `main` | CLI 入口 |
| `sync_capital_flow_range` | 指定区间批量同步资金流 |
| `_persist_capital_flow_bulk` | 批量 upsert |
| `_load_money_flow` | 从 stockdb 拉资金流 |
| `_load_ohlcv_amount_map` | 用 OHLCV 成交额补齐/校验 |
| `_frames_to_df` | 多票 Frame 拼成落库 DataFrame |
| `_parse_args` | 命令行参数 |

#### `nightly_pipeline`

一键：OHLCV → 资金流 → 因子预计算。

| 方法 | 作用 |
|------|------|
| `main` | CLI 入口，打日志后 `asyncio.run(run_nightly)` |
| `run_nightly` | 编排三步夜跑，汇总成功/失败 |
| `_run_ohlcv_sync` | 调 sync_ohlcv |
| `_run_capital_flow_sync` | 调 sync_capital_flow |
| `_run_precompute` | 调 factor 层 `precompute_factors` |
| `_setup_logging` / `_parse_args` / `_today` / `_days_ago` | 日志与参数 |

#### `nightly_scheduler.NightlyScheduler`

定点调度 `nightly_pipeline`（状态落盘）。

| 方法 | 作用 |
|------|------|
| `status` | 返回启用状态、下次/上次运行、schedule |
| `set_enabled` | 开/关调度 |
| `set_time` | 设置每日 HH:MM |
| `run_now` | 立即跑一轮（已在跑则提示） |
| `start` / `stop` | 启动/取消后台循环任务 |
| `_execute` | 实际调用 `run_nightly` 并写状态 |
| `_loop` | 约 20s 轮询，到点执行 |
| `_load` / `_save` / `_recompute_next` | 状态文件读写与下次时间计算 |
| `main`（模块级） | 独立进程跑调度器 |

模块级单例：`scheduler`。
