给你整理一份 **TradingAgents-CN** 的快速上手知识库，5 分钟能判断要不要深入。

---

## 🎯 一句话定位

**TradingAgents-CN = 多智能体 LLM 驱动的"虚拟券商研究所"**，输入股票代码（A 股/港股/美股），一群各司其职的 AI Agent 会像真·投研团队一样分工、辩论、出报告——技术面、基本面、新闻面、情绪面全覆盖，最后给你"买入/持有/卖出"建议+逻辑链。

> 📌 关键边界先说清：**没有实盘接口，不下单，不连券商**。定位是「研究与学习工具」，输出是分析意见不是操作指令。

---

## 📜 来龙去脉

| 项目 | 说明 |
|---|---|
| **原版** | Tauric Research 2024 年底开源，MIT/UCLA 背景，论文 *TradingAgents: Multi-Agents LLM Financial Trading Framework*（arXiv:2412.20138） |
| **CN 分支** | `hsliuping/TradingAgents-CN`，Apache-2.0，2025 年初 fork 后做中文深度改造，**不是简单翻译** |
| **现状** | GitHub ~12K+ Star（中文圈多智能体金融框架最活跃的那个），版本迭代到 **cn-0.1.15**（独立 `cn-` 前缀避免和原版冲突） |

---

## 🏗️ 架构：多智能体怎么协作

基于 **LangGraph** 编排，工作流像一个迷你投行投研部：

```
数据层（Tushare/AkShare/FinnHub/Yahoo/Google News）
        ↓
  ┌─────┬────────┬────────┐
  │基本面│技术面  │新闻/情绪│  ← 四大分析师并行
  └─────┴────────┴────────┘
        ↓ 汇总
  🐂看涨研究员  vs  🐻看跌研究员   ← 结构化辩论
        ↓
     交易员（综合决策：买/持/卖）
        ↓
    风险管理（多层风控审查）
        ↓
    管理层/研究主管（最终审核）
```

**每个 Agent 的职责**：

- **分析师层**：基本面（财报/估值）、技术（MA/MACD/RSI）、新闻（事件情绪）、社媒（Reddit/雪球类情绪）
- **研究员层**：强制多空辩论，避免单边偏见
- **交易员**：收束所有输入，给投资建议+置信度+目标价位
- **风控**：评估仓位/止损/风险评分

研究深度分 **5 级**（1 级 ≈ 2min 快速概览，5 级 ≈ 25min 全辩论），可自选。

---

## 🆚 相比原版 TradingAgents 加了什么

| 维度 | 原版 | CN 增强版 |
|---|---|---|
| 市场 | 主要是美股 | **A 股/港股/美股**三市场 |
| 数据源 | FinnHub/Yahoo | + **Tushare、AkShare、BaoStock** |
| LLM | OpenAI 为主 | + **DeepSeek、阿里百炼(Qwen)、Gemini、OpenRouter 60+ 模型** |
| 提示词 | 英文语境 | 中文金融语境优化 |
| 界面 | CLI | + **Streamlit Web UI**（进度可视化、报告导出 Word/PDF/Markdown） |
| 部署 | 手动 | **Docker Compose 一键**（Web + MongoDB + Redis） |
| 新闻模块 | 基础 | v0.1.12 起加 AI 新闻过滤+质量评估三级处理 |

---

## 📊 数据 & 模型支持清单

**数据源**：
- A 股：Tushare、AkShare（默认 `akshare`，可在 `.env` 切）
- 港股：AkShare、Yahoo
- 美股：FinnHub、Yahoo Finance
- 新闻：Google News

**LLM**：
- 🇨🇳 DeepSeek（`deepseek-chat`，性价比首选）
- 🇨🇳 阿里百炼（`qwen-turbo/plus/max`，中文优化）
- 🌍 Google AI（gemini-2.5-pro/flash 等 9 个模型）
- 🌍 原生 OpenAI + OpenRouter（60+ 模型聚合）

模型可在 Web UI 侧边栏一键切换，**URL 参数持久化**，刷新不丢。

---

## 🚀 快速启动（Docker，推荐）

```bash
# 1. 克隆
git clone https://github.com/hsliuping/TradingAgents-CN.git
cd TradingAgents-CN

# 2. 配置 API Key
cp .env.example .env
# 至少填 DASHSCOPE_API_KEY（或 DEEPSEEK_API_KEY）+ FINNHUB_API_KEY
# A 股建议加 TUSHARE_TOKEN=xxx 并把 TUSHARE_ENABLED=true

# 3. 启动
docker-compose up -d --build

# 4. 访问
# Web UI: http://localhost:8501
# MongoDB Express: http://localhost:8081
# Redis Commander: http://localhost:8082
```

本地部署也行（Python 3.10+，推荐 3.11，8GB RAM），`pip install -e .` 后 `python start_web.py`。

---

## ⚠️ 几个容易踩的坑

1. **不是交易系统**：无实盘、不下单、不连券商，别拿来当量化策略直接跑钱
2. **A 股数据**：Tushare 免费版有频率限制，跑 5 级深度建议搞个积分账号；默认 `akshare` 不用 token 但偶发不稳定
3. **成本**：DeepSeek / Qwen-Turbo 跑一轮 5 级分析大概几毛到几块 RMB，GPT-4o 会贵不少
4. **网络**：FinnHub/Google News 要能连外网，A 股数据源国内直连没问题

---

## 🎯 适合谁用

- **量化/金融 AI 工程师**：拿来做多智能体协作范式参考，LangGraph + LLM 在金融场景的样本工程
- **个人投资者**：当"AI 投研助理"用，比单看同花顺多一层多空辩论视角
- **高校/研究机构**：行为金融、多 Agent 博弈的教学/仿真素材
- **不适合**：想找"自动赚钱机器人"的——这项目给的是分析，不是 α

---

## 📚 知识库导航（AI 路由入口）

本知识库由 6 个文件组成，按序号分工明确。遇到具体问题时，**先读本文定位方向，再路由到对应子文件深入**。

| 序号 | 文件 | 定位 | 适合回答的问题 |
|---|---|---|---|
| **0** | `0-TradingAgents-CN概述.md`（本文件） | 项目概述 + 路由总纲 | "这个项目是什么""整体架构怎样""怎么启动" |
| **1** | `1-app模块.md` | `app/` 后端服务层深度分析 | FastAPI 路由、服务层、数据模型、配置管理、数据源适配器、后台任务、中间件、API 接口设计 |
| **2** | `2-frontend模块.md` | `frontend/` Vue3 前端应用 | 前端页面结构、组件、路由、API 调用、UI 交互、Streamlit Web UI |
| **3** | `3-tradingagents模块.md` | `tradingagents/` 核心库深度分析 | Agent 定义、数据流、LLM 适配器、数据源提供商、缓存系统、记忆系统、配置管理、可扩展性 |
| **4** | `4-多智能体运行机制深度解析.md` | 多 Agent 协作机制的底层原理 | LangGraph 图编排、State 状态管理、条件路由、工厂函数模式、辩论机制执行流程、记忆系统原理、扩展指南 |
| **5** | `5-AI选股功能架构设计文档.md` | 二次开发：AI 选股功能架构设计 | AI 选股系统、大盘→板块→合力→龙头→风险分析链路、多阶段辩论机制、selector Agent/数据流/Graph 设计、前端接口 |

### 路由规则（给 AI 的指引）

1. **宏观问题**（"项目是什么""怎么部署""支持哪些市场/模型"）→ 本文件即可回答
2. **后端 API / 服务层问题**（"某个接口怎么实现的""数据源适配器怎么工作的""后台任务怎么调度"）→ 路由到 **`1-app模块.md`**
3. **前端 UI / 交互问题**（"页面怎么组织的""前端怎么调用后端""组件结构"）→ 路由到 **`2-frontend模块.md`**
4. **核心库内部实现**（"Agent 怎么定义的""数据流怎么走的""缓存/记忆系统怎么设计的""LLM 怎么适配的"）→ 路由到 **`3-tradingagents模块.md`**
5. **多 Agent 协作机制 / LangGraph 原理**（"辩论怎么跑的""状态怎么流转的""条件路由逻辑""怎么扩展新 Agent"）→ 路由到 **`4-多智能体运行机制深度解析.md`**
6. **AI 选股功能相关**（"选股系统怎么设计的""板块分析链路""选股辩论机制""selector 相关代码"）→ 路由到 **`5-AI选股功能架构设计文档.md`**
7. **跨模块问题**（如"一次完整请求从前端到 Agent 执行的全链路"）→ 先读本文件理解全局，再按数据流方向依次读取 **2 → 1 → 3 → 4**

> **注意**：文件 1-4 是对已有开源代码的分析报告，文件 5 是二次开发的功能设计文档。修改代码时以源码为准，这些文件提供的是认知上下文。

---

# 项目启动方式：

启动虚拟环境：
.venv\Scripts\activate

后端：
 python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

前端：
cd frontend; npm run dev