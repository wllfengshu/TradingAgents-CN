# XtQuant 常见问题（FAQ）

> 本文档前 5 个问题（Q1–Q5）来自原始`常见问题_迅投知识库`文档。**Q6–Q10 为编者根据使用过程中常见场景补充**，不属于原文档。

---

## Q1：导入 xtquant 时提示 `NO module named 'xtquant.IPythonAPiClient'`

**原因**：Python 版本不受支持。

**解决**：目前 xtquant 支持的 Python 版本为 **64 位 Python 3.6 ~ 3.11**，请使用受支持的 Python 版本重试。

---

## Q2：连接 xtquant 时失败，返回 `-1` 及解决方法

按以下顺序排查：

### 1. 客户端是否以极简模式登录

登录 QMT 系统时需要勾选**极简模式**。

### 2. 检查路径是否正确

| 客户端类型 | 路径指向 |
|----------|---------|
| MiniQMT | 安装目录下 `\userdata_mini` 文件夹 |
| 投研端 | 安装目录下 `\userdata` 文件夹 |

### 3. 客户端安装在 C 盘的话，每次都需要用管理员权限运行策略

否则会有权限问题。**提示：不建议安装在 C 盘**。

可以通过以下测试来验证是否有写入权限：

```python
file_path = r"d:\qmt\userdata_mini\example.txt"  # 设置文件路径和名称

# 使用open函数创建文件，并指定写入模式("w"表示写入模式)
with open(file_path, "w") as file:
    file.write("123")  # 向文件写入内容
```

如果出现 `PermissionError`，则说明存在文件权限问题。

### 4. 路径正确时换个 session（任意整数即可）

**提示**：由于机制限制，同一个 session 的两次 Python 进程 connect 之间必须超过 3 秒钟。

### 5. 如果 miniqmt 开启后，userdata_mini 文件夹内没有 `up_queue_xtquant` 文件

说明用户没有对应函数下单的权限，需要联系券商开启。

---

## Q3：执行 `xtdatacenter.init` 时提示监听 58609 端口失败

说明当前环境的 58609 端口被其他程序占用，通常是启动了两个 xtdc 服务导致的。

**方法 1**：可以通过指定 `xtdc.init(False)` 后，使用 `xtdc.listen(port)` 指定自己需要的端口：

```python
from xtquant import xtdatacenter as xtdc

xtdc.set_token("这里输入token")
xtdc.init(False)
port = 58601
xtdc.listen(port=port)
print(f"服务启动,开放端口：{port}")
```

**方法 2**：关闭所有 py 程序，或重启电脑，再执行 `xtdc.init`。

---

## Q4：下单后，查询委托的投资备注只有前半部分

极简客户端的 `order_remark` 字段有长度限制，**最大 24 个英文字符**（一个中文占 3 个），超出的部分会丢弃。大 qmt 没有长度限制。

---

## Q5：userdata_mini 目录下，生成大量 `down_queue` 文件

该文件是 xttrade 指定新的 session 产生的文件，可以参考"指定 session id 范围连接交易"示例（见 `05_examples.md` 3.6）控制 session 产生的范围，避免该文件大量产生。

**该文件可以删除。**

---

## 以下为编者补充问题（Q6–Q10）

> 以下内容不在原始文档中，是基于常见场景的整理。

---

## Q6（编者补充）：如何避免在回调函数中调用同步查询接口卡住

**方法 1**：开启专用响应线程（时序不确定）

```python
xt_trader.set_relaxed_response_order_enabled(True)
```

**方法 2（推荐）**：在回调中使用异步查询接口，如 `query_stock_orders_async`。

---

## Q7（编者补充）：历史数据为何为空 / 拿不到数据

**原因**：历史数据需先下载到本地才能获取。

**解决步骤**：

```python
# 必须先下载
xtdata.download_history_data(stock_code, period='1d', start_time='20200101')

# 然后再获取
data = xtdata.get_market_data_ex([], [stock_code], period='1d')
```

对于实时数据，需先订阅：

```python
xtdata.subscribe_quote(stock_code, period='1d', count=-1)
# 等待订阅完成
import time; time.sleep(1)
data = xtdata.get_market_data_ex([], [stock_code], period='1d')
```

---

## Q8（编者补充）：单股订阅量大时行情延迟或异常

**原因**：单股订阅建议不超过 50 个。

**解决**：使用全推订阅代替：

```python
xtdata.subscribe_whole_quote(["SH", "SZ"], callback=your_callback)
```

---

## Q9（编者补充）：如何查看过期（退市）板块名称

```python
print([i for i in xtdata.get_sector_list() if "过期" in i])
```

需先执行 `xtdata.download_history_contracts()` 下载退市合约信息。

---

## Q10（编者补充）：同一时间运行多个策略如何避免 session 冲突

每个策略使用不同的 session_id：

```python
import time
session_id = int(time.time())  # 用时间戳作为 session_id
```

或使用随机范围（参考 `05_examples.md` 3.6 示例）。
