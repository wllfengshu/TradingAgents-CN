# XtQuant 完整代码示例

---

## 一、行情模块示例

### 1.1 新手入门：历史数据下载 + 实时订阅

```python
# 用前须知
## xtdata提供和MiniQmt的交互接口，本质是和MiniQmt建立连接，由MiniQmt处理行情数据请求，再把结果回传返回到python层。使用的行情服务器以及能获取到的行情数据和MiniQmt是一致的，要检查数据或者切换连接时直接操作MiniQmt即可。
## 对于数据获取接口，使用时需要先确保MiniQmt已有所需要的数据，如果不足可以通过补充数据接口补充，再调用数据获取接口获取。
## 对于订阅接口，直接设置数据回调，数据到来时会由回调返回。订阅接收到的数据一般会保存下来，同种数据不需要再单独补充。

# 代码讲解

# 从本地python导入xtquant库，如果出现报错则说明安装失败
from xtquant import xtdata
import time

# 设定一个标的列表
code_list = ["000001.SZ"]
# 设定获取数据的周期
period = "1d"

# 下载标的行情数据
if 1:
    ## 为了方便用户进行数据管理，xtquant的大部分历史数据都是以压缩形式存储在本地的
    ## 比如行情数据，需要通过download_history_data下载，财务数据需要通过
    ## 所以在取历史数据之前，我们需要调用数据下载接口，将数据下载到本地
    for i in code_list:
        xtdata.download_history_data(i, period=period, incrementally=True)  # 增量下载行情数据（开高低收,等等）到本地

    xtdata.download_financial_data(code_list)  # 下载财务数据到本地
    xtdata.download_sector_data()              # 下载板块数据到本地
    # 更多数据的下载方式可以通过数据字典查询

# 读取本地历史行情数据
history_data = xtdata.get_market_data_ex([], code_list, period=period, count=-1)
print(history_data)
print("=" * 20)

# 如果需要盘中的实时行情，需要向服务器进行订阅后才能获取
# 订阅后，get_market_data函数于get_market_data_ex函数将会自动拼接本地历史行情与服务器实时行情

# 向服务器订阅数据
for i in code_list:
    xtdata.subscribe_quote(i, period=period, count=-1)  # 设置count = -1来取到当天所有实时行情

# 等待订阅完成
time.sleep(1)

# 获取订阅后的行情
kline_data = xtdata.get_market_data_ex([], code_list, period=period)
print(kline_data)

# 获取订阅后的行情，并以固定间隔进行刷新,预期会循环打印10次
for i in range(10):
    # 这边做演示，就用for来循环了，实际使用中可以用while True
    kline_data = xtdata.get_market_data_ex([], code_list, period=period)
    print(kline_data)
    time.sleep(3)  # 三秒后再次获取行情

# 如果不想用固定间隔触发，可以以用订阅后的回调来执行
# 这种模式下当订阅的callback回调函数将会异步的执行，每当订阅的标的tick发生变化更新，callback回调函数就会被调用一次
# 本地已有的数据不会触发callback

# 定义的回测函数
## 回调函数中，data是本次触发回调的数据，只有一条
def f(data):
    # print(data)

    code_list = list(data.keys())  # 获取到本次触发的标的代码

    kline_in_callabck = xtdata.get_market_data_ex([], code_list, period=period)  # 在回调中获取klines数据
    print(kline_in_callabck)

for i in code_list:
    xtdata.subscribe_quote(i, period=period, count=-1, callback=f)  # 订阅时设定回调函数

# 使用回调时，必须要同时使用xtdata.run()来阻塞程序，否则程序运行到最后一行就直接结束退出了。
xtdata.run()
```

---

### 1.2 下载历史数据 + 订阅最新行情

```python
# coding: utf-8
import time
from xtquant import xtdata

code = '600000.SH'

# 获取全推数据（最新快照）
full_tick = xtdata.get_full_tick([code])
print('全推数据 日线最新值', full_tick)

# 下载历史数据（接口本身不返回数据）
xtdata.download_history_data(code, period='1m', start_time='20230701')

# 订阅最新行情
def callback_func(data):
    print('回调触发', data)

xtdata.subscribe_quote(code, period='1m', count=-1, callback=callback_func)

# 一次性取历史数据
data = xtdata.get_market_data(['close'], [code], period='1m', start_time='20230701')
print('一次性取数据', data)

# 阻塞主线程
xtdata.run()
```

---

### 1.3 获取对手价

```python
# 以卖出为例：取买一价作为对手价
import pandas as pd
import numpy as np
from xtquant import xtdata

to_do_trade_list = ["000001.SZ"]
tick = xtdata.get_full_tick(to_do_trade_list)

# 取买一价为对手价，若买一价为0说明已跌停，则取最新价
for i in tick:
    fix_price = tick[i]["bidPrice"][0] if tick[i]["bidPrice"][0] != 0 else tick[i]["lastPrice"]
    print(fix_price)
# 输出示例：10.01
```

---

### 1.4 复权计算完整示例

```python
# coding: utf-8
import numpy as np
import pandas as pd
from xtquant import xtdata


def gen_divid_ratio(quote_datas, divid_datas):
    """生成复权系数序列"""
    drl = []
    dr = 1.0
    qi, qdl = 0, len(quote_datas)
    di, ddl = 0, len(divid_datas)
    while qi < qdl and di < ddl:
        qd = quote_datas.iloc[qi]
        dd = divid_datas.iloc[di]
        if qd.name >= dd.name:
            dr *= dd['dr']
            di += 1
        if qd.name <= dd.name:
            drl.append(dr)
            qi += 1
    while qi < qdl:
        drl.append(dr)
        qi += 1
    return pd.DataFrame(drl, index=quote_datas.index, columns=quote_datas.columns)


def process_forward_ratio(quote_datas, divid_datas):
    """等比前复权"""
    drl = gen_divid_ratio(quote_datas, divid_datas)
    drlf = drl / drl.iloc[-1]
    return (quote_datas * drlf).apply(lambda x: round(x, 2))


def process_backward_ratio(quote_datas, divid_datas):
    """等比后复权"""
    drl = gen_divid_ratio(quote_datas, divid_datas)
    return (quote_datas * drl).apply(lambda x: round(x, 2))


def process_forward(quote_datas1, divid_datas):
    """前复权"""
    quote_datas = quote_datas1.copy()
    def calc_front(v, d):
        return ((v - d['interest'] + d['allotPrice'] * d['allotNum'])
                / (1 + d['allotNum'] + d['stockBonus'] + d['stockGift']))
    for qi in range(len(quote_datas)):
        q = quote_datas.iloc[qi]
        for di in range(len(divid_datas)):
            d = divid_datas.iloc[di]
            if d.name <= q.name:
                continue
            q.iloc[0] = calc_front(q.iloc[0], d)
    return quote_datas


def process_backward(quote_datas1, divid_datas):
    """后复权"""
    quote_datas = quote_datas1.copy()
    def calc_back(v, d):
        return (v * (1.0 + d['stockGift'] + d['stockBonus'] + d['allotNum'])
                + d['interest'] - d['allotNum'] * d['allotPrice'])
    for qi in range(len(quote_datas)):
        q = quote_datas.iloc[qi]
        for di in range(len(divid_datas) - 1, -1, -1):
            d = divid_datas.iloc[di]
            if d.name > q.name:
                continue
            q.iloc[0] = calc_back(q.iloc[0], d)
    return quote_datas


s = '002594.SZ'
# xtdata.download_history_data(s, '1d', '20100101', '')

dd = xtdata.get_divid_factors(s)
print(dd)

field_list = ['open', 'high', 'low', 'close']
datas_ori = xtdata.get_market_data(field_list, [s], '1d', dividend_type='none')['close'].T

datas_forward_ratio = process_forward_ratio(datas_ori, dd)
print('等比前复权', datas_forward_ratio)

datas_backward_ratio = process_backward_ratio(datas_ori, dd)
print('等比后复权', datas_backward_ratio)

datas_forward = process_forward(datas_ori, dd)
print('前复权', datas_forward)

datas_backward = process_backward(datas_ori, dd)
print('后复权', datas_backward)
```

---

### 1.5 根据商品期权代码获取对应期货合约

```python
from xtquant import xtdata


def get_option_underline_code(code: str) -> str:
    """
    注意：该函数不适用于股指期货期权与ETF期权
    根据商品期权代码获取对应的具体商品期货合约代码
    """
    Exchange_dict = {
        "SHFE": "SF", "CZCE": "ZF", "DCE": "DF", "INE": "INE", "GFEX": "GF"
    }
    if code.split(".")[-1] not in [v for k, v in Exchange_dict.items()]:
        raise KeyError("此函数不支持该交易所合约")
    info = xtdata.get_option_detail_data(code)
    underline_code = info["OptUndlCode"] + "." + Exchange_dict[info["OptUndlMarket"]]
    return underline_code


if __name__ == "__main__":
    symbol_code = get_option_underline_code('sc2403C465.INE')
    print(symbol_code)
    # 输出：'sc2403.INE'
```

---

### 1.6 根据指数代码获取对应的期货合约列表

```python
from xtquant import xtdata
import re


def get_financial_futures_code_from_index(index_code: str) -> list:
    """
    传入指数代码，返回对应的期货合约列表（当前）
    index_code: 如 "000300.SH", "000905.SH"
    """
    financial_futures = xtdata.get_stock_list_in_sector("中金所")
    future_list = []
    pattern = r'^[a-zA-Z]{1,2}\d{3,4}\.[A-Z]{2}$'
    for i in financial_futures:
        if re.match(pattern, i):
            future_list.append(i)

    ls = []
    for i in future_list:
        _info = xtdata._get_instrument_detail(i)
        _index_code = (_info["ExtendInfo"]['OptUndlCode'] + "."
                       + _info["ExtendInfo"]['OptUndlMarket'])
        if _index_code == index_code:
            ls.append(i)
    return ls


if __name__ == "__main__":
    ls = get_financial_futures_code_from_index("000905.SH")
    print(ls)
    # 输出示例：['IC2402.IF', 'IC2403.IF', 'IC2406.IF', 'IC2409.IF']
```

---

### 1.7 连接VIP服务器（xtdatacenter）

```python
import sys
import time
import pandas as pd
from xtquant import xtdatacenter as xtdc
from xtquant import xtdata

# 设置token（从投研用户中心获取：https://xuntou.net/#/userInfo）
xtdc.set_token('这里输入token')

# 设置连接池，使服务器只在连接池内优选，建议将VIP服务器设为连接池
addr_list = [
    '115.231.218.73:55310',
    '115.231.218.79:55310',
    '42.228.16.211:55300',
    '42.228.16.210:55300',
    '36.99.48.20:55300',
    '36.99.48.21:55300',
]
xtdc.set_allow_optmize_address(addr_list)
xtdc.set_kline_mirror_enabled(True)  # 开启K线全推功能(VIP)，获取全市场实时K线数据

# 初始化
xtdc.init()

# 监听端口
port = xtdc.listen(port=58621)  # 指定固定端口
# 通过指定范围自动寻找可用端口：
# port = xtdc.listen(port=(58620, 58630))[1]

xtdata.connect(port=port)
print('-----连接上了------')
print(xtdata.data_dir)

servers = xtdata.get_quote_server_status()
for k, v in servers.items():
    print(k, v)

xtdata.run()
```

---

### 1.8 连接指定服务器

```python
import time
from xtquant import xtdata

# 用token方式连接，不需要账号密码
info = {"ip": '115.231.218.73', "port": 55300, "username": '', "pwd": ''}

connect_success = 0

def func(d):
    ip = d.get('ip', '')
    port = d.get('port')
    status = d.get('status', 'disconnected')
    global connect_success
    if ip == info['ip'] and port == info['port']:
        if status == 'connected':
            connect_success = 1
        else:
            connect_success = 2

# 注册连接回调
xtdata.watch_quote_server_status(func)

# 行情连接
qs = xtdata.QuoteServer(info)
qs.connect()

# 获取当前数据连接站点
data_server_info = xtdata.get_quote_server_status()
for k, v in data_server_info.items():
    print(f"data:{k}, connect info:{v.info}")

# 等待连接状态
while connect_success == 0:
    time.sleep(0.3)

if connect_success == 2:
    print("连接失败")
```

---

### 1.9 指定初始化行情连接范围

```python
from xtquant import xtdatacenter as xtdc

# 设置数据目录
xtdc.set_data_home_dir('data')

# 设置token
token = "你的token"
xtdc.set_token(token)

# 限定行情站点的优选范围
opt_list = [
    '115.231.218.73:55310',
    '115.231.218.79:55310',
    '42.228.16.210:55300',
    '42.228.16.211:55300',
    '36.99.48.20:55300',
    '36.99.48.21:55300',
]
xtdc.set_allow_optmize_address(opt_list)

# 开启指定市场的K线全推
xtdc.set_kline_mirror_markets(['SH', 'SZ', 'BJ'])

# 设置要初始化的市场列表
init_markets = [
    'SH', 'SZ', 'BJ',
    # 'DF', 'GF', 'IF', 'SF', 'ZF', 'INE',  # 期货市场
    # 'SHO', 'SZO',                           # 期权市场
]
xtdc.set_init_markets(init_markets)

# 初始化xtdc模块
xtdc.init(start_local_service=False)

# 监听端口
listen_port = xtdc.listen(port=(58620, 58650))

import xtquant.xtdata as xtdata
xtdata.connect(port=listen_port)

import code; code.interact(local=locals())
```

---

### 1.10 高频因子数据共享（上传）

```python
# coding: utf-8
import xtquant.invadv as xtia

remote_host = '115.231.218.7'
remote_port = 55300
user_name = '授权账号'
password = '授权账号对应密码'

api = xtia.InvAdv()
api.set_remote_addr(remote_host, remote_port)
api.set_user(user_name, password)
api.connect()

# 可用板块列表
ret_sector_dict = api.get_block_list()
print(f'支持的板块列表:{ret_sector_dict}')

new_dict = {v: k for k, v in ret_sector_dict.items()}

# 创建新板块（如不存在）
fp_name = '测试投顾C'
if fp_name not in new_dict:
    api.create_block(fp_name)
    print(f'创建{fp_name}表')

# 板块对应代码及权重
codes = {
    '002594.SZ': 0.009, '300750.SZ': 0.007,
    '688001.SH': 0.1, '000001.SZ': 0.2, '300751.SZ': 0.3
}

ret_sector_dict = api.get_block_list()
for k_msg_id, v in ret_sector_dict.items():
    write_codes = []
    if v == fp_name:
        for code, value in codes.items():
            write_codes.append(f'{code}|{value}')
        api.push_block(k_msg_id, write_codes)
        print(f'表:{fp_name} id:{k_msg_id} {write_codes}')
        print(f'上传结束!')

print('====end====')
```

---

### 1.11 高频因子数据共享（拉取/获取）

```python
# coding: utf-8
import time
import json
import os
import xtquant.invadv as xtia

api = xtia.InvAdv()
api.set_remote_addr('115.231.218.7', 55300)
api.set_user('授权账号', '授权账号对应密码')
api.connect()

ret_sector_dict = api.get_block_list()
print(ret_sector_dict)

while 1:
    if 1:
        ret_sector_dict = api.get_block_list()
        for mid, v in ret_sector_dict.items():
            sector_gt_list = []
            if v not in ['CESHI2', '新建共享板块3', 'B投顾', '测试投顾信号', '测试投顾信号A']:
                try:
                    if api.check_outdated(mid):
                        basket_list = api.pull_block(mid)
                        if basket_list == []:
                            continue
                        print(f'板块:{v} 有新的更新, 更新板块!')
                        for s in basket_list:
                            sector_gt_list.append(s[:9])
                        print(f'更新板块:{v} 长度:{len(basket_list)} 内容:{sector_gt_list}')
                except:
                    pass
    time.sleep(5)
```

---

## 二、交易模块快速入门

> **来源说明**：本节"基础交易框架"示例来自原始`xtquant_xttrade_交易模块`文档的"快速入门-创建策略"章节，并非`完整实例`文档中的内容。

### 2.1 基础交易框架

```python
# coding: utf-8
from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
from xtquant.xttype import StockAccount
from xtquant import xtconstant


class MyXtQuantTraderCallback(XtQuantTraderCallback):
    def on_disconnected(self):
        """连接断开"""
        print("connection lost")

    def on_stock_order(self, order):
        """委托回报推送"""
        print("on order callback:")
        print(order.stock_code, order.order_status, order.order_sysid)

    def on_stock_trade(self, trade):
        """成交变动推送"""
        print("on trade callback")
        print(trade.account_id, trade.stock_code, trade.order_id)

    def on_order_error(self, order_error):
        """委托失败推送"""
        print("on order_error callback")
        print(order_error.order_id, order_error.error_id, order_error.error_msg)

    def on_cancel_error(self, cancel_error):
        """撤单失败推送"""
        print("on cancel_error callback")
        print(cancel_error.order_id, cancel_error.error_id, cancel_error.error_msg)

    def on_order_stock_async_response(self, response):
        """异步下单回报推送"""
        print("on_order_stock_async_response")
        print(response.account_id, response.order_id, response.seq)

    def on_account_status(self, status):
        """账号状态推送"""
        print("on_account_status")
        print(status.account_id, status.account_type, status.status)


if __name__ == "__main__":
    print("demo test")
    # path 为 mini qmt 客户端安装目录下 userdata_mini 路径
    path = 'D:\\迅投极速交易终端 睿智融科版\\userdata_mini'
    # session_id 为会话编号，不同的 Python 策略需要使用不同的会话编号
    session_id = 123456
    xt_trader = XtQuantTrader(path, session_id)

    # 创建资金账号对象
    acc = StockAccount('1000000365')
    # StockAccount 可以用第二个参数指定账号类型
    # acc = StockAccount('1000000365', 'STOCK')

    callback = MyXtQuantTraderCallback()
    xt_trader.register_callback(callback)
    xt_trader.start()

    connect_result = xt_trader.connect()
    print(connect_result)  # 0 表示连接成功

    subscribe_result = xt_trader.subscribe(acc)
    print(subscribe_result)  # 0 表示订阅成功

    stock_code = '600000.SH'

    # 使用指定价下单，返回订单编号
    print("order using the fix price:")
    fix_result_order_id = xt_trader.order_stock(
        acc, stock_code, xtconstant.STOCK_BUY, 200, xtconstant.FIX_PRICE, 10.5,
        'strategy_name', 'remark')
    print(fix_result_order_id)

    # 使用订单编号撤单
    print("cancel order:")
    cancel_order_result = xt_trader.cancel_order_stock(acc, fix_result_order_id)
    print(cancel_order_result)

    # 使用异步下单接口，返回下单请求序号 seq
    print("order using async api:")
    async_seq = xt_trader.order_stock_async(
        acc, stock_code, xtconstant.STOCK_BUY, 200, xtconstant.FIX_PRICE, 10.5,
        'strategy_name', 'remark')
    print(async_seq)

    # 查询证券资产
    print("query asset:")
    asset = xt_trader.query_stock_asset(acc)
    if asset:
        print("cash:", asset.cash)

    # 根据订单编号查询委托
    print("query order:")
    order = xt_trader.query_stock_order(acc, fix_result_order_id)
    if order:
        print("order_id:", order.order_id)

    # 查询当日所有委托
    orders = xt_trader.query_stock_orders(acc)
    print("orders:", len(orders))
    if orders:
        print("{0} {1} {2}".format(
            orders[-1].stock_code, orders[-1].order_volume, orders[-1].price))

    # 查询当日所有成交
    trades = xt_trader.query_stock_trades(acc)
    print("trades:", len(trades))
    if trades:
        print("{0} {1} {2}".format(
            trades[-1].stock_code, trades[-1].traded_volume, trades[-1].traded_price))

    # 查询当日所有持仓
    positions = xt_trader.query_stock_positions(acc)
    print("positions:", len(positions))
    if positions:
        print("{0} {1} {2}".format(
            positions[-1].account_id, positions[-1].stock_code, positions[-1].volume))

    # 根据股票代码查询对应持仓
    position = xt_trader.query_stock_position(acc, stock_code)
    if position:
        print("{0} {1} {2}".format(
            position.account_id, position.stock_code, position.volume))

    # 阻塞线程，接收交易推送
    xt_trader.run_forever()
```

---

## 三、实盘策略示例

### 3.1 单股查询+下单实盘示例（原文 Example 15）

说明：
- `path` 变量需改为本地客户端路径，券商端指定到 `userdata_mini` 文件夹
- 注意：如果是连接投研端进行交易，文件目录需要指定到 `f"{安装目录}\userdata"`
- 资金账号需调整为自身资金账号
- 原文使用 `m_dCash`、`m_nVolume`、`m_nCanUseVolume` 等旧式属性名访问账户对象（这些属性在 xtquant 实际对象中确实存在）

```python
# coding:utf-8
import time, datetime, traceback, sys
from xtquant import xtdata
from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
from xtquant.xttype import StockAccount
from xtquant import xtconstant


# 定义一个类 创建类的实例 作为状态的容器
class _a():
    pass


A = _a()
A.bought_list = []
A.hsa = xtdata.get_stock_list_in_sector('沪深A股')


def interact():
    """执行后进入repl模式"""
    import code
    code.InteractiveConsole(locals=globals()).interact()


xtdata.download_sector_data()


class MyXtQuantTraderCallback(XtQuantTraderCallback):
    def on_disconnected(self):
        """连接断开"""
        print(datetime.datetime.now(), '连接断开回调')

    def on_stock_order(self, order):
        """委托回报推送"""
        print(datetime.datetime.now(), '委托回调 投资备注', order.order_remark)

    def on_stock_trade(self, trade):
        """成交变动推送"""
        print(datetime.datetime.now(), '成交回调', trade.order_remark,
              f"委托方向(48买 49卖) {trade.offset_flag} 成交价格 {trade.traded_price} 成交数量 {trade.traded_volume}")

    def on_order_error(self, order_error):
        """委托失败推送"""
        # print("on order_error callback")
        # print(order_error.order_id, order_error.error_id, order_error.error_msg)
        print(f"委托报错回调 {order_error.order_remark} {order_error.error_msg}")

    def on_cancel_error(self, cancel_error):
        """撤单失败推送"""
        print(datetime.datetime.now(), sys._getframe().f_code.co_name)

    def on_order_stock_async_response(self, response):
        """异步下单回报推送"""
        print(f"异步委托回调 投资备注: {response.order_remark}")

    def on_cancel_order_stock_async_response(self, response):
        print(datetime.datetime.now(), sys._getframe().f_code.co_name)

    def on_account_status(self, status):
        print(datetime.datetime.now(), sys._getframe().f_code.co_name)


if __name__ == '__main__':
    print("start")
    # 指定客户端所在路径, 券商端指定到 userdata_mini文件夹
    # 注意：如果是连接投研端进行交易，文件目录需要指定到f"{安装目录}\userdata"
    path = r'D:\qmt\投研\迅投极速交易终端睿智融科版\userdata'
    # 生成session id 整数类型 同时运行的策略不能重复
    session_id = int(time.time())
    xt_trader = XtQuantTrader(path, session_id)
    # 开启主动请求接口的专用线程 开启后在on_stock_xxx回调函数里调用XtQuantTrader.query_xxx函数不会卡住回调线程，但是查询和推送的数据在时序上会变得不确定
    # 详见: http://docs.thinktrader.net/vip/pages/ee0e9b/#开启主动请求接口的专用线程
    # xt_trader.set_relaxed_response_order_enabled(True)

    # 创建资金账号为 800068 的证券账号对象 股票账号为STOCK 信用CREDIT 期货FUTURE
    acc = StockAccount('2000128', 'STOCK')
    # 创建交易回调类对象，并声明接收回调
    callback = MyXtQuantTraderCallback()
    xt_trader.register_callback(callback)
    # 启动交易线程
    xt_trader.start()
    # 建立交易连接，返回0表示连接成功
    connect_result = xt_trader.connect()
    print('建立交易连接，返回0表示连接成功', connect_result)
    # 对交易回调进行订阅，订阅后可以收到交易主推，返回0表示订阅成功
    subscribe_result = xt_trader.subscribe(acc)
    print('对交易回调进行订阅，订阅后可以收到交易主推，返回0表示订阅成功', subscribe_result)

    # 取账号信息
    account_info = xt_trader.query_stock_asset(acc)
    # 取可用资金
    available_cash = account_info.m_dCash

    print(acc.account_id, '可用资金', available_cash)
    # 查账号持仓
    positions = xt_trader.query_stock_positions(acc)
    # 取各品种 总持仓 可用持仓
    position_total_dict = {i.stock_code: i.m_nVolume for i in positions}
    position_available_dict = {i.stock_code: i.m_nCanUseVolume for i in positions}
    print(acc.account_id, '持仓字典', position_total_dict)
    print(acc.account_id, '可用持仓字典', position_available_dict)

    # 买入 浦发银行 最新价 两万元
    stock = '600000.SH'
    target_amount = 20000
    full_tick = xtdata.get_full_tick([stock])
    print(f"{stock} 全推行情： {full_tick}")
    current_price = full_tick[stock]['lastPrice']
    # 买入金额 取目标金额 与 可用金额中较小的
    buy_amount = min(target_amount, available_cash)
    # 买入数量 取整为100的整数倍
    buy_vol = int(buy_amount / current_price / 100) * 100
    print(f"当前可用资金 {available_cash} 目标买入金额 {target_amount} 买入股数 {buy_vol}股")
    async_seq = xt_trader.order_stock_async(acc, stock, xtconstant.STOCK_BUY, buy_vol, xtconstant.FIX_PRICE, current_price,
                                            'strategy_name', stock)

    # 卖出 500股
    stock = '513130.SH'
    # 目标数量
    target_vol = 500
    # 可用数量
    available_vol = position_available_dict[stock] if stock in position_available_dict else 0
    # 卖出量取目标量与可用量中较小的
    sell_vol = min(target_vol, available_vol)
    print(f"{stock} 目标卖出量 {target_vol} 可用数量 {available_vol} 卖出 {sell_vol}股")
    if sell_vol > 0:
        async_seq = xt_trader.order_stock_async(acc, stock, xtconstant.STOCK_SELL, sell_vol, xtconstant.LATEST_PRICE,
                                                -1,
                                                'strategy_name', stock)
    print(f"下单完成 等待回调")
    # 阻塞主线程退出
    xt_trader.run_forever()
    # 如果使用vscode pycharm等本地编辑器 可以进入交互模式 方便调试 （把上一行的run_forever注释掉 否则不会执行到这里）
    interact()
```

---

### 3.1bis 单股订阅 callback 驱动下单（原文 Example 16）

本示例订阅单股 1d 行情，回调中判断涨幅 > 9% 时买入 100 股。

```python
# coding:utf-8
import time, datetime, traceback, sys
from xtquant import xtdata
from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
from xtquant.xttype import StockAccount
from xtquant import xtconstant


# 定义一个类 创建类的实例 作为状态的容器
class _a():
    pass


A = _a()
A.bought_list = []
A.hsa = xtdata.get_stock_list_in_sector('沪深A股')


def interact():
    """执行后进入repl模式"""
    import code
    code.InteractiveConsole(locals=globals()).interact()


xtdata.download_sector_data()


def f(data):
    print(data)
    now = datetime.datetime.now()
    for stock in data:
        if stock not in A.hsa:
            continue
        cuurent_price = data[stock][0]['close']
        pre_price = data[stock][0]['preClose']
        ratio = cuurent_price / pre_price - 1 if pre_price > 0 else 0
        if ratio > 0.09 and stock not in A.bought_list:
            print(f"{now} 最新价 买入 {stock} 100股")
            async_seq = xt_trader.order_stock_async(acc, stock, xtconstant.STOCK_BUY, 100, xtconstant.LATEST_PRICE, -1,
                                                    'strategy_name', stock)
            A.bought_list.append(stock)


class MyXtQuantTraderCallback(XtQuantTraderCallback):
    def on_disconnected(self):
        print(datetime.datetime.now(), '连接断开回调')

    def on_stock_order(self, order):
        print(datetime.datetime.now(), '委托回调', order.order_remark)

    def on_stock_trade(self, trade):
        print(datetime.datetime.now(), '成交回调', trade.order_remark)

    def on_order_error(self, order_error):
        # print("on order_error callback")
        # print(order_error.order_id, order_error.error_id, order_error.error_msg)
        print(f"委托报错回调 {order_error.order_remark} {order_error.error_msg}")

    def on_cancel_error(self, cancel_error):
        print(datetime.datetime.now(), sys._getframe().f_code.co_name)

    def on_order_stock_async_response(self, response):
        print(f"异步委托回调 {response.order_remark}")

    def on_cancel_order_stock_async_response(self, response):
        print(datetime.datetime.now(), sys._getframe().f_code.co_name)

    def on_account_status(self, status):
        print(datetime.datetime.now(), sys._getframe().f_code.co_name)


if __name__ == '__main__':
    print("start")
    # 指定客户端所在路径, 券商端指定到 userdata_mini文件夹
    # 注意：如果是连接投研端进行交易，文件目录需要指定到f"{安装目录}\userdata"
    path = r'D:\qmt\投研\迅投极速交易终端睿智融科版\userdata'
    session_id = int(time.time())
    xt_trader = XtQuantTrader(path, session_id)
    # xt_trader.set_relaxed_response_order_enabled(True)

    # 创建资金账号为 800068 的证券账号对象 股票账号为STOCK 信用CREDIT 期货FUTURE
    acc = StockAccount('2000128', 'STOCK')
    callback = MyXtQuantTraderCallback()
    xt_trader.register_callback(callback)
    xt_trader.start()

    connect_result = xt_trader.connect()
    print('建立交易连接，返回0表示连接成功', connect_result)
    subscribe_result = xt_trader.subscribe(acc)
    print('对交易回调进行订阅，订阅后可以收到交易主推，返回0表示订阅成功', subscribe_result)

    # 订阅的品种列表
    code_list = ['600000.SH', '000001.SZ']

    for code in code_list:
        xtdata.subscribe_quote(code, '1d', callback=f)

    xt_trader.run_forever()
    interact()
```

---

### 3.2 全推订阅实盘示例

本示例订阅沪深全市场推送，对涨幅超 9% 的沪深A股品种买入 200 股。

**注意**：本策略仅作写法参考，直接实盘使用造成损失本网站不负担责任。

```python
# coding: utf-8
import time, datetime, sys
from xtquant import xtdata
from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
from xtquant.xttype import StockAccount
from xtquant import xtconstant


class _state():
    pass

A = _state()
A.bought_list = []
A.hsa = xtdata.get_stock_list_in_sector('沪深A股')


def interact():
    import code
    code.InteractiveConsole(locals=globals()).interact()


xtdata.download_sector_data()


def f(data):
    now = datetime.datetime.now()
    for stock in data:
        if stock not in A.hsa:
            continue
        cuurent_price = data[stock][0]['lastPrice']
        pre_price = data[stock][0]['lastClose']
        ratio = cuurent_price / pre_price - 1 if pre_price > 0 else 0
        if ratio > 0.09 and stock not in A.bought_list:
            print(f"{now} 最新价 买入 {stock} 200股")
            async_seq = xt_trader.order_stock_async(
                acc, stock, xtconstant.STOCK_BUY, 200, xtconstant.LATEST_PRICE, -1,
                'strategy_name', stock)
            A.bought_list.append(stock)


class MyXtQuantTraderCallback(XtQuantTraderCallback):
    def on_disconnected(self):
        print(datetime.datetime.now(), '连接断开回调')

    def on_stock_order(self, order):
        print(datetime.datetime.now(), '委托回调', order.order_remark)

    def on_stock_trade(self, trade):
        print(datetime.datetime.now(), '成交回调', trade.order_remark)

    def on_order_error(self, order_error):
        print(f"委托报错回调 {order_error.order_remark} {order_error.error_msg}")

    def on_cancel_error(self, cancel_error):
        print(datetime.datetime.now(), sys._getframe().f_code.co_name)

    def on_order_stock_async_response(self, response):
        print(f"异步委托回调 {response.order_remark}")

    def on_cancel_order_stock_async_response(self, response):
        print(datetime.datetime.now(), sys._getframe().f_code.co_name)

    def on_account_status(self, status):
        print(datetime.datetime.now(), sys._getframe().f_code.co_name)


if __name__ == '__main__':
    print("start")
    path = r'D:\qmt\sp3\迅投极速交易终端 睿智融科版\userdata_mini'
    session_id = int(time.time())
    xt_trader = XtQuantTrader(path, session_id)
    acc = StockAccount('800068', 'STOCK')

    callback = MyXtQuantTraderCallback()
    xt_trader.register_callback(callback)
    xt_trader.start()

    connect_result = xt_trader.connect()
    print('建立交易连接，返回0表示连接成功', connect_result)

    subscribe_result = xt_trader.subscribe(acc)
    print('对交易回调进行订阅，返回0表示订阅成功', subscribe_result)

    # 注册全推回调函数，安全起见处于注释状态，确认理解效果后再放开
    # xtdata.subscribe_whole_quote(["SH", "SZ"], callback=f)

    xt_trader.run_forever()
    interact()
```

---

### 3.3 定时判断实盘示例

```python
# coding: utf-8
import time, datetime, sys
from xtquant import xtdata
from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
from xtquant.xttype import StockAccount
from xtquant import xtconstant


class _state():
    pass

A = _state()
A.bought_list = []
A.hsa = xtdata.get_stock_list_in_sector('沪深A股')


def interact():
    import code
    code.InteractiveConsole(locals=globals()).interact()


xtdata.download_sector_data()


def f(data):
    now = datetime.datetime.now()
    for stock in data:
        if stock not in A.hsa:
            continue
        cuurent_price = data[stock].iloc[-1, 0]
        pre_price = data[stock].iloc[-2, 0]
        ratio = cuurent_price / pre_price - 1 if pre_price > 0 else 0
        if ratio > 0.09 and stock not in A.bought_list:
            print(f"{now} 最新价 买入 {stock} 100股")
            async_seq = xt_trader.order_stock_async(
                acc, stock, xtconstant.STOCK_BUY, 100, xtconstant.LATEST_PRICE, -1,
                'strategy_name', stock)
            A.bought_list.append(stock)


class MyXtQuantTraderCallback(XtQuantTraderCallback):
    def on_disconnected(self):
        print(datetime.datetime.now(), '连接断开回调')

    def on_stock_order(self, order):
        print(datetime.datetime.now(), '委托回调', order.order_remark)

    def on_stock_trade(self, trade):
        print(datetime.datetime.now(), '成交回调', trade.order_remark)

    def on_order_error(self, order_error):
        print(f"委托报错回调 {order_error.order_remark} {order_error.error_msg}")

    def on_cancel_error(self, cancel_error):
        print(datetime.datetime.now(), sys._getframe().f_code.co_name)

    def on_order_stock_async_response(self, response):
        print(f"异步委托回调 {response.order_remark}")

    def on_cancel_order_stock_async_response(self, response):
        print(datetime.datetime.now(), sys._getframe().f_code.co_name)

    def on_account_status(self, status):
        print(datetime.datetime.now(), sys._getframe().f_code.co_name)


if __name__ == '__main__':
    print("start")
    path = r'D:\qmt\投研\迅投极速交易终端睿智融科版\userdata'
    session_id = int(time.time())
    xt_trader = XtQuantTrader(path, session_id)
    acc = StockAccount('2000128', 'STOCK')

    callback = MyXtQuantTraderCallback()
    xt_trader.register_callback(callback)
    xt_trader.start()

    connect_result = xt_trader.connect()
    print('建立交易连接，返回0表示连接成功', connect_result)

    subscribe_result = xt_trader.subscribe(acc)
    print('对交易回调进行订阅，返回0表示订阅成功', subscribe_result)

    code_list = ['600000.SH', '000001.SZ']
    for code in code_list:
        xtdata.download_history_data(code, period='1d', start_time='20200101')
        xtdata.subscribe_quote(code, '1d', callback=None)

    while True:
        now = datetime.datetime.now()
        now_time = now.strftime('%H%M%S')
        if not '093000' <= now_time < '150000':
            print(f"{now} 非交易时间 循环退出")
            break
        # 取K线数据
        data = xtdata.get_market_data_ex(['close'], code_list, period='1d', start_time='20240101')
        # 判断交易
        f(data)
        time.sleep(3)

    xt_trader.run_forever()
    interact()
```

---

### 3.4 下单后通过回调撤单示例

```python
# coding: utf-8
import datetime, time
from xtquant import xtdata, xttrader
from xtquant.xttype import StockAccount
from xtquant import xtconstant
from xtquant.xttrader import XtQuantTraderCallback

"""
异步下单委托流程：
1. order_stock_async 发出委托
2. on_order_stock_async_response 回调收到委托反馈
3. on_stock_order 回调收到委托信息
4. cancel_order_stock_sysid_async 发出异步撤单指令
5. on_cancel_order_stock_async_response 收到撤单回调信息
6. on_stock_order 再次收到委托状态更新
"""

strategy_name = "委托撤单测试"


class MyXtQuantTraderCallback(XtQuantTraderCallback):
    def on_stock_order(self, order):
        """委托回报推送"""
        print(f"""
        委托信息：
        账号类型: {order.account_type}, 资金账号: {order.account_id},
        证券代码: {order.stock_code}, 订单编号: {order.order_id},
        柜台合同编号: {order.order_sysid}, 报单时间: {order.order_time},
        委托类型: {order.order_type}, 委托数量: {order.order_volume},
        报价类型: {order.price_type}, 委托价格: {order.price},
        成交数量: {order.traded_volume}, 成交均价: {order.traded_price},
        委托状态: {order.order_status}, 状态描述: {order.status_msg},
        策略名称: {order.strategy_name}, 委托备注: {order.order_remark},
        多空方向: {order.direction}, 交易操作: {order.offset_flag}
        """)

        if order.strategy_name == strategy_name:
            ssid = order.order_sysid
            status = order.order_status
            # 已报(50)或部成(55)时发起撤单
            if ssid and status in [50, 55]:
                # 投研端 market 参数填0，券商端按实际填写
                print(xt_trade.cancel_order_stock_sysid_async(account, 0, ssid))

    def on_stock_trade(self, trade):
        print(datetime.datetime.now(), '成交回调',
              trade.order_remark, trade.stock_code,
              trade.traded_volume, trade.offset_flag)

    def on_order_stock_async_response(self, response):
        print(datetime.datetime.now(), '异步下单编号为：', response.seq)

    def on_cancel_order_stock_async_response(self, response):
        print(f"""
        异步撤单回调信息：
        账号类型: {response.account_type}, 资金账号: {response.account_id},
        订单编号: {response.order_id}, 柜台委托编号: {response.order_sysid},
        撤单结果: {response.cancel_result}, 请求序号: {response.seq}
        """)


callback = MyXtQuantTraderCallback()
# 填投研端的期货账号
account = StockAccount("1000024", account_type="FUTURE")
# 填写投研端的股票账号：account = StockAccount("2000567")

# 填投研端 userdata 路径，miniqmt 指定到 userdata_mini
xt_trade = xttrader.XtQuantTrader(
    r"C:\Program Files\测试1\迅投极速交易终端睿智融科版\userdata",
    int(time.time())
)
xt_trade.register_callback(callback)
xt_trade.start()
connect_result = xt_trade.connect()
subscribe_result = xt_trade.subscribe(account)
print(subscribe_result)

code = "rb2410.SF"
tick = xtdata.get_full_tick([code])[code]
last_price = tick["lastPrice"]      # 最新价
ask_price = round(tick["askPrice"][0], 3)  # 卖方1档价
bid_price = round(tick["bidPrice"][4], 3)  # 买方5档价

symbol_info = xtdata.get_instrument_detail(code)
up_limit = symbol_info["UpStopPrice"]
down_limit = symbol_info["DownStopPrice"]

lots = 1
res_id = xt_trade.order_stock_async(
    account, code, xtconstant.FUTURE_OPEN_LONG, lots,
    xtconstant.FIX_PRICE, down_limit, strategy_name, "跌停价/固定手数")

xtdata.run()
```

---

### 3.5 断线重连示例（均线策略）

```python
# 本文演示交易接口断开时如何重连
# 注意：不要使用 while True 无限循环尝试，每次连接都会用 session_id 创建对接文件，会占满硬盘
# 要控制 session_id 在有限范围内尝试

import time
from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
from xtquant.xttype import StockAccount
from xtquant import xtconstant
from xtquant import xtdata


class MyXtQuantTraderCallback(XtQuantTraderCallback):
    def on_disconnected(self):
        print("connection lost, 交易接口断开，即将重连")
        global xt_trader
        xt_trader = None

    def on_stock_order(self, order):
        print(f'委托回报: 股票:{order.stock_code} 账号:{order.account_id} '
              f'订单:{order.order_id} 柜台:{order.order_sysid} '
              f'状态:{order.order_status} 已成:{order.traded_volume} 委托:{order.order_volume}')

    def on_stock_trade(self, trade):
        print(f'成交回报: 股票:{trade.stock_code} 账号:{trade.account_id} '
              f'订单:{trade.order_id} 成交编号:{trade.traded_id} '
              f'成交量:{trade.traded_volume}')

    def on_order_error(self, order_error):
        print(f"报单失败：订单:{order_error.order_id} 信息:{order_error.error_msg} 备注:{order_error.order_remark}")

    def on_cancel_error(self, cancel_error):
        print(f"撤单失败：订单:{cancel_error.order_id} 信息:{cancel_error.error_msg} 市场:{cancel_error.market}")

    def on_order_stock_async_response(self, response):
        print(f"异步下单请求序号:{response.seq}, 订单编号：{response.order_id}")

    def on_account_status(self, status):
        print(f"账号状态变化，账号:{status.account_id} 最新状态：{status.status}")


def create_trader(xt_acc, path, session_id):
    trader = XtQuantTrader(path, session_id, callback=MyXtQuantTraderCallback())
    trader.start()
    connect_result = trader.connect()
    trader.subscribe(xt_acc)
    return trader if connect_result == 0 else None


def try_connect(xt_acc, path):
    session_id_range = [i for i in range(100, 120)]
    import random
    random.shuffle(session_id_range)
    for session_id in session_id_range:
        trader = create_trader(xt_acc, path, session_id)
        if trader:
            print(f'连接成功，session_id:{session_id}')
            return trader
        else:
            print(f'连接失败，session_id:{session_id}，继续尝试')
    print('所有id都尝试后仍失败，放弃连接')
    return None


def get_xttrader(xt_acc, path):
    global xt_trader
    if xt_trader is None:
        xt_trader = try_connect(xt_acc, path)
    return xt_trader


if __name__ == "__main__":
    path = r'E:\qmt\userdata_mini'
    xt_trader = None
    xt_acc = StockAccount('2000204')
    xt_trader = get_xttrader(xt_acc, path)
    if not xt_trader:
        raise Exception('交易接口连接失败')
    print('交易接口连接成功，策略开始')

    stock = '513050.SH'
    xtdata.subscribe_quote(stock, '5m', '', '', count=-1)
    time.sleep(1)
    order_record = []

    while '093000' <= time.strftime('%H%M%S') < '150000':
        time.sleep(3)
        xt_trader = get_xttrader(xt_acc, path)

        price = xtdata.get_market_data_ex(['close'], [stock], period='5m')[stock]
        ma5 = price['close'].rolling(5).mean()
        ma10 = price['close'].rolling(10).mean()

        if ma5.iloc[-1] > ma5.iloc[-10]:
            t = price.index[-1]
            order_flag = (t, '买')
            if order_flag not in order_record:
                print(f'发起买入 {stock} k线时间:{t}')
                xt_trader.order_stock_async(
                    xt_acc, stock, xtconstant.STOCK_BUY, 100, xtconstant.LATEST_PRICE, 0)
                order_record.append(order_flag)
        elif ma5.iloc[-1] < ma5[-10]:
            t = price.index[-1]
            order_flag = (t, '卖')
            if order_flag not in order_record:
                print(f'发起卖出 {stock} k线时间:{t}')
                xt_trader.order_stock_async(
                    xt_acc, stock, xtconstant.STOCK_SELL, 100, xtconstant.LATEST_PRICE, 0)
                order_record.append(order_flag)
```

---

### 3.6 指定session范围重试连接

```python
# coding: utf-8


def connect(path, session):
    from xtquant import xttrader
    trader = xttrader.XtQuantTrader(path, session)
    trader.start()
    connect_result = trader.connect()
    return trader if connect_result == 0 else None


def try_connect_range():
    # 100以内的id保留，不使用
    ids = [i for i in range(100, 200)]
    import random
    random.shuffle(ids)
    path = r'userdata_mini'
    for session_id in ids:
        print(f'尝试id:{session_id}')
        trader = connect(path, session_id)
        if trader:
            print('连接成功')
            return trader
        else:
            print('连接失败，继续尝试下一个id')
    raise Exception('XtQuantTrader 连接失败，请重试')


try:
    trader = try_connect_range()
except Exception as e:
    import traceback
    print(e, traceback.format_exc())

import time
while True:
    print('.', end='')
    time.sleep(2)
```

---

### 3.7 信用账号还款示例

```python
# coding: utf-8
from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
from xtquant.xttype import StockAccount
from xtquant import xtconstant

path = 'E:\\qmt\\userdata_mini'
session_id = 1234567
repay_money = 1000.51  # 元，需要执行还款的金额


class MyXtQuantTraderCallback(XtQuantTraderCallback):
    def on_disconnected(self):
        print("connection lost")

    def on_stock_order(self, order):
        print("on order callback:")
        print(order.stock_code, order.order_status, order.order_sysid)

    def on_stock_asset(self, asset):
        """资金变动推送"""
        print("on asset callback")
        print(asset.account_id, asset.cash, asset.total_asset)

    def on_stock_trade(self, trade):
        print("on trade callback")
        print(trade.account_id, trade.stock_code, trade.order_id)

    def on_order_error(self, order_error):
        print("on order_error callback")
        print(order_error.order_id, order_error.error_id, order_error.error_msg)

    def on_cancel_error(self, cancel_error):
        print("on cancel_error callback")
        print(cancel_error.order_id, cancel_error.error_id, cancel_error.error_msg)

    def on_order_stock_async_response(self, response):
        print("on_order_stock_async_response")
        print(response.account_id, response.order_id, response.seq)

    def on_account_status(self, status):
        print("on_account_status")
        print(status.account_id, status.account_type, status.status)


if __name__ == "__main__":
    print("demo test")
    xt_trader = XtQuantTrader(path, session_id)

    acc = StockAccount('200035', 'CREDIT')
    callback = MyXtQuantTraderCallback()
    xt_trader.register_callback(callback)
    xt_trader.start()

    connect_result = xt_trader.connect()
    if connect_result != 0:
        import sys
        sys.exit('连接失败，程序即将退出 %d' % connect_result)

    subscribe_result = xt_trader.subscribe(acc)
    if subscribe_result != 0:
        print('账号订阅失败 %d' % subscribe_result)

    # CREDIT_DIRECT_CASH_REPAY 直接还款
    # stock_code 和 volume 为占位参数，任意值即可
    fix_result_order_id = xt_trader.order_stock(
        acc, '600000.SH', xtconstant.CREDIT_DIRECT_CASH_REPAY,
        repay_money, xtconstant.FIX_PRICE, -1,
        'strategy_name', 'remark')

    xt_trader.run_forever()
```
