# XtQuant / MiniQMT 知识库总览

## 什么是 XtQuant

XtQuant 是基于迅投 MiniQMT 衍生的 Python 策略运行框架，以 Python 库形式提供量化交易所需的**行情**和**交易** API。

- **行情模块**：`xtquant.xtdata`（`XtData`）
- **交易模块**：`xtquant.xttrader`（`XtQuantTrader`）

## 运行依赖

- Python 版本：64 位 3.6 / 3.7 / 3.8 / 3.9 / 3.10 / 3.11 / 3.12（导入时自动切换）
- 必须先启动 **MiniQMT 客户端**（极简模式登录）才能运行策略

## 架构原理

```
Python 策略
    │
    ├── xtdata  ──连接──▶ MiniQMT（行情服务器）
    └── xttrader──连接──▶ MiniQMT（柜台）
```

- xtdata 本质是与 MiniQMT 建立连接，由 MiniQMT 处理行情请求后回传
- 历史数据存储在本地（压缩格式），需先 `download_*` 再 `get_*`
- 订阅接口实时推送，订阅数据会自动保存，无需重复补充

## 合约代码格式（stock_code）

```
格式：<代码>.<市场>
示例：000001.SZ  600000.SH  000300.SH  rb2410.SF
```

| 市场后缀 | 含义 |
|---------|------|
| SH | 上交所 |
| SZ | 深交所 |
| BJ | 北交所 |
| SF | 上期所（期货） |
| DF | 大商所（期货） |
| ZF | 郑商所（期货） |
| IF | 中金所（期货） |
| INE | 能源中心（期货） |
| GF | 广期所（期货） |

## 周期（period）说明

| 周期值 | 含义 |
|-------|------|
| `tick` | 分笔数据 |
| `1m` | 1分钟线 |
| `5m` | 5分钟线 |
| `15m` | 15分钟线 |
| `30m` | 30分钟线 |
| `1h` | 1小时线 |
| `1d` | 日线 |
| `1w` | 周线 |
| `1mon` | 月线 |
| `1q` | 季度线 |
| `1hy` | 半年线 |
| `1y` | 年线 |

投研版特色周期（需投研端权限）：

| 周期值 | 含义 |
|-------|------|
| `warehousereceipt` | 期货仓单 |
| `futureholderrank` | 期货席位 |
| `interactiveqa` | 互动问答 |
| `transactioncount1m` | 逐笔成交统计1分钟级 |
| `transactioncount1d` | 逐笔成交统计日级 |
| `stoppricedata` | 涨跌停数据 |
| `snapshotindex` | 快照指标数据 |

## 复权方式（dividend_type）

| 值 | 含义 |
|---|------|
| `none` | 不复权 |
| `front` | 前复权 |
| `back` | 后复权 |
| `front_ratio` | 等比前复权 |
| `back_ratio` | 等比后复权 |

> 复权仅对 K 线数据有效，tick 等其他周期无效。

## 时间范围参数

- `start_time`：起始时间，为空则取最早
- `end_time`：结束时间，为空则取最新
- `count`：`-1` 返回全部；`>0` 限制条数；`0` 不返回数据
- 范围为闭区间 `[start_time, end_time]` 中最后不多于 count 条

## 知识库文件目录

| 文件 | 内容 |
|------|------|
| `01_xtdata_api.md` | 行情模块所有 API 接口 |
| `02_xtdata_fields.md` | 行情数据字段字典 |
| `03_xttrader_api.md` | 交易模块所有 API 接口 |
| `04_xttrader_data_structures.md` | 交易数据结构与枚举值 |
| `05_examples.md` | 完整代码示例 |
| `06_faq.md` | 常见问题 |
