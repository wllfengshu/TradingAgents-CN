# XtData 行情模块 API 接口

模块：`from xtquant import xtdata`

---

## 〇、接口概述

### 运行逻辑

xtdata 提供和 MiniQMT 的交互接口，本质是和 MiniQMT 建立连接，由 MiniQMT 处理行情数据请求，再把结果回传给 Python 层。使用的行情服务器以及能获取到的行情数据和 MiniQMT 一致。要检查数据或者切换连接时直接操作 MiniQMT 即可。

- **数据获取接口**：使用前需先确保 MiniQMT 已有所需数据。不足时通过 `download_*` 接口补充，再调用 `get_*` 获取。
- **订阅接口**：直接设置数据回调，数据到来时由回调返回。订阅接收到的数据一般会保存下来，同种数据不需要再单独补充。

### 接口分类（按前缀）

| 前缀 | 含义 |
|------|------|
| `subscribe_` / `unsubscribe_` | 订阅 / 反订阅 |
| `get_` | 获取数据 |
| `download_` | 下载数据 |

**常见用法**：
- Level1 数据历史部分用 `download_history_data` 补充，实时部分用 `subscribe_XXX` 订阅，用 `get_XXX` 获取
- Level2 数据实时部分用 `subscribe_XXX` 订阅，用 `get_l2_XXX` 获取。Level2 函数无历史数据存储，跨交易日后数据清理

### 请求限制

- **全推数据**：是市场全部合约的切面数据，高订阅数场景下推荐使用。持续订阅全推数据可获取每个合约的最新分笔数据推送，流量和处理效率优于单股订阅
- **单股订阅**：仅返回单股数据。**建议单股订阅数量不超过 50**。订阅数较多时建议直接使用全推数据
- **板块分类信息等静态信息**：更新频率低，按周或按日定期下载更新即可

### 投研版特色数据周期

下列周期需投研端权限才能获取：

| 周期值 | 含义 |
|--------|------|
| `warehousereceipt` | 期货仓单 |
| `futureholderrank` | 期货席位 |
| `interactiveqa` | 互动问答 |
| `transactioncount1m` | 逐笔成交统计 1 分钟级 |
| `transactioncount1d` | 逐笔成交统计日级 |
| `delistchangebond` | 退市可转债信息 |
| `replacechangebond` | 待发可转债信息 |
| `specialtreatment` | ST 变更历史 |
| `northfinancechange1m` | 港股通（深港通、沪港通）资金流向 1 分钟级 |
| `northfinancechange1d` | 港股通资金流向日级 |
| `dividendplaninfo` | 红利分配方案信息 |
| `historycontract` | 过期合约列表 |
| `optionhistorycontract` | 期权历史信息 |
| `historymaincontract` | 历史主力合约 |
| `stoppricedata` | 涨跌停数据 |
| `snapshotindex` | 快照指标数据 |

---

## 一、订阅接口

### `subscribe_quote` — 订阅单股行情

```python
subscribe_quote(stock_code, period='1d', start_time='', end_time='', count=0, callback=None)
```

| 参数 | 类型 | 说明 |
|------|------|------|
| stock_code | str | 合约代码，如 `'000001.SZ'` |
| period | str | 周期 |
| start_time | str | 起始时间 |
| end_time | str | 结束时间 |
| count | int | 历史数据条数；**订阅时通常传 `0`**，传 `-1` 则同时拉取历史全量数据到缓存 |
| callback | func | 数据推送回调，形式为 `on_data(datas)`，`datas` 格式为 `{stock_code: [data1, data2, ...]}` |

**返回**：订阅号（`int`），成功 `>0`，失败 `-1`

**注意**：单股订阅数量不宜超过 50，量大时改用全推。

**原文回调示例**：
```python
def on_data(datas):
    for stock_code in datas:
        print(stock_code, datas[stock_code])
```

---

### `subscribe_whole_quote` — 订阅全推行情

```python
subscribe_whole_quote(code_list, callback=None)
```

| 参数 | 类型 | 说明 |
|------|------|------|
| code_list | list | 市场代码（`['SH', 'SZ']`）或合约代码列表（`['600000.SH']`） |
| callback | func | 回调 `on_data(datas)`，`datas` 格式为 `{stock1: data1, stock2: data2, ...}` |

**返回**：订阅号，成功 `>0`，失败 `-1`

**备注**：订阅后首先推送当前最新全推数据。

---

### `unsubscribe_quote` — 反订阅

```python
unsubscribe_quote(seq)
```

| 参数 | 类型 | 说明 |
|------|------|------|
| seq | int | 订阅时返回的订阅号 |

---

### `run` — 阻塞线程接收回调

```python
run()
```

阻塞当前线程维持运行，通常在订阅后调用。连接断开时抛出异常结束循环。

---

### `subscribe_formula` — 订阅VBA模型（投研端）

```python
subscribe_formula(formula_name, stock_code, period, start_time='', end_time='', count=-1,
                  dividend_type=None, extend_param={}, callback=None)
```

订阅 VBA 模型运行结果，**需连接投研端**使用。

| 参数 | 类型 | 说明 |
|------|------|------|
| formula_name | str | 模型名 |
| stock_code | str | 模型主图代码，如 `'000300.SH'` |
| period | str | K线周期。可选范围：`'tick'`（分笔线）、`'1d'`（日线）、`'1m'`（分钟线）、`'3m'`（三分钟线）、`'5m'`（5分钟线）、`'15m'`（15分钟线）、`'30m'`（30分钟线）、`'1h'`（小时线）、`'1w'`（周线）、`'1mon'`（月线）、`'1q'`（季线）、`'1hy'`（半年线）、`'1y'`（年线） |
| start_time | str | 起始时间，形如 `'20200101'`，默认空视为最早 |
| end_time | str | 截止时间，形如 `'20200101'`，默认空视为最新 |
| count | int | 向前运行的 bar 数，`-1` 为全部 |
| dividend_type | str | 复权方式，默认使用主图除权方式。可选 `'none'/'front'/'back'/'front_ratio'/'back_ratio'` |
| extend_param | dict | 模型入参，如 `{'a': 1, '__basket': {}}`；`__basket` 为组合模型的股票池权重，形如 `{'600000.SH': 0.06, '000001.SZ': 0.01}` |
| callback | func | 数据推送回调 |

**返回**：订阅 ID（`int`），失败 `-1`

> **备注**：使用该函数时需要先补充本地 K 线或分笔数据。

---

### `unsubscribe_formula` — 反订阅模型

```python
unsubscribe_formula(subID)
```

**返回**：`bool`，成功 `True`，失败 `False`

---

## 二、数据获取接口

### `get_market_data` — 获取行情数据（旧版）

```python
get_market_data(field_list=[], stock_list=[], period='1d', start_time='', end_time='',
                count=-1, dividend_type='none', fill_data=True)
```

从缓存获取行情数据。

| 参数 | 说明 |
|------|------|
| field_list | list，数据字段列表，传空则返回全部字段 |
| stock_list | list，合约代码列表 |
| period | str，周期 |
| start_time | str，起始时间 |
| end_time | str，结束时间 |
| count | int，数据个数。**默认行为**：`count >= 0` 时，若指定了 `start_time/end_time`，以 `end_time` 为基准向前取 `count` 条；若 `start_time/end_time` 缺省，默认取本地数据最新的 `count` 条；若 `start_time/end_time/count` 都缺省，默认取本地全部数据 |
| dividend_type | str，除权方式（仅 K 线有效） |
| fill_data | bool，是否向后填充空缺数据 |

**返回**（K线周期 `1m`/`5m`/`1d` 等）：
```
dict {
    field1: pd.DataFrame(index=stock_list, columns=time_list),
    field2: pd.DataFrame(index=stock_list, columns=time_list),
    ...
}
```
各字段对应的 DataFrame 维度和索引相同。

**返回**（tick 周期）：
```
dict {
    stock_code: np.ndarray  # 按时间戳 time 增序排列
}
```

**备注**：
- 获取 Level2 数据时需要数据终端有 Level2 数据权限
- 时间范围为闭区间

**与 `get_market_data_ex` 的关键区别**：
- `get_market_data` 以**字段**为第一维键，每个值是包含所有股票的 DataFrame
- `get_market_data_ex` 以**股票代码**为第一维键，每个值是包含所有字段的 DataFrame

---

### `get_market_data_ex` — 获取行情数据（新版，推荐）

```python
get_market_data_ex(field_list=[], stock_list=[], period='1d', start_time='', end_time='',
                   count=-1, dividend_type='none', fill_data=True)
```

参数与 `get_market_data` 相同，但**返回结构不同**，更适合按标的处理数据。

**返回**（K线周期）：
```
dict {
    stock_code: pd.DataFrame(index=time_list, columns=field_list)
}
```
即每个股票对应一个 DataFrame，行为时间，列为字段。

**返回**（tick 周期）：与 `get_market_data` 的 tick 返回相同，`{stock_code: np.ndarray}`

**支持的额外功能（相比 `get_market_data`）**：
- 日线以上周期：`1w`（周线）、`1mon`（月线）、`1q`（季线）、`1hy`（半年线）、`1y`（年线）
- ETF 申赎清单数据
- 期货历史主力合约数据

**使用示例**：
```python
# 获取历史 K 线
history_data = xtdata.get_market_data_ex([], ['000001.SZ'], period='1d', count=-1)
# history_data['000001.SZ'] 是一个 DataFrame，index 为时间戳，columns 为 open/high/low/close 等

# 订阅后获取最新行情（自动拼接历史+实时）
xtdata.subscribe_quote('000001.SZ', period='1d', count=-1)
kline_data = xtdata.get_market_data_ex([], ['000001.SZ'], period='1d')

# 按字段过滤，按标的访问
data = xtdata.get_market_data_ex(['close'], ['600000.SH', '000001.SZ'], period='1d', start_time='20240101')
close_series = data['600000.SH']['close']  # pd.Series，index=时间戳

# 获取单标的 DataFrame 后计算均线
price = xtdata.get_market_data_ex(['close'], ['513050.SH'], period='5m')['513050.SH']
ma5 = price['close'].rolling(5).mean()
```

---

### `get_local_data` — 从本地文件获取行情数据

```python
get_local_data(field_list=[], stock_list=[], period='1d', start_time='', end_time='',
               count=-1, dividend_type='none', fill_data=True, data_dir=data_dir)
```

直接读取本地数据文件，用于快速批量获取历史数据，**无需连接 MiniQMT**。

| 参数 | 说明 |
|------|------|
| data_dir | MiniQMT 的 `userdata_mini` 路径；默认自动获取，也可通过 `xtdata.data_dir` 修改全局默认值 |

**返回格式与 `get_market_data` 相同**。

**注意**：仅支持 level1 数据。

---

### `get_full_tick` — 获取全推分笔数据（最新快照）

```python
get_full_tick(code_list)
```

| 参数 | 类型 | 说明 |
|------|------|------|
| code_list | list | 市场代码（`['SH', 'SZ']`）或合约代码列表（`['600000.SH']`） |

**返回**：`dict {stock_code: data}`，data 字段见数据字典中的 tick 字段。

**示例**：
```python
from xtquant import xtdata

code = '600000.SH'
full_tick = xtdata.get_full_tick([code])
print('全推数据', full_tick)

# 取买一价作为对手价（买入用卖一价，卖出用买一价）
# 若买一价为0表示跌停，取最新价
tick = xtdata.get_full_tick(['000001.SZ'])
for i in tick:
    fix_price = tick[i]["bidPrice"][0] if tick[i]["bidPrice"][0] != 0 else tick[i]["lastPrice"]
    print(fix_price)
```

---

### `get_full_kline` — 获取最新交易日K线全推数据

```python
get_full_kline(field_list=[], stock_list=[], period='1m', start_time='', end_time='',
               count=1, dividend_type='none', fill_data=True)
```

仅支持**最新一个交易日**，不含历史值。参数参考 `get_market_data`。

**返回**：`dict {field: DataFrame}`

---

### `get_divid_factors` — 获取除权数据

```python
get_divid_factors(stock_code, start_time='', end_time='')
```

**返回**：`pd.DataFrame`，字段见数据字典中的除权数据字段。

---

## 三、下载接口

### `download_history_data` — 下载历史行情（单股）

```python
download_history_data(stock_code, period, start_time='', end_time='', incrementally=None)
```

| 参数 | 类型 | 说明 |
|------|------|------|
| stock_code | str | 合约代码 |
| period | str | 周期 |
| start_time | str | 起始时间 |
| end_time | str | 结束时间 |
| incrementally | None/bool | `None`：由 start_time 控制（start_time 为空则增量下载，否则全量下载指定范围）；`True`：强制增量下载（从本地最后一条往后）；`False`：强制全量下载 |

**同步执行，完成后返回，无返回值。**

---

### `download_history_data2` — 下载历史行情（批量）

```python
download_history_data2(stock_list, period, start_time='', end_time='', callback=None,
                       incrementally=None)
```

| 参数 | 说明 |
|------|------|
| stock_list | list，合约列表 |
| callback | 进度回调函数，参数为 dict：`{'total': n, 'finished': n, 'stockcode': '...', 'message': '...'}` |

**同步执行，完成后返回，无返回值。**

**回调示例**：
```python
def on_progress(data):
    print(data)
    # {'finished': 1, 'total': 50, 'stockcode': '000001.SZ', 'message': ''}
```

---

### `download_financial_data` — 下载财务数据

```python
download_financial_data(stock_list, table_list=[])
```

同步执行，无返回值。

---

### `download_financial_data2` — 下载财务数据（带时间范围）

```python
download_financial_data2(stock_list, table_list=[], start_time='', end_time='', callback=None)
```

按 `m_anntime`（披露日期）字段在 `[start_time, end_time]` 范围筛选。进度回调格式同 `download_history_data2`。

---

### `download_sector_data` — 下载板块分类信息

```python
download_sector_data()
```

同步执行，无返回值。板块数据更新频率低，建议按日或按周定期下载。

---

### `download_index_weight` — 下载指数成分权重

```python
download_index_weight()
```

同步执行，无返回值。

---

### `download_cb_data` — 下载可转债基础信息

```python
download_cb_data()
```

无参数，无返回值。

---

### `download_etf_info` — 下载ETF申赎清单信息

```python
download_etf_info()
```

无参数，无返回值。

---

### `download_holiday_data` — 下载节假日数据

```python
download_holiday_data()
```

无参数，无返回值。

---

### `download_history_contracts` — 下载过期/退市合约信息

```python
download_history_contracts()
```

下载后可通过 `get_stock_list_in_sector` 获取退市标的列表，通过 `get_instrument_detail` 查看合约详情。

查看所有过期板块名称：
```python
print([i for i in xtdata.get_sector_list() if "过期" in i])
```

---

## 四、基础信息接口

### `get_instrument_detail` — 获取合约基础信息

```python
get_instrument_detail(stock_code, iscomplete=False)
```

| 参数 | 类型 | 说明 |
|------|------|------|
| stock_code | str | 合约代码 |
| iscomplete | bool | `False`（默认）返回常用字段；`True` 返回全部字段（含手续费、期权类型等扩展字段） |

**返回**：`dict`，找不到合约时返回 `None`。可用于检查合约代码是否正确。

`iscomplete=False` 时的常用返回字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| ExchangeID | str | 合约市场代码 |
| InstrumentID | str | 合约代码 |
| InstrumentName | str | 合约名称 |
| ProductID | str | 合约品种ID（期货） |
| ProductName | str | 合约品种名称（期货） |
| ExchangeCode | str | 交易所代码 |
| UniCode | str | 统一规则代码 |
| OpenDate | str | IPO 日期（股票） |
| CreateDate | str | 上市日期（期货） |
| ExpireDate | int | 退市日/到期日 |
| PreClose | float | 前收盘价 |
| SettlementPrice | float | 前结算价 |
| UpStopPrice | float | 涨停价 |
| DownStopPrice | float | 跌停价 |
| FloatVolume | float | 流通股本 |
| TotalVolume | float | 总股本 |
| LongMarginRatio | float | 多头保证金率 |
| ShortMarginRatio | float | 空头保证金率 |
| PriceTick | float | 最小价格变动单位 |
| VolumeMultiple | int | 合约乘数（非期货默认 1） |
| MainContract | int | 主力合约标记（1=第一主力，2=第二主力，3=第三主力） |
| LastVolume | int | 昨日持仓量 |
| InstrumentStatus | int | 合约停牌状态 |
| IsTrading | bool | 合约是否可交易 |
| IsRecent | bool | 是否是近月合约 |
| OpenInterestMultiple | int | 交割月持仓倍数 |

`iscomplete=True` 时增加的字段（部分）：

| 字段 | 类型 | 说明 |
|------|------|------|
| ChargeType | int | 手续费方式（0=未知，1=元/手，2=费率‱） |
| ChargeOpen | float | 开仓手续费(率)，返回 `-1` 时无效 |
| ChargeClose | float | 平仓手续费(率)，返回 `-1` 时无效 |
| ChargeTodayOpen | float | 开今仓手续费(率)，返回 `-1` 时无效 |
| ChargeTodayClose | float | 平今仓手续费(率)，返回 `-1` 时无效 |
| OptionType | int | 期权类型（`-1`=非期权，`0`=认购，`1`=认沽） |
| OptUndlCode | str | 期权标的证券代码/可转债正股代码 |
| OptUndlMarket | str | 期权标的证券市场 |
| OptExercisePrice | float | 期权行权价/可转债转股价 |

完整字段列表见附录文件 `02_xtdata_fields.md`。

---

### `get_option_detail_data` — 获取期权/期货合约详细信息

```python
get_option_detail_data(code)
```

| 参数 | 类型 | 说明 |
|------|------|------|
| code | str | 合约代码，如 `'sc2403C465.INE'` |

**返回**：`dict`，包含合约详细信息字段（与 `get_instrument_detail` iscomplete=True 返回字段集合相同）。

**使用示例**：
```python
from xtquant import xtdata

# 获取期权合约对应的期货标的
info = xtdata.get_option_detail_data('sc2403C465.INE')
underline_code = info["OptUndlCode"] + "." + {"SHFE":"SF","CZCE":"ZF","DCE":"DF","INE":"INE","GFEX":"GF"}[info["OptUndlMarket"]]
print(underline_code)  # 'sc2403.INE'
```

---

### `get_instrument_type` — 获取合约类型

```python
get_instrument_type(stock_code)
```

**返回**：`dict {type_name: bool}`，找不到返回 `None`

```python
# 示例返回值
{'index': False, 'stock': True, 'fund': False, 'etf': False}
```

可用 type_name：`'index'`（指数）、`'stock'`（股票）、`'fund'`（基金）、`'etf'`（ETF）

---

### `get_trading_dates` — 获取交易日列表

```python
get_trading_dates(market, start_time='', end_time='', count=-1)
```

| 参数 | 类型 | 说明 |
|------|------|------|
| market | str | 市场代码，如 `'SH'`、`'SZ'` |
| start_time | str | 起始时间 |
| end_time | str | 结束时间 |
| count | int | 数据个数 |

**返回**：`list`，时间戳列表

---

### `get_trading_calendar` — 获取交易日历

```python
get_trading_calendar(market, start_time='', end_time='')
```

| 参数 | 类型 | 说明 |
|------|------|------|
| market | str | 市场代码 |
| start_time | str | 起始时间，8位字符串，空表示该市场首个交易日 |
| end_time | str | 结束时间，8位字符串，**可填未来时间**以获取未来交易日 |

**返回**：`list`，完整交易日列表。需要先下载节假日数据（`download_holiday_data()`）。

---

### `get_trading_time` — 获取交易时段

```python
get_trading_time(market, traded_date=None)
```

| 参数 | 类型 | 说明 |
|------|------|------|
| market | str | 市场代码 |
| traded_date | str/None | 指定日期，None 表示最新 |

**备注**：此接口由旧版 `get_trade_times` 改名而来（2024-01-22），同时更新了实现逻辑。旧版接口名 `get_trade_times` 已废弃，不再支持 `tradetimes` 参数。

---

### `get_holidays` — 获取节假日数据

```python
get_holidays()
```

**返回**：`list`，8 位日期字符串（如 `'20240101'`），包含截止当年的节假日日期。

---

### `get_period_list` — 获取可用周期列表

```python
get_period_list()
```

**返回**：`list`，当前可用的周期字符串列表。

---

## 五、板块接口

### `get_sector_list` — 获取板块列表

```python
get_sector_list()
```

**返回**：`list [sector1, sector2, ...]`。**需先执行 `download_sector_data()`**。

---

### `get_stock_list_in_sector` — 获取板块成分股

```python
get_stock_list_in_sector(sector_name, real_timetag=None)
```

| 参数 | 类型 | 说明 |
|------|------|------|
| sector_name | str | 板块名称 |
| real_timetag | str/None | 指定日期的板块成分（历史成分），`None` 表示最新成分 |

**返回**：`list [stock1, stock2, ...]`。**需要板块分类信息**（先 `download_sector_data()`）。

---

### `get_index_weight` — 获取指数成分权重

```python
get_index_weight(index_code)
```

**返回**：`dict {stock_code: weight}`。需先执行 `download_index_weight()`。

---

### `create_sector_folder` — 创建板块目录节点

```python
create_sector_folder(parent_node, folder_name, overwrite=True)
```

| 参数 | 说明 |
|------|------|
| parent_node | 父节点，`' '` 表示"我的"（默认目录） |
| folder_name | 要创建的目录名称 |
| overwrite | `True`（默认）：目标已存在则跳过；`False`：在名称后追加自增数字编号 |

**返回**：实际创建的目录名（`str`）

---

### `create_sector` — 创建板块

```python
create_sector(parent_node, sector_name, overwrite=True)
```

参数同 `create_sector_folder`。**返回**：实际创建的板块名（`str`）

---

### `add_sector` — 添加自定义板块成分股

```python
add_sector(sector_name, stock_list)
```

无返回值。

---

### `remove_stock_from_sector` — 移除板块成分股

```python
remove_stock_from_sector(sector_name, stock_list)
```

**返回**：`bool`，成功 `True`，失败 `False`

---

### `remove_sector` — 移除自定义板块

```python
remove_sector(sector_name)
```

无返回值。

---

### `reset_sector` — 重置板块成分股

```python
reset_sector(sector_name, stock_list)
```

**返回**：`bool`，成功 `True`，失败 `False`

---

## 六、财务数据接口

### `get_financial_data` — 获取财务数据

```python
get_financial_data(stock_list, table_list=[], start_time='', end_time='',
                   report_type='report_time')
```

| 参数 | 说明 |
|------|------|
| table_list | 财务表名列表（见下方可选值），传空则获取全部 |
| report_type | `'report_time'`（按截止日期筛选）或 `'announce_time'`（按披露日期筛选） |

财务表名可选值：

| 表名 | 含义 |
|------|------|
| `Balance` | 资产负债表 |
| `Income` | 利润表 |
| `CashFlow` | 现金流量表 |
| `Capital` | 股本表 |
| `Holdernum` | 股东数 |
| `Top10holder` | 十大股东 |
| `Top10flowholder` | 十大流通股东 |
| `Pershareindex` | 每股指标 |

**返回**：`dict {stock_code: {table_name: pd.DataFrame}}`，各表字段见 `02_xtdata_fields.md`。

---

### `download_financial_data` — 下载财务数据

```python
download_financial_data(stock_list, table_list=[])
```

同步执行，无返回值。

---

### `download_financial_data2` — 下载财务数据（带时间范围）

```python
download_financial_data2(stock_list, table_list=[], start_time='', end_time='', callback=None)
```

按 `m_anntime` 披露日期字段在 `[start_time, end_time]` 范围筛选。同步执行，无返回值。

---

## 七、其他数据接口

### `get_cb_info` — 获取可转债基础信息

```python
get_cb_info(stockcode)
```

**需先执行 `download_cb_data()`。**

**返回**：`dict`，可转债信息字典。

---

### `get_ipo_info` — 获取新股申购信息

```python
get_ipo_info(start_time, end_time)
```

`start_time` 和 `end_time` 为空则返回全部数据，格式如 `'20230327'`。

**返回**：`list[dict]`，每项字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| securityCode | str | 证券代码 |
| codeName | str | 代码简称 |
| market | str | 所属市场 |
| actIssueQty | int | 发行总量（股） |
| onlineIssueQty | int | 网上发行量（股） |
| onlineSubCode | str | 申购代码 |
| onlineSubMaxQty | int | 申购上限（股） |
| publishPrice | float | 发行价格 |
| isProfit | int | 是否已盈利（0=上市时尚未盈利，1=已盈利） |
| industryPe | float | 行业市盈率 |
| afterPE | float | 发行后市盈率 |

---

### `get_etf_info` — 获取ETF申赎清单信息

```python
get_etf_info()
```

**需先执行 `download_etf_info()`。返回**：`dict`，所有ETF申赎数据。

---

## 八、VBA模型接口（投研端）

### `call_formula` — 调用VBA模型

```python
call_formula(formula_name, stock_code, period, start_time="", end_time="",
             count=-1, dividend_type="none", extend_param={})
```

使用前需先补充本地 K 线或分笔数据。

| 参数 | 说明 |
|------|------|
| formula_name | str，模型名称 |
| stock_code | str，主图代码如 `'000300.SH'` |
| period | str，K 线周期。可选：`'tick'/'1d'/'1m'/'3m'/'5m'/'15m'/'30m'/'1h'/'1w'/'1mon'/'1q'/'1hy'/'1y'` |
| start_time | str，模型运行起始时间，形如 `'20200101'`，默认空视为最早 |
| end_time | str，模型运行截止时间，形如 `'20200101'`，默认空视为最新 |
| count | int，模型运行范围为向前 count 根 bar，默认 `-1` 运行所有 bar |
| dividend_type | str，复权方式。可选 `'none'/'front'/'back'/'front_ratio'/'back_ratio'` |
| extend_param | dict，如 `{"模型名:参数名": 参数值}`，例如跑模型 MA 时 `{'MA:n1': 1}`；支持 `__basket` 键设置组合权重，形如 `{'__basket': {'600000.SH': 0.06, '000001.SZ': 0.01}}`；若模型1调用模型2，可用 `{'模型2: 参数': 参数值}` 修改模型2的参数 |

**返回**：
```python
{
    'dbt': 0,                          # 返回数据类型，0=全部历史数据
    'timelist': [...],                 # 时间范围列表
    'outputs': {'var1': [...], ...}    # 输出变量名：变量值列表
}
```

---

### `call_formula_batch` — 批量调用VBA模型

```python
call_formula_batch(formula_names, stock_codes, period, start_time="", end_time="",
                   count=-1, dividend_type="none", extend_params=[])
```

使用前需先补充本地 K 线或分笔数据。

| 参数 | 说明 |
|------|------|
| formula_names | list，包含要批量运行的模型名 |
| stock_codes | list，包含要批量运行的模型主图代码（形如 `'stkcode.market'`） |
| period | str，K 线周期。可选范围同 `call_formula` |
| start_time | str，起始时间，默认空视为最早 |
| end_time | str，截止时间，默认空视为最新 |
| count | int，模型运行范围为向前 count 根 bar，默认 `-1` |
| dividend_type | str，复权方式，可选 `'none'/'front'/'back'/'front_ratio'/'back_ratio'` |
| extend_params | list，包含每个模型的入参，形如 `[{"模型名:参数名": 参数值}]` |

**返回**：`list[dict]`，每项含以下字段：
- `formula`：模型名
- `stock`：品种代码
- `argument`：参数
- `result`：dict，参考 `call_formula` 返回结果

---

### `generate_index_data` — 生成因子数据文件（投研端）

```python
generate_index_data(formula_name, formula_param={}, stock_list=[], period='1d',
                    dividend_type='none', start_time='', end_time='',
                    fill_mode='fixed', fill_value=float('nan'), result_path=None)
```

在本地生成 **feather 格式**因子数据文件。**必须连接投研端**，`formula_name` 须存在于投研端中。

| 参数 | 说明 |
|------|------|
| fill_mode | `'fixed'`：固定值填充；`'forward'`：向前延续 |
| fill_value | 填充数值，默认 `float('nan')` |
| result_path | 结果文件路径（feather格式），None 则不保存文件 |

**返回**：None

---

## 九、连接相关接口

### `reconnect` — 连接到指定 IP 端口

```python
reconnect(...)
```

> 备注：源文档（版本日志 2023-02-06）记载"添加连接到指定 ip 端口的接口 reconnect"，但**未在正文给出详细签名**。该接口在 xtdata 实际存在，详细参数请参考官方 SDK 或源码。

---

### `connect` — 连接到 MiniQMT

```python
xtdata.connect(port=...)
```

供 `xtdatacenter` 监听端口后由 xtdata 主动连接使用。详见 `05_examples.md` 中"连接 VIP 服务器"示例。

---

### `get_quote_server_status` — 获取当前数据连接站点状态

```python
servers = xtdata.get_quote_server_status()
```

返回当前连接的行情服务器状态信息字典。

---

### `watch_quote_server_status` — 监听行情连接状态变更

```python
xtdata.watch_quote_server_status(callback)
```

注册行情连接状态变化回调；回调收到 `dict`，含 `ip`、`port`、`status` 字段。

---

## 十、时间戳转换工具

```python
import time

def conv_time(ct):
    """将毫秒时间戳转为字符串
    conv_time(1476374400000) --> '20161014000000.000'
    """
    local_time = time.localtime(ct / 1000)
    data_head = time.strftime('%Y%m%d%H%M%S', local_time)
    data_secs = (ct - int(ct)) * 1000
    return '%s.%03d' % (data_head, data_secs)
```

---

## 十一、版本变更说明（对调用方有 API 影响）

| 日期 | 变更内容 |
|------|---------|
| 2020-09-07 | 添加获取除权数据接口 `get_divid_factors`；`get_trading_dates` 支持指定日期范围 |
| 2020-09-13 | 添加财务数据接口；将"补充"调整为"下载"，`supply_*` 接口改为 `download_*`；`volumn` 拼写错误修正为 `volume`（影响 tick、l2quote 周期数据成交量字段；合约基础信息总股本、流通股本字段） |
| 2020-11-23 | 合约基础信息 `CreateDate`、`OpenDate` 字段类型由 `int` 调整为 `str`；添加数据字典部分（Level2 数据字段枚举说明） |
| 2021-07-20 | 添加新版下载数据接口 `download_history_data2`、`download_financial_data2` |
| 2021-12-30 | 委托方向、成交类型添加上交所/深交所撤单信息区分说明 |
| 2022-06-27 | K 线添加 `preClose`（前收价）、`suspendFlag`（停牌标记）字段 |
| 2022-09-30 | 添加交易日历相关接口 `get_holidays`、`get_trading_calendar`、`get_trade_times` |
| 2023-01-04 | 添加千档行情获取 |
| 2023-01-31 | 添加可转债基础信息下载/获取 `download_cb_data`、`get_cb_info` |
| 2023-02-06 | 添加连接到指定 ip 端口的接口 `reconnect` |
| 2023-02-07 | 支持 QMT 的本地 Python 模式；多 QMT 同时存在时自动选择 xtdata 连接端口 |
| 2023-03-27 | 添加新股申购信息获取 `get_ipo_info` |
| 2023-04-13 | 本地 Python 模式下运行 VBA 函数 |
| 2023-08-21 | 数据接口支持投研版特色数据；`get_instrument_detail` 返回字段增加 `ExchangeCode`、`UniCode`；添加 `get_period_list` |
| 2023-10-11 | `get_market_data_ex` 支持获取 ETF 申赎清单数据；数据字典添加现金替代标志 |
| 2023-11-09 | `download_history_data` 添加 `incrementally` 增量下载参数 |
| 2023-11-22 | `get_trading_calendar` 不再支持 `tradetimes` 参数 |
| 2023-11-27 | 添加 ETF 申赎清单信息下载/获取 `download_etf_info`、`get_etf_info` |
| 2023-11-28 | 添加节假日下载 `download_holiday_data` |
| 2023-12-27 | `get_stock_list_in_sector` 增加北交所板块 |
| 2024-01-19 | `get_market_data_ex` 支持获取期货历史主力合约数据、商品期权品种数据、日线以上周期 K 线（`1w`/`1mon`/`1q`/`1hy`/`1y`） |
| 2024-01-22 | `get_trade_times` 改名为 `get_trading_time` 并更新实现逻辑 |
| 2024-01-26 | `get_instrument_detail` 支持 `iscomplete=True` 获取全部合约信息字段 |
| 2024-05-15 | 添加获取最新交易日 K 线数据 `get_full_kline` |
| 2024-05-27 | `get_stock_list_in_sector` 增加 `real_timetag` 参数（获取指定日期的板块成分） |
