# XtQuant 交易数据结构与枚举值

---

## 一、账号对象

### `StockAccount`

```python
from xtquant.xttype import StockAccount

acc = StockAccount('1000000365')           # 股票账号（默认）
acc = StockAccount('1000000365', 'STOCK')  # 股票账号
acc = StockAccount('1000000365', 'CREDIT') # 信用账号
acc = StockAccount('1000000365', 'FUTURE') # 期货账号
acc = StockAccount('1000000365', 'HUGANGTONG')   # 沪港通
acc = StockAccount('1000000365', 'SHENGANGTONG') # 深港通
```

---

## 二、数据结构

### `XtAsset` — 资产

| 属性 | 类型 | 说明 |
|------|------|------|
| account_type | int | 账号类型 |
| account_id | str | 资金账号 |
| cash | float | 可用金额 |
| frozen_cash | float | 冻结金额 |
| market_value | float | 持仓市值 |
| total_asset | float | 总资产 |

---

### `XtOrder` — 委托

| 属性 | 类型 | 说明 |
|------|------|------|
| account_type | int | 账号类型 |
| account_id | str | 资金账号 |
| stock_code | str | 证券代码，如 `"600000.SH"` |
| stock_code1 | str | 长代码（2024-04-25 新增，适配长代码） |
| order_id | int | 订单编号 |
| order_sysid | str | 柜台合同编号 |
| order_time | int | 报单时间 |
| order_type | int | 委托类型 |
| order_volume | int | 委托数量 |
| price_type | int | 报价类型（柜台返回值，与下单传入不等价） |
| price | float | 委托价格 |
| traded_volume | int | 成交数量 |
| traded_price | float | 成交均价 |
| order_status | int | 委托状态 |
| status_msg | str | 委托状态描述（如废单原因） |
| strategy_name | str | 策略名称 |
| order_remark | str | 委托备注（最大 24 英文字符） |
| direction | int | 多空方向（股票不适用） |
| offset_flag | int | 交易操作（区分买卖/开平仓） |

---

### `XtTrade` — 成交

| 属性 | 类型 | 说明 |
|------|------|------|
| account_type | int | 账号类型 |
| account_id | str | 资金账号 |
| stock_code | str | 证券代码 |
| stock_code1 | str | 长代码（2024-04-25 新增） |
| order_type | int | 委托类型 |
| traded_id | str | 成交编号 |
| traded_time | int | 成交时间 |
| traded_price | float | 成交均价 |
| traded_volume | int | 成交数量 |
| traded_amount | float | 成交金额 |
| order_id | int | 订单编号 |
| order_sysid | str | 柜台合同编号 |
| strategy_name | str | 策略名称 |
| order_remark | str | 委托备注（最大 24 英文字符） |
| direction | int | 多空方向（股票不适用） |
| offset_flag | int | 交易操作 |

---

### `XtPosition` — 持仓

| 属性 | 类型 | 说明 |
|------|------|------|
| account_type | int | 账号类型 |
| account_id | str | 资金账号 |
| stock_code | str | 证券代码 |
| stock_code1 | str | 长代码（2024-04-25 新增） |
| volume | int | 持仓数量 |
| can_use_volume | int | 可用数量 |
| open_price | float | 开仓价（返回值与成本价一致） |
| market_value | float | 市值 |
| frozen_volume | int | 冻结数量 |
| on_road_volume | int | 在途股份 |
| yesterday_volume | int | 昨夜拥股 |
| avg_price | float | 成本价 |
| direction | int | 多空方向（股票不适用） |

---

### `XtPositionStatistics` — 期货持仓统计

| 属性 | 类型 | 说明 |
|------|------|------|
| account_id | str | 账户 |
| exchange_id | str | 市场代码 |
| exchange_name | str | 市场名称 |
| product_id | str | 品种代码 |
| instrument_id | str | 合约代码 |
| instrument_name | str | 合约名称 |
| direction | int | 多空方向（股票不适用） |
| hedge_flag | int | 投保类型 |
| position | int | 持仓数量 |
| yesterday_position | int | 昨仓数量 |
| today_position | int | 今仓数量 |
| can_close_vol | int | 可平数量 |
| position_cost | float | 持仓成本 |
| avg_price | float | 持仓均价 |
| position_profit | float | 持仓盈亏 |
| float_profit | float | 浮动盈亏 |
| open_price | float | 开仓均价 |
| open_cost | float | 开仓成本 |
| used_margin | float | 已使用保证金 |
| used_commission | float | 已使用的手续费 |
| frozen_margin | float | 冻结保证金 |
| frozen_commission | float | 冻结手续费 |
| instrument_value | float | 市值，合约价值 |
| open_times | int | 开仓次数 |
| open_volume | int | 总开仓量（中间平仓不减） |
| cancel_times | int | 撤单次数 |
| last_price | float | 最新价 |
| rise_ratio | float | 当日涨幅 |
| product_name | str | 产品名称 |
| royalty | float | 权利金市值 |
| expire_date | str | 到期日 |
| assest_weight | float | 资产占比 |
| increase_by_settlement | float | 当日涨幅（结） |
| margin_ratio | float | 保证金占比 |
| float_profit_divide_by_used_margin | float | 浮盈比例（保证金） |
| float_profit_divide_by_balance | float | 浮盈比例（动态权益） |
| today_profit_loss | float | 当日盈亏（结） |
| yesterday_init_position | int | 昨日持仓 |
| frozen_royalty | float | 冻结权利金 |
| today_close_profit_loss | float | 当日盈亏（收） |
| close_profit | float | 平仓盈亏 |
| ft_product_name | str | 品种名称 |

---

### `XtOrderResponse` — 异步下单委托反馈

| 属性 | 类型 | 说明 |
|------|------|------|
| account_type | int | 账号类型 |
| account_id | str | 资金账号 |
| order_id | int | 订单编号 |
| strategy_name | str | 策略名称 |
| order_remark | str | 委托备注 |
| seq | int | 异步下单的请求序号 |

---

### `XtCancelOrderResponse` — 异步撤单委托反馈

| 属性 | 类型 | 说明 |
|------|------|------|
| account_type | int | 账号类型 |
| account_id | str | 资金账号 |
| order_id | int | 订单编号 |
| order_sysid | str | 柜台委托编号 |
| cancel_result | int | 撤单结果（0 成功，-1 失败） |
| seq | int | 异步撤单的请求序号 |

---

### `XtOrderError` — 下单失败错误

| 属性 | 类型 | 说明 |
|------|------|------|
| account_type | int | 账号类型 |
| account_id | str | 资金账号 |
| order_id | int | 订单编号 |
| error_id | int | 下单失败错误码 |
| error_msg | str | 下单失败具体信息 |
| strategy_name | str | 策略名称 |
| order_remark | str | 委托备注 |

---

### `XtCancelError` — 撤单失败错误

| 属性 | 类型 | 说明 |
|------|------|------|
| account_type | int | 账号类型 |
| account_id | str | 资金账号 |
| order_id | int | 订单编号 |
| market | int | 交易市场（0:上海，1:深圳） |
| order_sysid | str | 柜台委托编号 |
| error_id | int | 撤单失败错误码 |
| error_msg | str | 撤单失败具体信息 |

---

### `XtCreditDetail` — 信用账号资产

| 属性 | 类型 | 说明 |
|------|------|------|
| account_type | int | 账号类型 |
| account_id | str | 资金账号 |
| m_nStatus | int | 账号状态 |
| m_nUpdateTime | int | 更新时间 |
| m_nCalcConfig | int | 计算参数 |
| m_dFrozenCash | float | 冻结金额 |
| m_dBalance | float | 总资产 |
| m_dAvailable | float | 可用金额 |
| m_dPositionProfit | float | 持仓盈亏 |
| m_dMarketValue | float | 总市值 |
| m_dFetchBalance | float | 可取金额 |
| m_dStockValue | float | 股票市值 |
| m_dFundValue | float | 基金市值 |
| m_dTotalDebt | float | 总负债 |
| m_dEnableBailBalance | float | 可用保证金 |
| m_dPerAssurescaleValue | float | 维持担保比例 |
| m_dAssureAsset | float | 净资产 |
| m_dFinDebt | float | 融资负债 |
| m_dFinDealAvl | float | 融资本金 |
| m_dFinFee | float | 融资息费 |
| m_dSloDebt | float | 融券负债 |
| m_dSloMarketValue | float | 融券市值 |
| m_dSloFee | float | 融券息费 |
| m_dOtherFare | float | 其它费用 |
| m_dFinMaxQuota | float | 融资授信额度 |
| m_dFinEnableQuota | float | 融资可用额度 |
| m_dFinUsedQuota | float | 融资冻结额度 |
| m_dSloMaxQuota | float | 融券授信额度 |
| m_dSloEnableQuota | float | 融券可用额度 |
| m_dSloUsedQuota | float | 融券冻结额度 |
| m_dSloSellBalance | float | 融券卖出资金 |
| m_dUsedSloSellBalance | float | 已用融券卖出资金 |
| m_dSurplusSloSellBalance | float | 剩余融券卖出资金 |

---

### `StkCompacts` — 负债合约

| 属性 | 类型 | 说明 |
|------|------|------|
| account_type | int | 账号类型 |
| account_id | str | 资金账号 |
| compact_type | int | 合约类型 |
| cashgroup_prop | int | 头寸来源 |
| exchange_id | int | 证券市场 |
| open_date | int | 开仓日期 |
| business_vol | int | 合约证券数量 |
| real_compact_vol | int | 未还合约数量 |
| ret_end_date | int | 到期日 |
| business_balance | float | 合约金额 |
| businessFare | float | 合约息费 |
| real_compact_balance | float | 未还合约金额 |
| real_compact_fare | float | 未还合约息费 |
| repaid_fare | float | 已还息费 |
| repaid_balance | float | 已还金额 |
| instrument_id | str | 证券代码 |
| compact_id | str | 合约编号 |
| position_str | str | 定位串 |

---

### `CreditSubjects` — 融资融券标的

| 属性 | 类型 | 说明 |
|------|------|------|
| account_type | int | 账号类型 |
| account_id | str | 资金账号 |
| slo_status | int | 融券状态（见下表） |
| fin_status | int | 融资状态 |
| exchange_id | int | 证券市场 |
| slo_ratio | float | 融券保证金比例 |
| fin_ratio | float | 融资保证金比例 |
| instrument_id | str | 证券代码 |

**融券状态说明**

| 返回值 | 状态 |
|---|---|
| 48 | 正常 |
| 49 | 暂停 |
| 50 | 作废 |

---

### `CreditSloCode` — 可融券数据

| 属性 | 类型 | 说明 |
|------|------|------|
| account_type | int | 账号类型 |
| account_id | str | 资金账号 |
| cashgroup_prop | int | 头寸来源 |
| exchange_id | int | 证券市场 |
| enable_amount | int | 融券可融数量 |
| instrument_id | str | 证券代码 |

---

### `CreditAssure` — 标的担保品

| 属性 | 类型 | 说明 |
|------|------|------|
| account_type | int | 账号类型 |
| account_id | str | 资金账号 |
| assure_status | int | 是否可做担保 |
| exchange_id | int | 证券市场 |
| assure_ratio | float | 担保品折算比例 |
| instrument_id | str | 证券代码 |

---

### `XtAccountStatus` — 账号状态

| 属性 | 类型 | 说明 |
|------|------|------|
| account_type | int | 账号类型 |
| account_id | str | 资金账号 |
| status | int | 账号状态（见枚举） |

---

### `XtAccountInfo` — 账号信息

| 属性 | 类型 | 说明 |
|------|------|------|
| account_type | int | 账号类型 |
| account_id | str | 资金账号 |
| broker_type | int | 同 account_type |
| platform_id | int | 平台号 |
| account_classification | int | 账号分类 |
| login_status | int | 账号状态 |

---

### `XtSmtAppointmentResponse` — 约券相关异步接口的反馈

| 属性 | 类型 | 说明 |
|------|------|------|
| seq | int | 异步请求序号 |
| success | bool | 申请是否成功 |
| msg | str | 反馈信息 |
| apply_id | str | 若申请成功返回资券申请编号，否则返回 -1 |

> 注：原文档示例代码中曾打印 `response.account_id, response.order_sysid, response.error_id, response.error_msg, response.seq`，与数据结构定义不一致；以此处结构定义为准。

---

## 三、枚举值（xtconstant）

### 交易市场（market）

| 常量 | 说明 |
|------|------|
| `xtconstant.SH_MARKET` | 上交所 |
| `xtconstant.SZ_MARKET` | 深交所 |
| `xtconstant.MARKET_ENUM_BEIJING` | 北交所 |
| `xtconstant.MARKET_ENUM_SHANGHAI_HONGKONG_STOCK` | 沪港通 |
| `xtconstant.MARKET_ENUM_SHENZHEN_HONGKONG_STOCK` | 深港通 |
| `xtconstant.MARKET_ENUM_SHANGHAI_FUTURE` | 上期所 |
| `xtconstant.MARKET_ENUM_DALIANG_FUTURE` | 大商所 |
| `xtconstant.MARKET_ENUM_ZHENGZHOU_FUTURE` | 郑商所 |
| `xtconstant.MARKET_ENUM_INDEX_FUTURE` | 中金所 |
| `xtconstant.MARKET_ENUM_INTL_ENERGY_FUTURE` | 能源中心 |
| `xtconstant.MARKET_ENUM_GUANGZHOU_FUTURE` | 广期所 |
| `xtconstant.MARKET_ENUM_SHANGHAI_STOCK_OPTION` | 上海期权 |
| `xtconstant.MARKET_ENUM_SHENZHEN_STOCK_OPTION` | 深证期权 |

---

### 账号类型（account_type）

| 常量 | 说明 |
|------|------|
| `xtconstant.SECURITY_ACCOUNT` | 股票 |
| `xtconstant.CREDIT_ACCOUNT` | 信用 |
| `xtconstant.FUTURE_ACCOUNT` | 期货 |
| `xtconstant.FUTURE_OPTION_ACCOUNT` | 期货期权 |
| `xtconstant.STOCK_OPTION_ACCOUNT` | 股票期权 |
| `xtconstant.HUGANGTONG_ACCOUNT` | 沪港通 |
| `xtconstant.SHENGANGTONG_ACCOUNT` | 深港通 |

---

### 委托类型（order_type）

**股票**

| 常量 | 说明 |
|------|------|
| `xtconstant.STOCK_BUY` | 买入 |
| `xtconstant.STOCK_SELL` | 卖出 |

**信用**

| 常量 | 说明 |
|------|------|
| `xtconstant.CREDIT_BUY` | 担保品买入 |
| `xtconstant.CREDIT_SELL` | 担保品卖出 |
| `xtconstant.CREDIT_FIN_BUY` | 融资买入 |
| `xtconstant.CREDIT_SLO_SELL` | 融券卖出 |
| `xtconstant.CREDIT_BUY_SECU_REPAY` | 买券还券 |
| `xtconstant.CREDIT_DIRECT_SECU_REPAY` | 直接还券 |
| `xtconstant.CREDIT_SELL_SECU_REPAY` | 卖券还款 |
| `xtconstant.CREDIT_DIRECT_CASH_REPAY` | 直接还款 |
| `xtconstant.CREDIT_FIN_BUY_SPECIAL` | 专项融资买入 |
| `xtconstant.CREDIT_SLO_SELL_SPECIAL` | 专项融券卖出 |
| `xtconstant.CREDIT_BUY_SECU_REPAY_SPECIAL` | 专项买券还券 |
| `xtconstant.CREDIT_DIRECT_SECU_REPAY_SPECIAL` | 专项直接还券 |
| `xtconstant.CREDIT_SELL_SECU_REPAY_SPECIAL` | 专项卖券还款 |
| `xtconstant.CREDIT_DIRECT_CASH_REPAY_SPECIAL` | 专项直接还款 |

**期货（六键风格）**

| 常量 | 说明 |
|------|------|
| `xtconstant.FUTURE_OPEN_LONG` | 开多 |
| `xtconstant.FUTURE_CLOSE_LONG_HISTORY` | 平昨多 |
| `xtconstant.FUTURE_CLOSE_LONG_TODAY` | 平今多 |
| `xtconstant.FUTURE_OPEN_SHORT` | 开空 |
| `xtconstant.FUTURE_CLOSE_SHORT_HISTORY` | 平昨空 |
| `xtconstant.FUTURE_CLOSE_SHORT_TODAY` | 平今空 |

**期货（四键风格）**

| 常量 | 说明 |
|------|------|
| `xtconstant.FUTURE_CLOSE_LONG_TODAY_FIRST` | 平多，优先平今 |
| `xtconstant.FUTURE_CLOSE_LONG_HISTORY_FIRST` | 平多，优先平昨 |
| `xtconstant.FUTURE_CLOSE_SHORT_TODAY_FIRST` | 平空，优先平今 |
| `xtconstant.FUTURE_CLOSE_SHORT_HISTORY_FIRST` | 平空，优先平昨 |

**期货（两键风格）**

| 常量 | 说明 |
|------|------|
| `xtconstant.FUTURE_CLOSE_LONG_TODAY_HISTORY_THEN_OPEN_SHORT` | 卖出，优先平多（优先平今），余量开空 |
| `xtconstant.FUTURE_CLOSE_LONG_HISTORY_TODAY_THEN_OPEN_SHORT` | 卖出，优先平多（优先平昨），余量开空 |
| `xtconstant.FUTURE_CLOSE_SHORT_TODAY_HISTORY_THEN_OPEN_LONG` | 买入，优先平空（优先平今），余量开多 |
| `xtconstant.FUTURE_CLOSE_SHORT_HISTORY_TODAY_THEN_OPEN_LONG` | 买入，优先平空（优先平昨），余量开多 |
| `xtconstant.FUTURE_OPEN` | 买入，不优先平仓 |
| `xtconstant.FUTURE_CLOSE` | 卖出，不优先平仓 |

**期货（跨商品套利）**

| 常量 | 说明 |
|------|------|
| `xtconstant.FUTURE_ARBITRAGE_OPEN` | 开仓 |
| `xtconstant.FUTURE_ARBITRAGE_CLOSE_HISTORY_FIRST` | 平，优先平昨 |
| `xtconstant.FUTURE_ARBITRAGE_CLOSE_TODAY_FIRST` | 平，优先平今 |

**期货（展期）**

| 常量 | 说明 |
|------|------|
| `xtconstant.FUTURE_RENEW_LONG_CLOSE_HISTORY_FIRST` | 看多，优先平昨 |
| `xtconstant.FUTURE_RENEW_LONG_CLOSE_TODAY_FIRST` | 看多，优先平今 |
| `xtconstant.FUTURE_RENEW_SHORT_CLOSE_HISTORY_FIRST` | 看空，优先平昨 |
| `xtconstant.FUTURE_RENEW_SHORT_CLOSE_TODAY_FIRST` | 看空，优先平今 |

**股票期权**

| 常量 | 说明 |
|------|------|
| `xtconstant.STOCK_OPTION_BUY_OPEN` | 买入开仓 |
| `xtconstant.STOCK_OPTION_SELL_CLOSE` | 卖出平仓 |
| `xtconstant.STOCK_OPTION_SELL_OPEN` | 卖出开仓 |
| `xtconstant.STOCK_OPTION_BUY_CLOSE` | 买入平仓 |
| `xtconstant.STOCK_OPTION_COVERED_OPEN` | 备兑开仓 |
| `xtconstant.STOCK_OPTION_COVERED_CLOSE` | 备兑平仓 |
| `xtconstant.STOCK_OPTION_CALL_EXERCISE` | 认购行权 |
| `xtconstant.STOCK_OPTION_PUT_EXERCISE` | 认沽行权 |
| `xtconstant.STOCK_OPTION_SECU_LOCK` | 证券锁定 |
| `xtconstant.STOCK_OPTION_SECU_UNLOCK` | 证券解锁 |

**期货期权**

| 常量 | 说明 |
|------|------|
| `xtconstant.OPTION_FUTURE_OPTION_EXERCISE` | 期货期权行权 |

**ETF申赎**

| 常量 | 说明 |
|------|------|
| `xtconstant.ETF_PURCHASE` | 申购 |
| `xtconstant.ETF_REDEMPTION` | 赎回 |

---

### 报价类型（price_type）

> 市价类型仅在实盘环境生效，模拟环境不支持市价报单。

| 常量 | 说明 |
|------|------|
| `xtconstant.FIX_PRICE` | 指定价（限价） |
| `xtconstant.LATEST_PRICE` | 最新价 |

**郑商所期货**

| 常量 | 说明 |
|------|------|
| `xtconstant.MARKET_BEST` | 市价最优价 |

**大商所期货**

| 常量 | 说明 |
|------|------|
| `xtconstant.MARKET_CANCEL` | 市价即成剩撤 |
| `xtconstant.MARKET_CANCEL_ALL` | 市价全额成交或撤 |

**中金所期货**

| 常量 | 说明 |
|------|------|
| `xtconstant.MARKET_CANCEL_1` | 市价最优一档即成剩撤 |
| `xtconstant.MARKET_CANCEL_5` | 市价最优五档即成剩撤 |
| `xtconstant.MARKET_CONVERT_1` | 市价最优一档即成剩转 |
| `xtconstant.MARKET_CONVERT_5` | 市价最优五档即成剩转 |

**上交所/北交所股票**

| 常量 | 说明 |
|------|------|
| `xtconstant.MARKET_SH_CONVERT_5_CANCEL` | 最优五档即时成交剩余撤销 |
| `xtconstant.MARKET_SH_CONVERT_5_LIMIT` | 最优五档即时成交剩转限价 |
| `xtconstant.MARKET_PEER_PRICE_FIRST` | 对手方最优价格委托 |
| `xtconstant.MARKET_MINE_PRICE_FIRST` | 本方最优价格委托 |

**深交所股票/期权**

| 常量 | 说明 |
|------|------|
| `xtconstant.MARKET_PEER_PRICE_FIRST` | 对手方最优价格委托（上深通用） |
| `xtconstant.MARKET_MINE_PRICE_FIRST` | 本方最优价格委托（上深通用） |
| `xtconstant.MARKET_SZ_INSTBUSI_RESTCANCEL` | 即时成交剩余撤销委托 |
| `xtconstant.MARKET_SZ_CONVERT_5_CANCEL` | 最优五档即时成交剩余撤销 |
| `xtconstant.MARKET_SZ_FULL_OR_CANCEL` | 全额成交或撤销委托 |

---

### 委托状态（order_status）

| 常量 | 值 | 含义 |
|------|---|------|
| `xtconstant.ORDER_UNREPORTED` | 48 | 未报 |
| `xtconstant.ORDER_WAIT_REPORTING` | 49 | 待报 |
| `xtconstant.ORDER_REPORTED` | 50 | 已报 |
| `xtconstant.ORDER_REPORTED_CANCEL` | 51 | 已报待撤 |
| `xtconstant.ORDER_PARTSUCC_CANCEL` | 52 | 部成待撤 |
| `xtconstant.ORDER_PART_CANCEL` | 53 | 部撤 |
| `xtconstant.ORDER_CANCELED` | 54 | 已撤 |
| `xtconstant.ORDER_PART_SUCC` | 55 | 部成 |
| `xtconstant.ORDER_SUCCEEDED` | 56 | 已成 |
| `xtconstant.ORDER_JUNK` | 57 | 废单 |
| `xtconstant.ORDER_UNKNOWN` | 255 | 未知 |

---

### 账号状态（account_status）

| 常量 | 值 | 含义 |
|------|---|------|
| `xtconstant.ACCOUNT_STATUS_INVALID` | -1 | 无效 |
| `xtconstant.ACCOUNT_STATUS_OK` | 0 | 正常 |
| `xtconstant.ACCOUNT_STATUS_WAITING_LOGIN` | 1 | 连接中 |
| `xtconstant.ACCOUNT_STATUSING` | 2 | 登陆中 |
| `xtconstant.ACCOUNT_STATUS_FAIL` | 3 | 失败 |
| `xtconstant.ACCOUNT_STATUS_INITING` | 4 | 初始化中 |
| `xtconstant.ACCOUNT_STATUS_CORRECTING` | 5 | 数据刷新校正中 |
| `xtconstant.ACCOUNT_STATUS_CLOSED` | 6 | 收盘后 |
| `xtconstant.ACCOUNT_STATUS_ASSIS_FAIL` | 7 | 穿透副链接断开 |
| `xtconstant.ACCOUNT_STATUS_DISABLEBYSYS` | 8 | 系统停用（总线使用-密码错误超限） |
| `xtconstant.ACCOUNT_STATUS_DISABLEBYUSER` | 9 | 用户停用（总线使用） |

---

### 划拨方向（transfer_direction）

| 常量 | 值 | 含义 |
|------|---|------|
| `xtconstant.FUNDS_TRANSFER_NORMAL_TO_SPEED` | 510 | 普通柜台→极速柜台 |
| `xtconstant.FUNDS_TRANSFER_SPEED_TO_NORMAL` | 511 | 极速柜台→普通柜台 |
| `xtconstant.NODE_FUNDS_TRANSFER_SH_TO_SZ` | 512 | 节点资金：上海→深圳 |
| `xtconstant.NODE_FUNDS_TRANSFER_SZ_TO_SH` | 513 | 节点资金：深圳→上海 |

---

### 多空方向（direction）

| 常量 | 值 | 含义 |
|------|---|------|
| `xtconstant.DIRECTION_FLAG_LONG` | 48 | 多 |
| `xtconstant.DIRECTION_FLAG_SHORT` | 49 | 空 |

---

### 交易操作（offset_flag）

| 常量 | 值 | 含义 |
|------|---|------|
| `xtconstant.OFFSET_FLAG_OPEN` | 48 | 买入/开仓 |
| `xtconstant.OFFSET_FLAG_CLOSE` | 49 | 卖出/平仓 |
| `xtconstant.OFFSET_FLAG_FORCECLOSE` | 50 | 强平 |
| `xtconstant.OFFSET_FLAG_CLOSETODAY` | 51 | 平今 |
| `xtconstant.OFFSET_FLAG_ClOSEYESTERDAY` | 52 | 平昨 |
| `xtconstant.OFFSET_FLAG_FORCEOFF` | 53 | 强减 |
| `xtconstant.OFFSET_FLAG_LOCALFORCECLOSE` | 54 | 本地强平 |

> 注：`OFFSET_FLAG_ClOSEYESTERDAY` 中间的 `l` 为小写——原 API 命名保持一致，调用时请按原拼写引用。

---

## 四、版本变更说明

| 日期 | 主要变更 |
|------|---------|
| 2020-09-01 | 初稿 |
| 2020-10-14 | 持仓结构添加字段；投资备注相关修正 |
| 2020-10-21 | 添加信用交易相关委托类型（order_type）枚举；调整 XtQuant 运行依赖环境说明 |
| 2020-11-13 | 添加信用交易相关类型定义及接口；新增异步撤单委托反馈结构、下单失败与撤单失败主推结构；新增订阅/反订阅、创建 API 实例、注册回调、`start`、`connect`、`stop`、`run_forever` 等接口；接口分为「系统设置/操作/查询/信用查询/回调类」五类；添加股票异步撤单接口，原撤单接口更名为股票同步撤单；所有"证券账号"改为"资金账号" |
| 2020-11-19 | 添加账号状态主推接口、`XtAccountStatus` 结构及账号状态枚举；补充异步下单/撤单回报推送接口 |
| 2021-07-20 | 修改回调/主推函数实现机制，提升报撤单回报速度；`run_forever()` 支持 Ctrl+C 跳出 |
| 2022-06-27 | `query_stock_orders` 支持仅查询可撤委托；新增 `query_new_purchase_limit`、`query_ipo_data`、`query_account_infos` 接口 |
| 2022-11-15 | 修复 `XtQuantTrader.unsubscribe` 的实现 |
| 2022-11-17 | 交易数据字典格式调整 |
| 2022-11-28 | 主动请求接口返回增加专用线程，新增 `set_relaxed_response_order_enabled` |
| 2023-07-17 | `XtPosition` 成本价字段调整：`open_price`（开仓价）/ `avg_price`（成本价） |
| 2023-07-26 | 新增资金划拨接口 `fund_transfer` |
| 2023-08-11 | 新增 `query_com_fund`、`query_com_position`（划拨业务普通柜台查询） |
| 2023-10-16 | 新增期货市价报价类型：`MARKET_BEST`（郑商所）、`MARKET_CANCEL`/`MARKET_CANCEL_ALL`（大商所）、`MARKET_CANCEL_1`/`MARKET_CANCEL_5`/`MARKET_CONVERT_1`/`MARKET_CONVERT_5`（中金所） |
| 2023-10-20 | `XtOrder`、`XtTrade`、`XtPosition` 新增 `direction`（多空方向）；`XtOrder`、`XtTrade` 新增 `offset_flag`（交易操作） |
| 2023-11-03 | 新增 `smt_query_quoter`、`smt_negotiate_order`（实际为 `smt_negotiate_order_async`）、`smt_query_compact` |
| 2024-01-02 | 委托类型新增 ETF 申赎（`ETF_PURCHASE`/`ETF_REDEMPTION`） |
| 2024-02-29 | 新增期货持仓统计查询接口 `query_position_statistics` |
| 2024-04-25 | 数据结构新增 `stock_code1` 字段以适配长代码 |
| 2024-05-24 | 新增通用数据导出/查询接口 `export_data`、`query_data` |
| 2024-06-27 | 新增外部成交导入接口 `sync_transaction_from_external` |
