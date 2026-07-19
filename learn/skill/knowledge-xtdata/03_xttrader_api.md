# XtQuant 交易模块 API 接口

模块：`from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback`
账号：`from xtquant.xttype import StockAccount`
常量：`from xtquant import xtconstant`

---

## 一、系统设置接口

### `XtQuantTrader` — 创建API实例

```python
xt_trader = XtQuantTrader(path, session_id)
```

| 参数 | 类型 | 说明 |
|------|------|------|
| path | str | MiniQMT 客户端 `userdata_mini` 的完整路径 |
| session_id | int | 会话 ID，同时运行的策略不能重复 |

**通常只需创建一个实例。**

---

### `register_callback` — 注册回调类

```python
xt_trader.register_callback(callback)
```

---

### `start` — 启动交易线程

```python
xt_trader.start()
```

---

### `connect` — 建立连接

```python
connect_result = xt_trader.connect()
```

**返回**：`0` 成功，非 `0` 失败。该连接为一次性连接，断开后需重新调用。

---

### `stop` — 停止运行

```python
xt_trader.stop()
```

---

### `run_forever` — 阻塞线程

```python
xt_trader.run_forever()
```

阻塞当前线程直到 `stop()` 被调用。支持 `Ctrl+C` 跳出。

---

### `set_relaxed_response_order_enabled` — 开启专用响应线程

```python
xt_trader.set_relaxed_response_order_enabled(enabled)
```

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| enabled | bool | False | `True`：在 `on_stock_order` 等回调中调用同步查询不会卡住；但查询和推送数据时序不确定 |

**建议**：在回调中使用异步查询接口（如 `query_stock_orders_async`）代替开启此选项。

---

## 二、操作接口

### `subscribe` — 订阅账号信息

```python
subscribe_result = xt_trader.subscribe(account)
```

订阅后可收到资金、委托、成交、持仓等主推消息。

**返回**：`0` 成功，`-1` 失败

---

### `unsubscribe` — 反订阅账号信息

```python
xt_trader.unsubscribe(account)
```

**返回**：`0` 成功，`-1` 失败

---

### `order_stock` — 股票同步报单

```python
order_id = xt_trader.order_stock(account, stock_code, order_type, order_volume,
                                  price_type, price, strategy_name, order_remark)
```

| 参数 | 类型 | 说明 |
|------|------|------|
| account | StockAccount | 资金账号 |
| stock_code | str | 证券代码，如 `'600000.SH'` |
| order_type | int | 委托类型（见数据字典） |
| order_volume | int | 委托数量（股票：股；债券：张） |
| price_type | int | 报价类型（见数据字典） |
| price | float | 委托价格 |
| strategy_name | str | 策略名称 |
| order_remark | str | 委托备注（最大 24 个英文字符） |

**返回**：订单编号（`int`）；成功 `>0`，失败 `-1`

---

### `order_stock_async` — 股票异步报单

```python
seq = xt_trader.order_stock_async(account, stock_code, order_type, order_volume,
                                   price_type, price, strategy_name, order_remark)
```

**返回**：下单请求序号 `seq`；成功 `>0`，失败 `-1`

下单成功后会收到 `on_order_stock_async_response` 回调。

---

### `cancel_order_stock` — 股票同步撤单（按订单编号）

```python
cancel_result = xt_trader.cancel_order_stock(account, order_id)
```

> 备注：对于期货，`order_id` 取 `order.order_sysid` 字段。

**返回**：`0` 成功，`-1` 失败

---

### `cancel_order_stock_sysid` — 股票同步撤单（按柜台合同编号）

```python
cancel_result = xt_trader.cancel_order_stock_sysid(account, market, order_sysid)
```

| 参数 | 类型 | 说明 |
|------|------|------|
| market | int | 交易市场（`xtconstant.SH_MARKET` 等） |
| order_sysid | str | 券商柜台合同编号 |

**返回**：`0` 成功，`-1` 失败

---

### `cancel_order_stock_async` — 股票异步撤单（按订单编号）

```python
cancel_result = xt_trader.cancel_order_stock_async(account, order_id)
```

> 备注：对于期货，`order_id` 取 `order.order_sysid` 字段；失败时通过撤单失败主推接口（`on_cancel_error`）返回失败信息。

**返回**：撤单请求序号；成功 `>0`，失败 `-1`

---

### `cancel_order_stock_sysid_async` — 股票异步撤单（按柜台合同编号）

```python
cancel_result = xt_trader.cancel_order_stock_sysid_async(account, market, order_sysid)
```

**投研端 market 参数可填 `0`，券商端按实际填写。**

---

### `fund_transfer` — 资金划拨

```python
success, msg = xt_trader.fund_transfer(account, transfer_direction, price)
```

| 参数 | 说明 |
|------|------|
| transfer_direction | 划拨方向（见 `xtconstant` 枚举） |
| price | 划拨金额（`float`） |

---

### `sync_transaction_from_external` — 外部交易数据录入

```python
result = xt_trader.sync_transaction_from_external(operation, data_type, account, deal_list)
```

| 参数 | 说明 |
|------|------|
| operation | `"UPDATE"` / `"REPLACE"` / `"ADD"` / `"DELETE"` |
| data_type | 数据类型，如 `"DEAL"` |
| deal_list | 成交列表，每项为成交对象的参数字典 |

---

## 三、查询接口

### `query_stock_asset` — 资产查询

```python
asset = xt_trader.query_stock_asset(account)
```

**返回**：`XtAsset` 对象或 `None`（查询失败）

---

### `query_stock_orders` — 委托查询

```python
orders = xt_trader.query_stock_orders(account, cancelable_only=False)
```

| 参数 | 说明 |
|------|------|
| cancelable_only | `True` 只查询可撤委托 |

**返回**：`list[XtOrder]` 或 `None`

---

### `query_stock_order` — 按订单编号查询委托

```python
order = xt_trader.query_stock_order(account, order_id)
```

> 备注：源文档未单列章节，仅在示例代码中出现；接口在 xttrader 中实际存在。

**返回**：`XtOrder` 或 `None`

---

### `query_stock_trades` — 成交查询

```python
trades = xt_trader.query_stock_trades(account)
```

**返回**：`list[XtTrade]` 或 `None`

---

### `query_stock_positions` — 持仓查询

```python
positions = xt_trader.query_stock_positions(account)
```

**返回**：`list[XtPosition]` 或 `None`

---

### `query_stock_position` — 按股票代码查询持仓

```python
position = xt_trader.query_stock_position(account, stock_code)
```

> 备注：源文档未单列章节，仅在示例代码中出现；接口在 xttrader 中实际存在。

**返回**：`XtPosition` 或 `None`

---

### `query_position_statistics` — 期货持仓统计查询

```python
positions = xt_trader.query_position_statistics(account)
```

**返回**：`list[XtPositionStatistics]` 或 `None`

---

### `query_account_infos` — 账号信息查询

```python
infos = xt_trader.query_account_infos()
```

**返回**：`list[XtAccountInfo]`

---

### `query_account_status` — 账号状态查询

```python
statuses = xt_trader.query_account_status()
```

**返回**：`list[XtAccountStatus]`

---

### `query_new_purchase_limit` — 新股申购额度查询

```python
limit = xt_trader.query_new_purchase_limit(account)
```

**返回**：`dict {'KCB': n, 'SH': n, 'SZ': n}`（债券申购额度固定 10000 张）

---

### `query_ipo_data` — 当日新股信息查询

```python
ipo_data = xt_trader.query_ipo_data()
```

**返回**：`dict {stock_code: info}`，info 含 `name`、`type`、`maxPurchaseNum`、`minPurchaseNum`、`purchaseDate`、`issuePrice`

---

### `query_com_fund` — 普通柜台资金查询（划拨业务）

```python
result = xt_trader.query_com_fund(account)
```

**返回**：`dict`，包含以下字段（`success` 标识成功，`erro` 为错误信息，注意原 API 拼写为 `erro` 而非 `error`）：

| 字段 | 类型 | 含义 |
|------|------|------|
| success | bool | 操作是否成功 |
| erro | str | 错误信息 |
| currentBalance | double | 当前余额 |
| enableBalance | double | 可用余额 |
| fetchBalance | double | 可取金额 |
| interest | double | 待入账利息 |
| assetBalance | double | 总资产 |
| fetchCash | double | 可取现金 |
| marketValue | double | 市值 |
| debt | double | 负债 |

---

### `query_com_position` — 普通柜台持仓查询（划拨业务）

```python
result = xt_trader.query_com_position(account)
```

**返回**：`list[dict]`，每个 `dict` 包含以下字段：

| 字段 | 类型 | 含义 |
|------|------|------|
| success | bool | 操作是否成功 |
| error | str | 错误信息 |
| stockAccount | str | 股东号 |
| exchangeType | str | 交易市场 |
| stockCode | str | 证券代码 |
| stockName | str | 证券名称 |
| totalAmt | float | 总量 |
| enableAmount | float | 可用量 |
| lastPrice | float | 最新价 |
| costPrice | float | 成本价 |
| income | float | 盈亏 |
| incomeRate | float | 盈亏比例 |
| marketValue | float | 市值 |
| costBalance | float | 成本总额 |
| bsOnTheWayVol | int | 买卖在途量 |
| prEnableVol | int | 申赎可用量 |

---

### `export_data` — 通用数据导出

```python
result = xt_trader.export_data(account, result_path, data_type,
                                start_time=None, end_time=None, user_param={})
```

| 参数 | 说明 |
|------|------|
| result_path | 导出路径，含文件名及 `.csv` 后缀 |
| data_type | 数据类型，如 `'deal'` |

---

### `query_data` — 通用数据查询

```python
data = xt_trader.query_data(account, result_path, data_type,
                             start_time=None, end_time=None, user_param={})
```

内部调用 `export_data` 后读取数据，读完删除文件。

---

## 四、信用查询接口

### `query_credit_detail` — 信用账号资产查询

```python
datas = xt_trader.query_credit_detail(account)
```

**返回**：`list[XtCreditDetail]` 或 `None`

---

### `query_stk_compacts` — 负债合约查询

```python
datas = xt_trader.query_stk_compacts(account)
```

**返回**：`list[StkCompacts]` 或 `None`

---

### `query_credit_subjects` — 融资融券标的查询

```python
datas = xt_trader.query_credit_subjects(account)
```

**返回**：`list[CreditSubjects]` 或 `None`

---

### `query_credit_slo_code` — 可融券数据查询

```python
datas = xt_trader.query_credit_slo_code(account)
```

**返回**：`list[CreditSloCode]` 或 `None`

---

### `query_credit_assure` — 标的担保品查询

```python
datas = xt_trader.query_credit_assure(account)
```

**返回**：`list[CreditAssure]` 或 `None`

---

## 五、约券接口（信用账号）

### `smt_query_quoter` — 券源行情查询

```python
quoters = xt_trader.smt_query_quoter(account)
```

**返回**：`list[dict]`，每个 `dict` 包含以下字段：

| 字段 | 类型 | 含义 |
|------|------|------|
| success | bool | 是否成功 |
| error | str | 错误信息 |
| finType | str | 金融品种 |
| stockType | str | 证券类型 |
| date | int | 期限天数 |
| code | str | 证券代码 |
| codeName | str | 证券代码名称 |
| exchangeType | str | 市场 |
| fsmpOccupedRate | float | 资券占用利率 |
| fineRate | float | 罚息利率 |
| fsmpreendRate | float | 资券提前归还利率 |
| usedRate | float | 资券使用利率 |
| unUusedRate | float | 资券占用未使用利率 |
| initDate | int | 交易日期 |
| endDate | int | 到期日期 |
| enableSloAmountT0 | float | T+0 可融券数量 |
| enableSloAmountT3 | float | T+3 可融券数量 |
| srcGroupId | str | 来源组编号 |
| applyMode | str | 资券申请方式（"1":库存券，"2":专项券） |
| lowDate | int | 最低期限天数 |

---

### `smt_negotiate_order_async` — 库存券约券申请（异步）

```python
seq = xt_trader.smt_negotiate_order_async(account, src_group_id, order_code,
                                           date, amount, apply_rate, dict_param={})
```

| 参数 | 类型 | 说明 |
|------|------|------|
| account | StockAccount | 信用资金账号 |
| src_group_id | str | 来源组编号 |
| order_code | str | 证券代码，如 `'600000.SH'` |
| date | int | 期限天数 |
| amount | int | 委托数量 |
| apply_rate | float | 资券申请利率 |
| dict_param | dict | 可选：`{'subFareRate': float (提前归还利率), 'fineRate': float (罚息利率)}` |

**返回**：请求序号 `seq`；成功 `>0`，失败 `-1`。成功后会收到 `on_smt_appointment_async_response` 回报。

> 备注：原文档 2023-11-03 版本说明中列名为 `smt_negotiate_order`（无 `_async` 后缀），实际接口为 `smt_negotiate_order_async`。

---

### `smt_query_compact` — 约券合约查询

```python
compacts = xt_trader.smt_query_compact(account)
```

**返回**：`list[dict]`，每个 `dict` 包含以下字段：

| 字段 | 类型 | 含义 |
|------|------|------|
| success | bool | 是否成功 |
| error | str | 错误信息 |
| createDate | int | 创建日期 |
| cashcompactId | str | 头寸合约编号 |
| oriCashcompactId | str | 原头寸合约编号 |
| applyId | str | 资券申请编号 |
| srcGroupId | str | 来源组编号 |
| comGroupId | str | 资券组合编号 |
| finType | str | 金融品种 |
| exchangeType | str | 市场 |
| code | str | 证券代码 |
| codeName | str | 证券代码名称 |
| date | int | 期限天数 |
| beginCompacAmount | float | 期初合约数量 |
| beginCompacBalance | float | 期初合约金额 |
| compacAmount | float | 合约数量 |
| compacBalance | float | 合约金额 |
| returnAmount | float | 返还数量 |
| returnBalance | float | 返还金额 |
| realBuyAmount | float | 回报买入数量 |
| fsmpOccupedRate | float | 资券占用利率 |
| compactInterest | float | 合约利息金额 |
| compactFineInterest | float | 合约罚息金额 |
| repaidInterest | float | 已还利息 |
| repaidFineInterest | float | 归还罚息 |
| fineRate | float | 罚息利率 |
| preendRate | float | 资券提前归还利率 |
| compactType | str | 资券合约类型 |
| postponeTimes | int | 展期次数 |
| compactStatus | str | 资券合约状态（"0":未归还，"1":部分归还，"2":提前了结，"3":到期了结，"4":逾期了结，"5":逾期，"9":已作废） |
| lastInterestDate | int | 上次结息日期 |
| interestEndDate | int | 记息结束日期 |
| validDate | int | 有效日期 |
| dateClear | int | 清算日期 |
| usedAmount | float | 已使用数量 |
| usedBalance | float | 使用金额 |
| usedRate | float | 资券使用利率 |
| unUusedRate | float | 资券占用未使用利率 |
| srcGroupName | str | 来源组名称 |
| repaidDate | int | 归还日期 |
| preOccupedInterest | float | 当日实际应收利息 |
| compactInterestx | float | 合约总利息 |
| enPostponeAmount | float | 可展期数量 |
| postponeStatus | str | 合约展期状态（"0":未审核，"1":审核通过，"2":已撤销，"3":审核不通过） |
| applyMode | str | 资券申请方式（"1":库存券，"2":专项券） |

---

## 六、回调类（XtQuantTraderCallback）

继承 `XtQuantTraderCallback` 并实现以下方法：

### `on_disconnected` — 连接断开

```python
def on_disconnected(self):
    pass
```

---

### `on_account_status` — 账号状态变动推送

```python
def on_account_status(self, status):
    # status: XtAccountStatus
    print(status.account_id, status.account_type, status.status)
```

---

### `on_stock_order` — 委托信息推送

```python
def on_stock_order(self, order):
    # order: XtOrder
    print(order.stock_code, order.order_status, order.order_sysid)
```

---

### `on_stock_trade` — 成交信息推送

```python
def on_stock_trade(self, trade):
    # trade: XtTrade
    print(trade.account_id, trade.stock_code, trade.order_id)
```

---

### `on_order_error` — 下单失败推送

```python
def on_order_error(self, order_error):
    # order_error: XtOrderError
    print(order_error.order_id, order_error.error_id, order_error.error_msg)
```

---

### `on_cancel_error` — 撤单失败推送

```python
def on_cancel_error(self, cancel_error):
    # cancel_error: XtCancelError
    print(cancel_error.order_id, cancel_error.error_id, cancel_error.error_msg)
```

---

### `on_order_stock_async_response` — 异步下单回报推送

```python
def on_order_stock_async_response(self, response):
    # response: XtOrderResponse
    print(response.account_id, response.order_id, response.seq)
```

---

### `on_cancel_order_stock_async_response` — 异步撤单回报推送

> 备注：原文档**未列出此回调章节**，但 `XtCancelOrderResponse` 数据结构存在；此处根据数据结构推断保留，实际使用以柜台行为为准。

```python
def on_cancel_order_stock_async_response(self, response):
    # response: XtCancelOrderResponse
    print(response.cancel_result, response.seq)
```

---

### `on_stock_asset` — 资金变动推送

> 备注：原文档**未列出此回调章节**；此处为常见用法推断，实际是否触发请以柜台行为为准。

```python
def on_stock_asset(self, asset):
    # asset: XtAsset
    print(asset.account_id, asset.cash, asset.total_asset)
```

---

### `on_smt_appointment_async_response` — 约券异步回报推送

```python
def on_smt_appointment_async_response(self, response):
    # response: XtSmtAppointmentResponse
    print(response.seq, response.success, response.msg, response.apply_id)
```

> 备注：原文档示例代码中曾打印 `response.account_id, response.order_sysid, response.error_id, response.error_msg, response.seq`，与 `XtSmtAppointmentResponse` 数据结构定义不一致；此处以结构定义为准。
