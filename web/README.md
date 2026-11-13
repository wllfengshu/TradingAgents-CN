# ZStock Web 控制台

React + Vite 前端，FastAPI 后端。策略 / 回测 / 订单落 **MongoDB**；行情同步走 **stockdb → Mongo**。

## 启动

在项目根目录：

```powershell
# 1) 后端 API（示例端口 18081，与 frontend/vite.config.ts 代理一致）
.\.venv\Scripts\python.exe -m uvicorn web.backend.main:app --host 127.0.0.1 --port 18081

# 2) 前端 :5173
cd web\frontend
npm run dev
```

浏览器：http://127.0.0.1:5173  

## 功能

| 导航 | 说明 |
|------|------|
| 工作台 | 今日状态、行情、快捷入口 |
| 研究 | 因子库 / 策略库 / 回测 |
| 运维 | 数据源探测 · Mongo 只读 · 任务与夜跑（Python 调度） |
| 交易 | 订单只读 |

夜跑调度在 API 进程内（默认 21:00），也可单独：

```powershell
.\.venv\Scripts\python.exe -m zstock.data_management.script.nightly_scheduler --time 21:00
```

## Mongo 集合（Web）

- `zstock_strategies` / `zstock_backtests` / `zstock_orders`
