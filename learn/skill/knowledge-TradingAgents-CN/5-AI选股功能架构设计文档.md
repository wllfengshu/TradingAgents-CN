# AI选股功能架构设计文档
这是基于网上的TradingAgents-CN开源项目做的二次开发的项目，实现了“AI选股功能”。
ai选股的代码：
agent定义：tradingagents/agents/selector
数据指标：tradingagents/dataflows/selector
图：tradingagents/graph/selector

> **版本**: v1.2.0
> **创建日期**: 2026-06-06
> **更新日期**: 2026-06-06
> **设计目标**: 设计一个与现有"AI股票分析功能"风格一致的多智能体AI选股系统，支持多阶段辩论机制
创建 ToolNode 并编译 LangGraph 状态图
---

## 目录

1. [需求概述](#1-需求概述)
2. [整体架构](#2-整体架构)
3. [Agent定义](#3-agent定义)
4. [State状态设计](#4-state状态设计)
5. [Graph工作流设计](#5-graph工作流设计)
6. [多阶段辩论机制](#6-多阶段辩论机制)
7. [条件路由逻辑](#7-条件路由逻辑)
8. [工具与数据源](#8-工具与数据源)
9. [与现有系统的关系](#9-与现有系统的关系)
10. [文件结构规划](#10-文件结构规划)
11. [已确认问题](#11-已确认问题)
12. [附录A：提示词模板](#附录a提示词模板)
13. [附录B：前端接口设计](#附录b前端接口设计)
14. [附录C：前端分析师团队定义](#附录c前端分析师团队定义)
15. [附录D：Tools工具定义](#附录dtools工具定义)

---

## 1. 需求概述

### 1.1 功能定位

| 功能 | AI股票分析（已有） | AI选股（新增） |
|------|------------------|---------------|
| **输入** | 用户指定股票代码 + 分析日期 | 无需输入，自动使用当前日期 |
| **分析对象** | 单只股票深度分析 | 从全市场筛选推荐股票 |
| **分析维度** | 技术、基本面、新闻、情绪 | 大盘→板块→合力→龙头→风险 |
| **输出** | 买卖决策 + 详细报告 | 推荐股票列表 + 详细分析报告 |
| **辩论机制** | 看涨/看跌投资辩论 | 板块辩论 + 股票辩论 |

### 1.2 核心分析链路

```
大盘分析师（分析大盘环境）
    │
    ├─ 偏空 → 终止，输出"观望"
    │
    └─ 偏多/中性 → 继续
        │
        ▼
主线板块分析师（识别主线板块）
    │
    │ 使用技术指标筛选：
    │ - 涨幅前10板块
    │ - 涨停统计
    │ - 强势股池统计
    │ - 封板比统计
    │ - 炸板统计
    │
    ├─ 无主线 → 终止，输出"观望"
    │
    ├─ 只有1个板块 → 直接确认，跳过辩论
    │
    └─ 有2-3个候选板块 → 进入辩论
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│                    板块辩论环节                              │
│                                                             │
│  前置筛选：技术指标筛选出2-3个候选板块（如贵金属、半导体、军工）│
│                                                             │
│  ┌──────────────────┐     ┌──────────────────┐            │
│  │ 板块看涨研究员    │◄──►│ 板块看跌研究员    │            │
│  │                  │辩论  │                  │            │
│  │ 论证：           │     │ 论证：           │            │
│  │ "这些板块整体    │     │ "涨幅已透支、   │            │
│  │  值得追逐"       │     │  炸板率高"      │            │
│  └──────────────────┘     └──────────────────┘            │
│            │                       │                       │
│            └─── 达到轮数上限 ───────┘                       │
│                    │                                       │
│                    ▼                                       │
│            ┌──────────────────┐                            │
│            │   板块辩论法官    │                            │
│            │                  │                            │
│            │ 综合裁决：       │                            │
│            │ 确认主线=贵金属   │                            │
│            └──────────────────┘                            │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
市场合力分析师（从确认主线板块找合力股票）
    │
    ├─ 无合力股票 → 终止，输出"观望"
    │
    └─ 有合力股票（2-3支） → 进入辩论
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│                    股票辩论环节                              │
│                                                             │
│  ┌──────────────────┐     ┌──────────────────┐            │
│  │ 股票看涨研究员    │◄──►│ 股票看跌研究员    │            │
│  │                  │辩论  │                  │            │
│  │ 论证：           │     │ 论证：           │            │
│  │ "这3支股票综合   │     │ "603985涨幅过大 │            │
│  │  分析值得追涨"   │     │  PE过高"        │            │
│  └──────────────────┘     └──────────────────┘            │
│            │                       │                       │
│            └─── 达到轮数上限 ───────┘                       │
│                    │                                       │
│                    ▼                                       │
│            ┌──────────────────┐                            │
│            │   股票辩论法官    │                            │
│            │                  │                            │
│            │ 综合裁决：       │                            │
│            │ 优质标的=603985  │                            │
│            └──────────────────┘                            │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
股票龙头分析师（从优质标的中找龙头）
    │
    ├─ 无龙头 → 终止，输出"观望"
    │
    └─ 有龙头股（1-2支） → 继续
        │
        ▼
风险分析师（风险评估）
    │
    ├─ 高风险 → 终止，输出"规避"
    │
    └─ 低/中风险 → 继续
        │
        ▼
决策分析师（最终决策）
    │
    └─ 输出：推荐股票 + 仓位建议 + 风险提示
```

### 1.3 提前终止与跳过辩论条件

| 终止节点 | 终止/跳过条件 | 处理方式 |
|---------|-------------|---------|
| 大盘分析师 | 大盘偏空 | 提前终止 → 观望 |
| 主线板块分析师 | 无主线板块（has_main_sector=false） | 提前终止 → 观望 |
| 主线板块分析师 | 只有1个候选板块 | 跳过辩论，直接确认 |
| 板块辩论法官 | 辩论结论为"无值得追逐的板块" | 提前终止 → 观望 |
| 市场合力分析师 | 无合力股票 | 提前终止 → 观望 |
| 市场合力分析师 | 只有1支合力股票 | 跳过辩论，直接确认 |
| 股票辩论法官 | 所有股票不值得追涨 | 提前终止 → 观望 |
| 股票龙头分析师 | 无龙头股 | 提前终止 → 观望 |
| 风险分析师 | 风险等级=高，或safe_stocks为空 | 提前终止 → 规避 |

---

## 2. 整体架构

### 2.1 架构分层

```
┌─────────────────────────────────────────────────────────────────────┐
│                        前端层 (Vue3)                                 │
│                     AI选股界面 + 结果展示                             │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        API层 (FastAPI)                               │
│                    ai_selector_router.py                             │
│              create_task / get_result / execute_task                 │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        服务层                                        │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │           AiSelectorGraph（主编排器）                         │   │
│  │              graph/selector_graph.py                          │   │
│  │                                                              │   │
│  │  ├── setup.py          图构建                                │   │
│  │  ├── conditional_logic.py  条件路由                          │   │
│  │  ├── propagation.py    执行传播                              │   │
│  │  └── signal_processing.py  信号处理                          │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                 Agent层                                      │   │
│  │                                                              │   │
│  │  agents/                                                     │   │
│  │  ├── analysts/                                               │   │
│  │  │   ├── market_analyst.py      大盘分析师                   │   │
│  │  │   ├── sector_analyst.py      主线板块分析师                │   │
│  │  │   ├── force_analyst.py       市场合力分析师                │   │
│  │  │   ├── leader_analyst.py      股票龙头分析师                │   │
│  │  │   └── risk_analyst.py        风险分析师                   │   │
│  │  │   └── decision_analyst.py    决策分析师                   │   │
│  │  │                                                          │   │
│  │  ├── debaters/                辩论团队（新增）                │   │
│  │  │   ├── sector_bull_researcher.py   板块看涨研究员          │   │
│  │  │   ├── sector_bear_researcher.py   板块看跌研究员          │   │
│  │  │   ├── sector_judge.py             板块辩论法官            │   │
│  │  │   ├── stock_bull_researcher.py    股票看涨研究员          │   │
│  │  │   ├── stock_bear_researcher.py    股票看跌研究员          │   │
│  │  │   └── stock_judge.py              股票辩论法官            │   │
│  │  │                                                          │   │
│  │  └── utils/                                                   │   │
│  │      ├── agent_states.py       状态定义                       │   │
│  │      └── toolkit.py            工具包                         │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                 数据层                                       │   │
│  │                                                              │   │
│  │  dataflows/                                                  │   │
│  │  ├── indicators/             指标计算模块                     │   │
│  │  │   ├── market_indicators.py    大盘指标                    │   │
│  │  │   ├── sector_indicators.py    板块指标                    │   │
│  │  │   ├── force_indicators.py     合力指标                    │   │
│  │  │   ├── leader_indicators.py    龙头指标                    │   │
│  │  │   └── risk_indicators.py      险指标                     │   │
│  │  │                                                          │   │
│  │  └── providers/              数据提供商                       │   │
│  │      └── akshare_provider.py     AKShare数据源               │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        基础设施层                                    │
│                                                                     │
│  ├── MongoDB           任务存储、结果存储                           │
│  ├── LLM提供商          12+ LLM适配器                               │
│  ├── ChromaDB          记忆系统（可选）                             │
│  └─────────────────────────────────────────────────────────────────┘
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 与现有系统的对比

| 模块 | AI股票分析 | AI选股 |
|------|-----------|--------|
| **主类** | `TradingAgentsGraph` | `AiSelectorGraph` |
| **状态类** | `AgentState` | `SelectorState` |
| **分析师工厂** | `create_xxx_analyst(llm, toolkit)` | `create_xxx_analyst(llm, toolkit)` |
| **辩论员工厂** | `create_bull/bear_researcher(llm, memory)` | `create_sector/stock_bull/bear(llm, memory)` |
| **法官工厂** | `create_research_manager(llm, memory)` | `create_sector/stock_judge(llm, memory)` |
| **条件逻辑** | `should_continue_xxx()` | `should_continue_xxx()` + `should_debate_xxx()` |

---

## 3. Agent定义

### 3.1 设计原则：采用LangGraph Tools机制

**核心设计**：与现有"AI股票分析功能"保持一致，使用LangGraph的`@tool`装饰器定义工具，LLM通过`bind_tools()`自主调用工具获取数据。

| 对比项 | ❌ 原设计（ai_selector_service.py） | ✅ 新设计（与现有系统一致） |
|-------|--------------------------------|------------------------|
| **数据获取方式** | Python函数计算指标，写死传给LLM | 定义`@tool`装饰器的工具，LLM自主调用 |
| **工具定义位置** | `compute_indicators.py`（普通函数） | `SelectorToolkit`类（`@tool`装饰器） |
| **LLM调用方式** | 直接invoke prompt | `llm.bind_tools(tools)` + 条件路由 |
| **工具执行** | 无ToolNode机制 | LangGraph的`ToolNode`执行工具 |
| **死循环防护** | 无防护 | 工具调用计数器 + 条件路由强制终止 |

### 3.2 分析师团队

| Agent | 职责 | 绑定工具 | 输出State字段 |
|-------|------|---------|--------------|
| **大盘分析师** | 判断大盘环境 | `get_market_indicators` | `market_report`, `market_sentiment` |
| **主线板块分析师** | 识别主线板块 | `get_sector_indicators` | `sector_report`, `main_sectors` |
| **市场合力分析师** | 找合力股票 | `get_force_indicators` | `force_report`, `candidate_stocks` |
| **股票龙头分析师** | 找龙头股 | `get_leader_indicators` | `leader_report`, `leading_stocks` |
| **风险分析师** | 风险核查 | `get_risk_indicators` | `risk_report`, `safe_stocks` |
| **决策分析师** | 最终决策 | 无（直接LLM调用） | `decision_report`, `final_decision` |

### 3.3 辩论团队（不绑定工具）

辩论员直接接收上游分析结果，通过prompt调用LLM，不需要工具调用：

| Agent | 职责 | 输入来源 | 输出 |
|-------|------|---------|------|
| **板块看涨研究员** | 论证板块值得追逐 | `main_sectors` + `sector_report` | `sector_bull_argument` |
| **板块看跌研究员** | 论证板块不值得追逐 | `main_sectors` + `sector_report` | `sector_bear_argument` |
| **板块辩论法官** | 综合裁决 | 辩论历史 | `confirmed_sectors` |
| **股票看涨研究员** | 论证股票值得追涨 | `candidate_stocks` + `force_report` | `stock_bull_argument` |
| **股票看跌研究员** | 论证股票不值得追涨 | `candidate_stocks` + `force_report` | `stock_bear_argument` |
| **股票辩论法官** | 综合裁决 | 辩论历史 | `quality_stocks` |

### 3.4 分析师工厂函数（参考现有系统）

```python
# agents/selector/analysts/market_analyst.py

def create_market_analyst(llm, toolkit):
    """创建大盘分析师（采用LangGraph Tools机制）"""
    
    @log_analyst_module("market")
    def market_analyst_node(state):
        # 🔧 工具调用计数器 - 防止无限循环
        tool_call_count = state.get("market_tool_call_count", 0)
        max_tool_calls = 1  # 大盘指标一次调用即可
        
        logger.info(f"🔧 [工具调用计数] 当前次数: {tool_call_count}/{max_tool_calls}")
        
        # 1. 绑定工具（LLM自主决定调用）
        tools = [toolkit.get_market_indicators]
        
        # 2. 构建系统提示词（强调必须调用工具）
        system_prompt = """
        你是专业的A股大盘分析师。
        
        🔴 强制要求：你必须调用工具获取真实数据！
        ❌ 绝对禁止：不允许假设、编造或直接回答任何问题！
        
        ✅ 工作流程：
        1. 如果消息历史中没有工具结果（ToolMessage），立即调用 get_market_indicators 工具
        2. 如果消息历史中已经有工具结果，立即基于工具数据生成分析报告
        3. 工具只需调用一次！不要重复调用！
        
        📊 分析要求：
        - 分析指数走势（上证、深证）
        - 评估北向资金方向
        - 统计涨跌家数比
        - 输出市场情绪判断：偏多/偏空/中性
        
        📝 输出格式（JSON）：
        {
          "market_sentiment": "偏多/偏空/中性",
          "key_points": ["依据1", "依据2"],
          "analysis_brief": "大盘分析简报（200字内）"
        }
        """
        
        # 3. 创建提示模板
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            MessagesPlaceholder(variable_name="messages"),
        ])
        
        # 4. 绑定工具并调用LLM
        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke({"messages": state["messages"]})
        
        # 5. 处理结果（工具调用 vs 直接回复）
        current_tool_calls = len(result.tool_calls) if hasattr(result, 'tool_calls') else 0
        
        if current_tool_calls > 0:
            # LLM请求调用工具，返回让ToolNode执行
            logger.info(f"📊 [大盘分析师] LLM请求调用工具: {result.tool_calls}")
            return {"messages": [result]}
        
        # 无工具调用，检查是否已有工具结果
        messages = state.get("messages", [])
        has_tool_result = any(isinstance(msg, ToolMessage) for msg in messages)
        
        if has_tool_result:
            # 已有工具数据，LLM生成分析报告
            report = result.content
            market_sentiment = extract_market_sentiment(report)
            logger.info(f"✅ [大盘分析师] 生成报告完成")
            
            # 🔧 返回清洁的AIMessage（不含tool_calls），防止死循环
            from langchain_core.messages import AIMessage
            clean_message = AIMessage(content=report)
            
            return {
                "messages": [clean_message],
                "market_report": report,
                "market_sentiment": market_sentiment,
                "market_tool_call_count": tool_call_count + 1
            }
        
        # 无工具调用且无工具结果，强制调用工具
        logger.warning(f"⚠️ [大盘分析师] LLM未调用工具，强制执行")
        forced_data = toolkit.get_market_indicators.invoke({"curr_date": state["trade_date"]})
        
        # 基于强制获取的数据生成报告
        analysis_result = llm.invoke([
            {"role": "user", "content": f"基于以下大盘指标数据进行分析：\n{forced_data}\n输出市场情绪判断（偏多/偏空/中性）"}
        ])
        
        report = analysis_result.content
        market_sentiment = extract_market_sentiment(report)
        
        return {
            "market_report": report,
            "market_sentiment": market_sentiment,
            "market_tool_call_count": tool_call_count + 1
        }
    
    return market_analyst_node
```

### 3.5 辩论员工厂函数（无工具调用）

```python
# agents/selector/debaters/sector_bull_researcher.py

def create_sector_bull_researcher(llm):
    """创建板块看涨研究员（直接LLM调用，无工具）"""
    
    def sector_bull_node(state):
        # 直接接收上游数据，不需要工具调用
        main_sectors = state.get("main_sectors", [])
        sector_report = state.get("sector_report", "")
        debate_state = state.get("sector_debate_state", {})
        
        # 构建辩论提示词
        prompt = f"""
        你是板块看涨研究员，负责论证候选板块值得追逐。
        
        # 候选板块：{main_sectors}
        # 板块分析数据：{sector_report}
        # 上游看跌论点（需反驳）：{debate_state.get('current_response', '')}
        
        论证要点：
        1. 持续强度：5日/10日涨跌幅分析
        2. 资金真实性：封板比>1说明主力锁仓意愿强
        3. 情绪稳定性：炸板率<10%说明市场惜售
        4. 涨停质量：涨停股数量和连板高度
        
        输出格式（JSON）：
        {{
          "overall_argument": "看涨论点（200字以内）",
          "sectors_analysis": [
            {{
              "sector": "板块名",
              "worth_chasing": true,
              "argument": "论点（含数据依据）"
            }}
          ]
        }}
        """
        
        result = llm.invoke([{"role": "user", "content": prompt}])
        
        # 更新辩论状态
        new_debate_state = {
            "history": debate_state.get("history", "") + f"\nBull: {result.content}",
            "bull_history": debate_state.get("bull_history", "") + f"\n{result.content}",
            "current_response": f"Bull Analyst: {result.content}",
            "count": debate_state.get("count", 0) + 1,
        }
        
        return {"sector_debate_state": new_debate_state}
    
    return sector_bull_node
```

---

## 4. State状态设计

### 4.1 主状态 `SelectorState`

```python
# agents/utils/agent_states.py

class SelectorState(MessagesState):
    """AI选股主状态"""
    
    # ===== 分析日期 =====
    analysis_date: Annotated[str, "分析日期（自动获取当前日期）"]
    
    # ===== 阶段1：大盘分析 =====
    market_report: Annotated[str, "大盘分析报告"]
    market_sentiment: Annotated[str, "大盘情绪：偏多/偏空/中性"]
    
    # ===== 阶段2：板块分析 =====
    sector_report: Annotated[str, "板块分析报告"]
    main_sectors: Annotated[List[str], "主线板块列表"]
    has_main_sector: Annotated[bool, "是否有主线板块"]
    
    # ===== 阶段3：板块辩论 =====
    sector_debate_state: Annotated[SectorDebateState, "板块辩论状态"]
    confirmed_sectors: Annotated[List[str], "辩论确认的主线板块"]
    
    # ===== 阶段4：合力分析 =====
    force_report: Annotated[str, "合力分析报告"]
    candidate_stocks: Annotated[List[Dict], "候选合力股票列表（2-3支）"]
    force_direction: Annotated[str, "合力方向：正向共振/反向分歧/主力主导"]
    
    # ===== 阶段5：股票辩论 =====
    stock_debate_state: Annotated[StockDebateState, "股票辩论状态"]
    quality_stocks: Annotated[List[Dict], "辩论筛选的优质标的"]
    
    # ===== 阶段6：龙头分析 =====
    leader_report: Annotated[str, "龙头分析报告"]
    leading_stocks: Annotated[List[Dict], "龙头股列表（1-2支）"]
    
    # ===== 阶段7：风险分析 =====
    risk_report: Annotated[str, "风险分析报告"]
    risk_level: Annotated[str, "风险等级：低/中/高"]
    safe_stocks: Annotated[List[Dict], "安全标的列表"]
    
    # ===== 阶段8：最终决策 =====
    decision_report: Annotated[str, "决策报告"]
    final_decision: Annotated[Dict, "最终决策结构化数据"]
    
    # ===== 提前终止 =====
    early_stop: Annotated[bool, "是否提前终止"]
    early_stop_reason: Annotated[str, "提前终止原因"]
    early_stop_node: Annotated[str, "提前终止节点"]
```

### 4.2 辩论子状态

#### 板块辩论状态

```python
class SectorDebateState(TypedDict):
    """板块辩论状态"""
    
    bull_history: Annotated[str, "板块看涨方历史发言"]
    bear_history: Annotated[str, "板块看跌方历史发言"]
    history: Annotated[str, "完整辩论历史"]
    current_response: Annotated[str, "最新发言"]
    current_sector: Annotated[str, "当前辩论的板块"]
    judge_decision: Annotated[str, "法官裁决"]
    count: Annotated[int, "发言计数"]
```

#### 股票辩论状态

```python
class StockDebateState(TypedDict):
    """股票辩论状态"""
    
    bull_history: Annotated[str, "股票看涨方历史发言"]
    bear_history: Annotated[str, "股票看跌方历史发言"]
    history: Annotated[str, "完整辩论历史"]
    current_response: Annotated[str, "最新发言"]
    current_stock: Annotated[Dict, "当前辩论的股票"]
    judge_decision: Annotated[str, "法官裁决"]
    count: Annotated[int, "发言计数"]
    debated_stocks: Annotated[List[str], "已完成辩论的股票代码"]
    remaining_stocks: Annotated[List[Dict], "待辩论的股票"]
```

---

## 5. Graph工作流设计

### 5.1 设计原则：采用LangGraph ToolNode机制

**核心设计**：与现有"AI股票分析功能"保持一致，分析师节点通过`bind_tools()`绑定工具，LLM自主决定是否调用工具。如果LLM返回`tool_calls`，条件路由将流程导向`ToolNode`执行工具，执行完成后返回分析师节点继续处理。

```
┌──────────────────────────────────────────────────────────────────┐
│                    工具调用循环机制                                │
│                                                                  │
│  Analyst Node                                                    │
│      │                                                           │
│      │ 1. LLM.invoke(bind_tools)                                 │
│      ▼                                                           │
│  ┌─────────────────┐                                             │
│  │   AI Message    │                                             │
│  │  (含tool_calls) │──── 有tool_calls ───► ToolNode              │
│  │                 │                                             │
│  │  (直接回复)     │──── 无tool_calls ───► 下一个节点            │
│  └─────────────────┘                                             │
│      │                                                           │
│      │ ToolNode执行工具                                          │
│      ▼                                                           │
│  ┌─────────────────┐                                             │
│  │  Tool Message   │                                             │
│  │  (工具返回数据) │──► 返回Analyst Node                          │
│  └─────────────────┘                                             │
│      │                                                           │
│      │ 2. LLM基于工具数据生成报告                                 │
│      ▼                                                           │
│  最终分析报告                                                     │
└──────────────────────────────────────────────────────────────────┘
```

### 5.2 ToolNode注册

```python
# graph/selector/setup.py

from langgraph.prebuilt import ToolNode
from langgraph.graph import StateGraph, END, START

class SelectorGraphSetup:
    """AI选股图构建器（采用LangGraph ToolNode机制）"""
    
    def __init__(
        self,
        quick_thinking_llm,
        deep_thinking_llm,
        toolkit: SelectorToolkit,
        conditional_logic: SelectorConditionalLogic,
        config: Dict[str, Any] = None,
    ):
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.toolkit = toolkit
        self.conditional_logic = conditional_logic
        self.config = config or {}
        
        # 🔧 创建分析师节点工厂
        self.analyst_nodes = self._create_analyst_nodes()
        
        # 🔧 创建ToolNode（工具执行节点）
        self.tool_nodes = self._create_tool_nodes()
        
        # 🔧 创建辩论节点工厂
        self.debate_nodes = self._create_debate_nodes()
    
    def _create_tool_nodes(self) -> Dict[str, ToolNode]:
        """创建ToolNode：用于执行LLM调用的工具"""
        tool_nodes = {}
        
        # 大盘分析师ToolNode
        tool_nodes["market"] = ToolNode([self.toolkit.get_market_indicators])
        
        # 板块分析师ToolNode
        tool_nodes["sector"] = ToolNode([self.toolkit.get_sector_indicators])
        
        # 合力分析师ToolNode
        tool_nodes["force"] = ToolNode([self.toolkit.get_force_indicators])
        
        # 龙头分析师ToolNode
        tool_nodes["leader"] = ToolNode([self.toolkit.get_leader_indicators])
        
        # 风险分析师ToolNode
        tool_nodes["risk"] = ToolNode([self.toolkit.get_risk_indicators])
        
        logger.info(f"✅ [ToolNode创建] 已创建 {len(tool_nodes)} 个ToolNode")
        return tool_nodes
    
    def setup_graph(self):
        workflow = StateGraph(SelectorState)
        
        # ===== 注册分析师节点 =====
        workflow.add_node("Market Analyst", self.analyst_nodes["market"])
        workflow.add_node("Sector Analyst", self.analyst_nodes["sector"])
        workflow.add_node("Force Analyst", self.analyst_nodes["force"])
        workflow.add_node("Leader Analyst", self.analyst_nodes["leader"])
        workflow.add_node("Risk Analyst", self.analyst_nodes["risk"])
        workflow.add_node("Decision Analyst", self.analyst_nodes["decision"])
        
        # ===== 注册ToolNode（工具执行节点）=====
        workflow.add_node("tools_market", self.tool_nodes["market"])
        workflow.add_node("tools_sector", self.tool_nodes["sector"])
        workflow.add_node("tools_force", self.tool_nodes["force"])
        workflow.add_node("tools_leader", self.tool_nodes["leader"])
        workflow.add_node("tools_risk", self.tool_nodes["risk"])
        
        # ===== 注册消息清理节点（防止Anthropic兼容问题）=====
        workflow.add_node("Msg Clear Market", create_msg_delete())
        workflow.add_node("Msg Clear Sector", create_msg_delete())
        workflow.add_node("Msg Clear Force", create_msg_delete())
        workflow.add_node("Msg Clear Leader", create_msg_delete())
        workflow.add_node("Msg Clear Risk", create_msg_delete())
        
        # ===== 注册板块辩论节点 =====
        workflow.add_node("Sector Bull Debater", self.debate_nodes["sector_bull"])
        workflow.add_node("Sector Bear Debater", self.debate_nodes["sector_bear"])
        workflow.add_node("Sector Judge", self.debate_nodes["sector_judge"])
        
        # ===== 注册股票辩论节点 =====
        workflow.add_node("Stock Bull Debater", self.debate_nodes["stock_bull"])
        workflow.add_node("Stock Bear Debater", self.debate_nodes["stock_bear"])
        workflow.add_node("Stock Judge", self.debate_nodes["stock_judge"])
        
        # ===== 注册提前终止节点 =====
        workflow.add_node("Early Stop Handler", self.early_stop_handler)
        
        # ... 边的定义见下一节
```

### 5.3 边的定义（含工具调用循环）

```python
# ===== 阶段1：大盘分析（含工具调用循环）=====
workflow.add_edge(START, "Market Analyst")

# 条件路由：检测tool_calls判断是否需要执行工具
workflow.add_conditional_edges(
    "Market Analyst",
    conditional_logic.should_continue_market,  # 检测tool_calls
    {
        "tools_market": "tools_market",       # 有tool_calls → 执行工具
        "Msg Clear Market": "Msg Clear Market",  # 无tool_calls → 清理消息
    }
)

# ToolNode执行完成后返回分析师节点
workflow.add_edge("tools_market", "Market Analyst")

# 分析完成后路由到下一节点
workflow.add_conditional_edges(
    "Msg Clear Market",
    conditional_logic.should_continue_after_market,
    {
        "early_stop": "Early Stop Handler",
        "continue": "Sector Analyst",
    }
)

# ===== 阶段2：板块分析（含工具调用循环）=====
workflow.add_conditional_edges(
    "Sector Analyst",
    conditional_logic.should_continue_sector,  # 检测tool_calls
    {
        "tools_sector": "tools_sector",
        "Msg Clear Sector": "Msg Clear Sector",
    }
)
workflow.add_edge("tools_sector", "Sector Analyst")

workflow.add_conditional_edges(
    "Msg Clear Sector",
    conditional_logic.should_continue_after_sector,
    {
        "early_stop": "Early Stop Handler",
        "skip_debate": "Force Analyst",  # 只有1个板块，跳过辩论
        "enter_debate": "Sector Bull Debater",  # >=2个板块，进入辩论
    }
)

# ===== 阶段3：板块辩论 =====
workflow.add_conditional_edges(
    "Sector Bull Debater",
    conditional_logic.should_continue_sector_debate,
    {
        "continue": "Sector Bear Debater",
        "end": "Sector Judge",
    }
)
workflow.add_conditional_edges(
    "Sector Bear Debater",
    conditional_logic.should_continue_sector_debate,
    {
        "continue": "Sector Bull Debater",
        "end": "Sector Judge",
    }
)
workflow.add_conditional_edges(
    "Sector Judge",
    conditional_logic.should_continue_after_sector_judge,
    {
        "early_stop": "Early Stop Handler",
        "continue": "Force Analyst",
    }
)

# ===== 阶段4：合力分析（含工具调用循环）=====
workflow.add_conditional_edges(
    "Force Analyst",
    conditional_logic.should_continue_force,
    {
        "tools_force": "tools_force",
        "Msg Clear Force": "Msg Clear Force",
    }
)
workflow.add_edge("tools_force", "Force Analyst")

workflow.add_conditional_edges(
    "Msg Clear Force",
    conditional_logic.should_continue_after_force,
    {
        "early_stop": "Early Stop Handler",
        "skip_debate": "Leader Analyst",  # 只有1支股票，跳过辩论
        "enter_debate": "Stock Bull Debater",  # >=2支股票，进入辩论
    }
)

# ===== 阶段5：股票辩论 =====
workflow.add_conditional_edges(
    "Stock Bull Debater",
    conditional_logic.should_continue_stock_debate,
    {
        "continue": "Stock Bear Debater",
        "end": "Stock Judge",
    }
)
workflow.add_conditional_edges(
    "Stock Bear Debater",
    conditional_logic.should_continue_stock_debate,
    {
        "continue": "Stock Bull Debater",
        "end": "Stock Judge",
    }
)
workflow.add_conditional_edges(
    "Stock Judge",
    conditional_logic.should_continue_after_stock_judge,
    {
        "early_stop": "Early Stop Handler",
        "continue": "Leader Analyst",
    }
)

# ===== 阶段6：龙头分析（含工具调用循环）=====
workflow.add_conditional_edges(
    "Leader Analyst",
    conditional_logic.should_continue_leader,
    {
        "tools_leader": "tools_leader",
        "Msg Clear Leader": "Msg Clear Leader",
    }
)
workflow.add_edge("tools_leader", "Leader Analyst")

workflow.add_conditional_edges(
    "Msg Clear Leader",
    conditional_logic.should_continue_after_leader,
    {
        "early_stop": "Early Stop Handler",
        "continue": "Risk Analyst",
    }
)

# ===== 阶段7：风险分析（含工具调用循环）=====
workflow.add_conditional_edges(
    "Risk Analyst",
    conditional_logic.should_continue_risk,
    {
        "tools_risk": "tools_risk",
        "Msg Clear Risk": "Msg Clear Risk",
    }
)
workflow.add_edge("tools_risk", "Risk Analyst")

workflow.add_conditional_edges(
    "Msg Clear Risk",
    conditional_logic.should_continue_after_risk,
    {
        "early_stop": "Early Stop Handler",
        "continue": "Decision Analyst",
    }
)

# ===== 阶段8：最终决策 =====
workflow.add_edge("Decision Analyst", END)
workflow.add_edge("Early Stop Handler", END)
```

### 5.4 工具调用流程图（详细）

```
START
    │
    ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    Market Analyst（大盘分析师）                        │
│                                                                      │
│  🔧 工具绑定：llm.bind_tools([get_market_indicators])                │
│                                                                      │
│  流程：                                                              │
│  1. LLM.invoke(bind_tools + prompt)                                 │
│  2. 检查响应：                                                       │
│     ├─ 有tool_calls → 返回AIMessage（含tool_calls）                  │
│     └  无tool_calls → 检查是否已有ToolMessage                        │
│         ├─ 有ToolMessage → 基于数据生成报告                           │
│         └─ 无ToolMessage → 强制调用工具（防护机制）                   │
└──────────────────────────────────────────────────────────────────────┘
    │
    │ 条件判断：should_continue_market
    │
    ├── 有tool_calls ─────► tools_market（ToolNode）
    │                              │
    │                              │ ToolNode.invoke()
    │                              │ 执行 get_market_indicators
    │                              │ 返回 ToolMessage
    │                              │
    │                              └──────► 返回 Market Analyst
    │                                                         │
    │                                                         │ LLM基于工具数据生成报告
    │                                                         │
    └── 无tool_calls ─────► Msg Clear Market                  │
                               │                               │
                               │ 条件判断：market_sentiment    │
                               │                               │
                               ├── 偏空 ──► Early Stop Handler ──► END
                               │              │ action: "观望"
                               │              │ reason: "大盘偏空"
                               │
                               └─ 偏多/中性 ──► Sector Analyst ◄─┘
```

### 5.5 死循环防护机制

与现有系统一致，采用以下防护机制：

```python
# 在分析师节点中添加工具调用计数器
def market_analyst_node(state):
    # 🔧 工具调用计数器 - 防止无限循环
    tool_call_count = state.get("market_tool_call_count", 0)
    max_tool_calls = 1  # 大盘指标一次调用即可
    
    # 如果达到最大调用次数，强制生成报告
    if tool_call_count >= max_tool_calls:
        logger.warning(f"🔧 [死循环防护] 达到最大调用次数，强制生成报告")
        # 基于现有数据生成报告，不再调用工具
        ...
    
    # 正常工具调用逻辑
    ...
    
    return {
        "messages": [result],
        "market_report": report,
        "market_tool_call_count": tool_call_count + 1  # 更新计数器
    }
```

| 分析师 | 最大工具调用次数 | 说明 |
|--------|----------------|------|
| 大盘分析师 | 1 | 一次调用获取所有大盘指标 |
| 板块分析师 | 1 | 一次调用获取所有板块指标 |
| 合力分析师 | 1 | 一次调用获取合力数据 |
| 龙头分析师 | 1 | 一次调用获取龙头数据 |
| 风险分析师 | 1 | 一次调用获取风险数据 |

```
START
    │
    ▼
┌──────────────────┐
│  Market Analyst  │
│   大盘分析师      │
└──────────────────┘
    │
    │ 条件判断：market_sentiment
    │
    ├── 偏空 ─────────────────────► Early Stop Handler ──► END
    │                              │
    │                              │ action: "观望"
    │                              │ reason: "大盘偏空"
    │
    └─ 偏多/中性 ──► 继续
            │
            ▼
┌──────────────────┐
│  Sector Analyst  │
│  主线板块分析师   │
│                  │
│ 技术指标筛选：    │
│ - 涨幅前10板块    │
│ - 涨停统计        │
│ - 强势股池        │
│ - 封板比          │
│ - 炸板率          │
└──────────────────┘
    │
    │ 条件判断：main_sectors数量
    │
    ├── 无主线 ──────────────────► Early Stop Handler ──► END
    │                              │
    │                              │ action: "观望"
    │                              │ reason: "无主线板块"
    │
    ├── 只有1个板块 ─────────────► 跳过辩论，直接确认
    │                              │
    │                              │ confirmed_sectors = main_sectors
    │                              │
    │                              └──────────────────────────┐
    │                                                         │
    └─ 有2-3个板块 ───► 进入辩论                               │
            │                                                │
            ▼                                                │
┌─────────────────────────────────────────────────────────────┐
│                    板块辩论环节                              │
│                                                             │
│  整体辩论：看涨/看跌研究员对所有候选板块综合辩论              │
│                                                             │
│  ┌──────────────────┐     ┌──────────────────┐            │
│  │ Sector Bull      │◄──►│ Sector Bear      │            │
│  │ 板块看涨研究员    │辩论  │ 板块看跌研究员    │            │
│  │                  │     │                  │            │
│  │ 论证：           │     │ 反驳：           │            │
│  │ "贵金属+半导体   │     │ "涨幅已透支、   │            │
│  │  值得追逐"       │     │  炸板率高"      │            │
│  └──────────────────┘     └──────────────────┘            │
│            │                       │                       │
│            └─── 达到轮数上限 ───────┘                       │
│                    │                                       │
│                    ▼                                       │
│            ┌──────────────────┐                            │
│            │   Sector Judge   │                            │
│            │   板块辩论法官    │                            │
│            │                  │                            │
│            │ 综合裁决：        │                            │
│            │ 确认主线=贵金属   │                            │
│            └──────────────────┘                            │
└─────────────────────────────────────────────────────────────┘
    │                                                       │
    │ 条件判断：confirmed_sectors是否为空                     │
    │                                                       │
    ├── 空列表 ─────────────────► Early Stop Handler ──► END │
    │                              │                        │
    │                              │ action: "观望"         │
    │                              │ reason: "辩论无确认主线"│
    │                                                       │
    └─ 有确认板块 ───► 继续 ◄────────────────────────────────┘
            │
            ▼
┌──────────────────┐
│  Force Analyst   │
│  市场合力分析师   │
│                  │
│ 从确认主线板块中  │
│ 找合力股票        │
└──────────────────┘
    │
    │ 条件判断：candidate_stocks数量
    │
    ├── 无合力股票 ─────────────► Early Stop Handler ──► END
    │                              │
    │                              │ action: "观望"
    │                              │ reason: "无合力股票"
    │
    ├── 只有1支股票 ─────────────► 跳过辩论，直接确认
    │                              │
    │                              │ quality_stocks = candidate_stocks
    │                              │
    │                              └──────────────────────────┐
    │                                                         │
    └─ 有2-3支股票 ───► 进入辩论                               │
            │                                                │
            ▼                                                │
┌─────────────────────────────────────────────────────────────┐
│                    股票辩论环节                              │
│                                                             │
│  综合辩论：看涨/看跌研究员对所有候选股票综合辩论              │
│                                                             │
│  ┌──────────────────┐     ┌──────────────────┐            │
│  │ Stock Bull       │◄──►│ Stock Bear       │            │
│  │ 股票看涨研究员    │辩论  │ 股票看跌研究员    │            │
│  │                  │     │                  │            │
│  │ 论证：           │     │ 反驳：           │            │
│  │ "603985+600xxx   │     │ "603985涨幅过大 │            │
│  │  值得追涨"       │     │  PE过高"        │            │
│  └──────────────────┘     └──────────────────┘            │
│            │                       │                       │
│            └─── 达到轮数上限 ───────┘                       │
│                    │                                       │
│                    ▼                                       │
│            ┌──────────────────┐                            │
│            │   Stock Judge    │                            │
│            │   股票辩论法官    │                            │
│            │                  │                            │
│            │ 综合裁决：        │                            │
│            │ 优质标的=603985   │                            │
│            └──────────────────┘                            │
└─────────────────────────────────────────────────────────────┘
    │                                                       │
    │ 条件判断：quality_stocks是否为空                        │
    │                                                       │
    ├── 空 ─────────────────────► Early Stop Handler ──► END │
    │                              │                        │
    │                              │ action: "观望"         │
    │                              │ reason: "辩论无优质标的"│
    │                                                       │
    └─ 有优质标的 ───► 继续 ◄────────────────────────────────┘
            │
            ▼
┌──────────────────┐
│  Leader Analyst  │
│  股票龙头分析师   │
└──────────────────┘
    │
    │ 条件判断：leading_stocks是否为空
    │
    ├── 空 ─────────────────────► Early Stop Handler ──► END
    │                              │
    │                              │ action: "观望"
    │                              │ reason: "无龙头股"
    │
    └─ 有龙头股 ───► 继续
            │
            ▼
┌──────────────────┐
│  Risk Analyst    │
│  风险分析师       │
└──────────────────┘
    │
    │ 条件判断：risk_level 和 safe_stocks
    │
    ├── 高风险或safe_stocks空 ───► Early Stop Handler ──► END
    │                              │
    │                              │ action: "规避"
    │                              │ reason: "高风险"
    │
    └─ 低/中风险 ───► 继续
            │
            ▼
┌──────────────────┐
│ Decision Analyst │
│ 决策分析师       │
└──────────────────┘
    │
    ▼
   END
```

---

## 6. 多阶段辩论机制

### 6.1 设计原则

| 设计项 | 确定方案 |
|-------|---------|
| **板块筛选** | 先用技术指标筛选出2-3个候选板块，再辩论 |
| **板块辩论方式** | **整体辩论**：看涨/看跌研究员对所有候选板块综合辩论 |
| **股票辩论方式** | **综合辩论**：看涨/看跌研究员对所有候选股票综合辩论 |
| **单候选跳过** | 只有1个板块或1支股票时，跳过辩论直接确认 |
| **辩论轮数** | 与现有系统一致，默认1轮（看涨+看跌各发言一次） |
| **不支持跳过** | 用户不可配置跳过辩论环节 |

### 6.2 板块辩论流程

#### 前置筛选（技术指标）

主线板块分析师使用以下技术指标筛选候选板块：

```python
# 板块筛选指标
result["涨幅前10板块"] = stock_board_industry_rank(api_cache)
result["涨停统计"] = stock_zt_pool_stats(api_cache)
result["强势股池统计"] = stock_lb_pool_stats(api_cache)
result["封板比统计"] = stock_seal_ratio(api_cache)
result["炸板统计"] = stock_broken_limit_rate(api_cache, result.get("涨停统计"))
```

筛选逻辑：
- 从涨幅前10板块中，结合涨停集中度、封板比、炸板率等指标
- 筛选出2-3个最具主线特征的候选板块
- 如果筛选结果只有1个板块，跳过辩论直接确认

#### 辩论流程

```
候选板块：贵金属、半导体（假设筛选出2个）
    │
    │ 技术指标数据传入辩论员
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│                    整体辩论                                  │
│                                                             │
│  ┌──────────────────┐                                      │
│  │ 板块看涨研究员    │                                      │
│  │                  │                                      │
│  │ 综合论证：        │                                      │
│  │ "贵金属和半导体   │                                      │
│  │  都值得追逐，理由：│                                      │
│  │  - 资金持续净流入 │                                      │
│  │  - 涨停集中度高   │                                      │
│  │  - 封板比>1，牢固 │                                      │
│  │  - 炸板率低，情绪 │                                      │
│  │    稳定"          │                                      │
│  └──────────────────┘                                      │
│            │                                               │
│            ▼                                               │
│  ┌──────────────────┐                                      │
│  │ 板块看跌研究员    │                                      │
│  │                  │                                      │
│  │ 综合反驳：        │                                      │
│  │ "半导体涨幅已透支 │                                      │
│  │  贵金属虽强但位置 │                                      │
│  │  已高，风险大于   │                                      │
│  │  收益，理由：     │                                      │
│  │  - 半导体炸板率高 │                                      │
│  │  - 贵金属今日高位 │                                      │
│  │    放量，追高风险 │                                      │
│  │  - 连板股多为跟风 │                                      │
│  │    无真龙头"      │                                      │
│  └──────────────────┘                                      │
│            │                                               │
│            ▼                                               │
│  ┌──────────────────┐                                      │
│  │ 板块辩论法官      │                                      │
│  │                  │                                      │
│  │ 综合裁决：        │                                      │
│  │ "基于数据客观评估 │                                      │
│  │  看涨论点更可靠   │                                      │
│  │  但半导体风险较大 │                                      │
│  │  最终确认主线：   │                                      │
│  │  贵金属（值得追逐）│                                      │
│  │  confidence: 0.75 │                                      │
│  │  key_reason: 资金 │                                      │
│  │  流入+封板比高"   │                                      │
│  └──────────────────┘                                      │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
输出：confirmed_sectors = ["贵金属"]
```

#### 跳过辩论条件

```python
# 条件路由逻辑
def should_continue_after_sector(state):
    main_sectors = state.get("main_sectors", [])
    
    if not main_sectors:
        # 无候选板块，提前终止
        return "early_stop"
    
    if len(main_sectors) == 1:
        # 只有1个板块，跳过辩论直接确认
        state["confirmed_sectors"] = main_sectors
        logger.info(f"🔀 [板块判断] 只有1个候选板块，跳过辩论直接确认")
        return "skip_debate"  # 直接进入合力分析
    
    # >=2个板块，进入辩论
    return "enter_debate"
```

### 6.3 股票辩论流程

#### 辩论流程

```
候选股票：603985、600xxx、601xxx（假设合力分析师筛选出3支）
    │
    │ 合力指标数据传入辩论员
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│                    综合辩论                                  │
│                                                             │
│  ┌──────────────────┐                                      │
│  │ 股票看涨研究员    │                                      │
│  │                  │                                      │
│  │ 综合论证：        │                                      │
│  │ "这3支股票综合    │                                      │
│  │  分析值得追涨，   │                                      │
│  │  其中603985最优： │                                      │
│  │  - 主力净流入TOP1 │                                      │
│  │  - 换手率5.2%，量 │                                      │
│  │    价配合         │                                      │
│  │  - 属于贵金属主线 │                                      │
│  │  - 600xxx次优"    │                                      │
│  └──────────────────┘                                      │
│            │                                               │
│            ▼                                               │
│  ┌──────────────────┐                                      │
│  │ 股票看跌研究员    │                                      │
│  │                  │                                      │
│  │ 综合反驳：        │                                      │
│  │ "603985涨幅9.8%   │                                      │
│  │  接近涨停，追高   │                                      │
│  │  风险大；600xxx   │                                      │
│  │  PE>200估值过高； │                                      │
│  │  601xxx流通市值   │                                      │
│  │  <20亿易操控"     │                                      │
│  └──────────────────┘                                      │
│            │                                               │
│            ▼                                               │
│  ┌──────────────────┐                                      │
│  │ 股票辩论法官      │                                      │
│  │                  │                                      │
│  │ 综合裁决：        │                                      │
│  │ "看涨论点数据更   │                                      │
│  │  可靠，但需注意   │                                      │
│  │  风险提示         │                                      │
│  │  最终筛选优质标的 │                                      │
│  │  =603985          │                                      │
│  │  confidence: 0.7  │                                      │
│  │  risk_note: 高位  │                                      │
│  │  追涨T+1风险"     │                                      │
│  └──────────────────┘                                      │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
输出：quality_stocks = [{"code": "603985", "name": "恒润股份", ...}]
```

#### 跳过辩论条件

```python
# 条件路由逻辑
def should_continue_after_force(state):
    candidate_stocks = state.get("candidate_stocks", [])
    
    if not candidate_stocks:
        # 无候选股票，提前终止
        return "early_stop"
    
    if len(candidate_stocks) == 1:
        # 只有1支股票，跳过辩论直接确认
        state["quality_stocks"] = candidate_stocks
        logger.info(f"🔀 [合力判断] 只有1支候选股票，跳过辩论直接确认")
        return "skip_debate"  # 直接进入龙头分析
    
    # >=2支股票，进入辩论
    return "enter_debate"
```

### 6.4 辩论轮数配置

与现有"多Agent股票分析功能"保持一致：

```python
# default_config.py
DEFAULT_CONFIG = {
    ...
    "max_debate_rounds": 1,           # 与现有系统一致
    "max_risk_discuss_rounds": 1,     # 与现有系统一致
    ...
}
```

### 6.5 辩论员设计要点

#### 板块看涨研究员

```python
# 辩论输入
inputs = {
    "candidate_sectors": ["贵金属", "半导体"],  # 前置筛选的候选板块
    "sector_indicators": {...},                  # 板块技术指标数据
    "market_report": "...",                      # 上游大盘分析报告
}

# 辩论任务
# 对所有候选板块进行**综合论证**，说明哪些板块值得追逐
```

#### 板块看跌研究员

```python
# 辩论输入
inputs = {
    "candidate_sectors": ["贵金属", "半导体"],
    "sector_indicators": {...},
    "bull_argument": "...",  # 上游看涨论点（需要反驳）
}

# 辩论任务
# 对候选板块进行**综合反驳**，指出风险和不可持续性
```

#### 股票辩论法官

```python
# 裁决输入
inputs = {
    "debate_history": "看涨论点 + 看跌论点 + 反驳...",
    "sector_indicators": {...},
    "candidate_sectors": [...],
}

# 裁决任务
# 综合评估双方论点，基于数据客观裁决，输出确认的主线板块
# 输出格式：
{
    "confirmed_sectors": ["贵金属"],
    "decision_reasoning": "...",
    "confidence": 0.75,
}
```

---

## 7. 条件路由逻辑

### 7.1 设计原则：检测tool_calls判断工具执行

**核心逻辑**：与现有系统保持一致，分析师节点的条件路由需要检测`tool_calls`属性：

```python
# 条件路由判断逻辑（参考 tradingagents/graph/conditional_logic.py）
def should_continue_xxx(state):
    messages = state["messages"]
    last_message = messages[-1]
    
    # 🔧 工具调用计数器 - 防止死循环
    tool_call_count = state.get("xxx_tool_call_count", 0)
    max_tool_calls = 1  # 一次工具调用即可
    
    # ✅ 优先级1：如果已有报告内容，分析完成
    report = state.get("xxx_report", "")
    if report and len(report) > 100:
        return "Msg Clear Xxx"  # 清理消息，继续下一节点
    
    # ✅ 优先级2：检测tool_calls，判断是否执行工具
    if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
        if tool_call_count >= max_tool_calls:
            logger.warning(f"达到最大工具调用次数，强制结束")
            return "Msg Clear Xxx"
        return "tools_xxx"  # 执行工具
    
    # ✅ 优先级3：无tool_calls，正常结束
    return "Msg Clear Xxx"
```

### 7.2 分析师工具调用判断（新增）

```python
# graph/selector/conditional_logic.py

from tradingagents.agents.selector.utils.agent_states import SelectorState
from langchain_core.messages import ToolMessage

from tradingagents.utils.logging_init import get_logger
logger = get_logger("default")


class SelectorConditionalLogic:
    """AI选股条件路由逻辑（含tool_calls检测）"""
    
    def __init__(self, config):
        self.max_sector_debate_rounds = config.get("max_sector_debate_rounds", 1)
        self.max_stock_debate_rounds = config.get("max_stock_debate_rounds", 1)
    
    # ===== 工具调用检测函数（与现有系统一致）=====
    
    def should_continue_market(self, state: SelectorState) -> str:
        """大盘分析师工具调用判断"""
        messages = state["messages"]
        last_message = messages[-1]
        
        # 🔧 死循环防护：工具调用计数器
        tool_call_count = state.get("market_tool_call_count", 0)
        max_tool_calls = 1  # 大盘指标一次调用即可
        
        # 检查是否已有分析报告
        market_report = state.get("market_report", "")
        
        logger.info(f"🔀 [条件判断] should_continue_market")
        logger.info(f"🔀 [条件判断] - 报告长度: {len(market_report)}")
        logger.info(f"🔧 [死循环防护] - 工具调用次数: {tool_call_count}/{max_tool_calls}")
        
        # ✅ 优先级1：已有完整报告，结束工具调用循环
        if market_report and len(market_report) > 100:
            logger.info(f"🔀 [条件判断] ✅ 报告已完成 → Msg Clear Market")
            return "Msg Clear Market"
        
        # ✅ 优先级2：检测tool_calls，执行工具
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            if tool_call_count >= max_tool_calls:
                logger.warning(f"🔧 [死循环防护] 达到最大调用次数，强制结束")
                return "Msg Clear Market"
            logger.info(f"🔀 [条件判断] 🔧 检测到tool_calls → tools_market")
            return "tools_market"
        
        # ✅ 优先级3：无tool_calls，正常结束
        logger.info(f"🔀 [条件判断] ✅ 无tool_calls → Msg Clear Market")
        return "Msg Clear Market"
    
    def should_continue_sector(self, state: SelectorState) -> str:
        """板块分析师工具调用判断"""
        messages = state["messages"]
        last_message = messages[-1]
        
        tool_call_count = state.get("sector_tool_call_count", 0)
        max_tool_calls = 1
        sector_report = state.get("sector_report", "")
        
        logger.info(f"🔀 [条件判断] should_continue_sector")
        logger.info(f"🔧 [死循环防护] - 工具调用次数: {tool_call_count}/{max_tool_calls}")
        
        # 已有报告，结束循环
        if sector_report and len(sector_report) > 100:
            return "Msg Clear Sector"
        
        # 有tool_calls，执行工具
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            if tool_call_count >= max_tool_calls:
                return "Msg Clear Sector"
            return "tools_sector"
        
        return "Msg Clear Sector"
    
    def should_continue_force(self, state: SelectorState) -> str:
        """合力分析师工具调用判断"""
        messages = state["messages"]
        last_message = messages[-1]
        
        tool_call_count = state.get("force_tool_call_count", 0)
        max_tool_calls = 1
        force_report = state.get("force_report", "")
        
        if force_report and len(force_report) > 100:
            return "Msg Clear Force"
        
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            if tool_call_count >= max_tool_calls:
                return "Msg Clear Force"
            return "tools_force"
        
        return "Msg Clear Force"
    
    def should_continue_leader(self, state: SelectorState) -> str:
        """龙头分析师工具调用判断"""
        messages = state["messages"]
        last_message = messages[-1]
        
        tool_call_count = state.get("leader_tool_call_count", 0)
        max_tool_calls = 1
        leader_report = state.get("leader_report", "")
        
        if leader_report and len(leader_report) > 100:
            return "Msg Clear Leader"
        
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            if tool_call_count >= max_tool_calls:
                return "Msg Clear Leader"
            return "tools_leader"
        
        return "Msg Clear Leader"
    
    def should_continue_risk(self, state: SelectorState) -> str:
        """风险分析师工具调用判断"""
        messages = state["messages"]
        last_message = messages[-1]
        
        tool_call_count = state.get("risk_tool_call_count", 0)
        max_tool_calls = 1
        risk_report = state.get("risk_report", "")
        
        if risk_report and len(risk_report) > 100:
            return "Msg Clear Risk"
        
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            if tool_call_count >= max_tool_calls:
                return "Msg Clear Risk"
            return "tools_risk"
        
        return "Msg Clear Risk"
    
    # ===== 分析师后业务判断（清理消息后执行）=====
    
    def should_continue_after_market(self, state: SelectorState) -> str:
        """大盘分析后的业务路由"""
        market_sentiment = state.get("market_sentiment", "")
        
        if market_sentiment == "偏空":
            logger.info(f"🔀 [大盘判断] 偏空 → 提前终止")
            return "early_stop"
        
        logger.info(f"🔀 [大盘判断] {market_sentiment} → 继续板块分析")
        return "continue"
    
    def should_continue_after_sector(self, state: SelectorState) -> str:
        """板块分析后的业务路由"""
        has_main_sector = state.get("has_main_sector", False)
        main_sectors = state.get("main_sectors", [])
        
        if not has_main_sector or not main_sectors:
            logger.info(f"🔀 [板块判断] 无主线板块 → 提前终止")
            return "early_stop"
        
        if len(main_sectors) == 1:
            # 只有1个板块，跳过辩论直接确认
            state["confirmed_sectors"] = main_sectors
            logger.info(f"🔀 [板块判断] 只有1个候选板块，跳过辩论")
            return "skip_debate"
        
        logger.info(f"🔀 [板块判断] 主线板块: {main_sectors} → 进入板块辩论")
        return "enter_debate"
    
    def should_continue_after_sector_judge(self, state: SelectorState) -> str:
        """板块辩论法官裁决后的路由"""
        confirmed_sectors = state.get("confirmed_sectors", [])
        
        if not confirmed_sectors:
            logger.info(f"🔀 [板块辩论判断] 无确认主线 → 提前终止")
            return "early_stop"
        
        logger.info(f"🔀 [板块辩论判断] 确认主线: {confirmed_sectors} → 继续合力分析")
        return "continue"
    
    def should_continue_after_force(self, state: SelectorState) -> str:
        """合力分析后的业务路由"""
        candidate_stocks = state.get("candidate_stocks", [])
        
        if not candidate_stocks:
            logger.info(f"🔀 [合力判断] 无候选股票 → 提前终止")
            return "early_stop"
        
        if len(candidate_stocks) == 1:
            # 只有1支股票，跳过辩论直接确认
            state["quality_stocks"] = candidate_stocks
            logger.info(f"🔀 [合力判断] 只有1支候选股票，跳过辩论")
            return "skip_debate"
        
        logger.info(f"🔀 [合力判断] 候选股票: {[s['code'] for s in candidate_stocks]} → 进入股票辩论")
        return "enter_debate"
    
    def should_continue_after_stock_judge(self, state: SelectorState) -> str:
        """股票辩论法官裁决后的路由"""
        quality_stocks = state.get("quality_stocks", [])
        
        if not quality_stocks:
            logger.info(f"🔀 [股票辩论判断] 无优质标的 → 提前终止")
            return "early_stop"
        
        logger.info(f"🔀 [股票辩论判断] 优质标的: {[s['code'] for s in quality_stocks]} → 继续龙头分析")
        return "continue"
    
    def should_continue_after_leader(self, state: SelectorState) -> str:
        """龙头分析后的路由"""
        leading_stocks = state.get("leading_stocks", [])
        
        if not leading_stocks:
            logger.info(f"🔀 [龙头判断] 无龙头股 → 提前终止")
            return "early_stop"
        
        logger.info(f"🔀 [龙头判断] 龙头股: {[s['code'] for s in leading_stocks]} → 继续风险分析")
        return "continue"
    
    def should_continue_after_risk(self, state: SelectorState) -> str:
        """风险分析后的路由"""
        risk_level = state.get("risk_level", "中")
        safe_stocks = state.get("safe_stocks", [])
        
        if risk_level == "高" or not safe_stocks:
            logger.info(f"🔀 [风险判断] 高风险或无安全标的 → 提前终止")
            return "early_stop"
        
        logger.info(f"🔀 [风险判断] 风险等级: {risk_level}, 安全标的: {[s['code'] for s in safe_stocks]} → 继续决策")
        return "continue"
    
    # ===== 辩论条件判断 =====
    
    def should_continue_sector_debate(self, state: SelectorState) -> str:
        """板块辩论是否继续"""
        sector_debate_state = state.get("sector_debate_state", {})
        count = sector_debate_state.get("count", 0)
        max_count = 2 * self.max_sector_debate_rounds
        current_speaker = sector_debate_state.get("current_response", "")
        
        if count >= max_count:
            logger.info(f"🔀 [板块辩论] 达到轮数上限 → 进入法官裁决")
            return "end"
        
        next_speaker = "Sector Bear Debater" if current_speaker.startswith("Sector Bull") else "Sector Bull Debater"
        logger.info(f"🔀 [板块辩论] 继续 → {next_speaker}")
        return "continue"
    
    def should_continue_stock_debate(self, state: SelectorState) -> str:
        """股票辩论是否继续"""
        stock_debate_state = state.get("stock_debate_state", {})
        count = stock_debate_state.get("count", 0)
        max_count = 2 * self.max_stock_debate_rounds
        current_speaker = stock_debate_state.get("current_response", "")
        
        if count >= max_count:
            logger.info(f"🔀 [股票辩论] 当前股票辩论完成 → 进入法官裁决")
            return "end"
        
        next_speaker = "Stock Bear Debater" if current_speaker.startswith("Stock Bull") else "Stock Bull Debater"
        logger.info(f"🔀 [股票辩论] 继续 → {next_speaker}")
        return "continue"
```

### 7.3 条件路由流程图

```
┌────────────────────────────────────────────────────────────────────────┐
│                        条件路由判断流程                                  │
│                                                                        │
│  Analyst Node                                                          │
│      │                                                                 │
│      │ LLM.invoke(bind_tools)                                         │
│      ▼                                                                 │
│  AIMessage                                                             │
│      │                                                                 │
│      ├─ 检查 tool_calls                                                │
│      │                                                                 │
│      ├─ 有 tool_calls ───► tools_xxx（ToolNode）                       │
│      │                           │                                    │
│      │                           │ ToolNode.invoke()                  │
│      │                           │                                    │
│      │                           └────► 返回 Analyst Node             │
│      │                                    │                           │
│      │                                    │ LLM基于数据生成报告        │
│      │                                    │                           │
│      └───── 无 tool_calls ───► should_continue_xxx                    │
│                                   │                                    │
│                                   ├─ 报告已完成 ───► Msg Clear Xxx     │
│                                   │                        │         │
│                                   │                        ▼         │
│                                   │              should_continue_after │
│                                   │                        │         │
│                                   │                        ├─ early_stop│
│                                   │                        ├─ continue │
│                                   │                        ├─ skip_debate│
│                                   │                        └─ enter_debate│
│                                   │                                    │
│                                   └─ 工具调用超限 ───► Msg Clear Xxx   │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

### 7.4 消息清理机制

与现有系统一致，使用`create_msg_delete()`清理消息历史：

```python
# agents/utils/agent_utils.py

def create_msg_delete():
    def delete_messages(state):
        """清理消息历史，添加占位符消息（Anthropic兼容性）"""
        messages = state["messages"]
        
        # 移除所有消息
        removal_operations = [RemoveMessage(id=m.id) for m in messages]
        
        # 添加最小占位符消息
        placeholder = HumanMessage(content="Continue")
        
        return {"messages": removal_operations + [placeholder]}
    
    return delete_messages
```

---

## 8. 工具与数据源

### 8.1 设计原则：使用@tool装饰器定义工具

**核心设计**：与现有"AI股票分析功能"保持一致，使用LangGraph的`@tool`装饰器定义工具，LLM通过`bind_tools()`自主调用。

| 对比项 | ❌ 原设计 | ✅ 新设计（与现有系统一致） |
|-------|---------|------------------------|
| **工具定义方式** | 普通Python函数 | `@tool`装饰器定义 |
| **工具参数注解** | 无类型注解 | `Annotated[type, "description"]` |
| **工具绑定** | 无绑定机制 | `llm.bind_tools([tool1, tool2])` |
| **工具执行** | 直接调用函数 | LangGraph的`ToolNode`执行 |
| **返回格式** | Dict对象 | 格式化的字符串报告 |

### 8.2 SelectorToolkit工具类（@tool装饰器）

```python
# agents/selector/utils/toolkit.py

from langchain_core.tools import tool
from typing import Annotated, Dict, List
from datetime import date
import tradingagents.dataflows.interface as interface

from tradingagents.utils.logging_init import get_logger
logger = get_logger("default")


class SelectorToolkit:
    """AI选股工具包（使用@tool装饰器，与现有Toolkit设计一致）"""
    
    def __init__(self, api_cache):
        self.api_cache = api_cache
    
    @staticmethod
    @tool
    def get_market_indicators(
        curr_date: Annotated[str, "当前分析日期，格式 YYYY-MM-DD"]
    ) -> str:
        """
        获取A股大盘指标数据，包括主要指数行情、北向资金流向、涨跌家数统计。
        
        Args:
            curr_date: 当前分析日期（格式：YYYY-MM-DD）
        
        Returns:
            str: 格式化的大盘指标报告，包含：
                 - 上证指数、深证成指、创业板指的实时行情
                 - 北向资金净流入统计
                 - 涨跌家数比例
                 - 市场情绪评估
        """
        logger.info(f"📊 [大盘指标工具] 获取大盘数据: {curr_date}")
        
        try:
            # 获取指数数据
            index_data = interface.stock_zh_index_daily(curr_date)
            
            # 获取北向资金数据
            fund_flow = interface.stock_hsgt_fund_flow_summary(curr_date)
            
            # 获取涨跌家数
            up_down_count = interface.stock_up_down_count(curr_date)
            
            # 格式化报告
            report = f"""# A股大盘指标分析报告

**分析日期**: {curr_date}

## 📈 主要指数行情

{index_data}

## 💰 北向资金流向

{fund_flow}

## 📊 涨跌家数统计

{up_down_count}

## 📝 市场情绪评估

基于上述数据，请综合分析大盘环境，给出市场情绪判断（偏多/偏空/中性）。

---
*数据来源: AKShare实时数据*
"""
            
            logger.info(f"✅ [大盘指标工具] 数据获取成功，报告长度: {len(report)}")
            return report
            
        except Exception as e:
            error_msg = f"大盘指标获取失败: {str(e)}"
            logger.error(f"❌ [大盘指标工具] {error_msg}")
            return error_msg
    
    @staticmethod
    @tool
    def get_sector_indicators(
        curr_date: Annotated[str, "当前分析日期，格式 YYYY-MM-DD"]
    ) -> str:
        """
        获取A股板块指标数据，包括涨幅排名、涨停统计、强势股池、封板比、炸板率。
        
        用于识别主线板块，筛选2-3个候选板块进入辩论环节。
        
        Args:
            curr_date: 当前分析日期（格式：YYYY-MM-DD）
        
        Returns:
            str: 格式化的板块指标报告，包含：
                 - 涨幅前10板块排名
                 - 涨停股池统计（涨停数量、连板高度）
                 - 强势股池统计
                 - 封板比统计（主力锁仓意愿指标）
                 - 炸板率统计（情绪稳定性指标）
        """
        logger.info(f"📊 [板块指标工具] 获取板块数据: {curr_date}")
        
        try:
            # 获取板块涨幅排名
            sector_rank = interface.stock_board_industry_rank(curr_date)
            
            # 获取涨停统计
            zt_stats = interface.stock_zt_pool_stats(curr_date)
            
            # 获取强势股池统计
            lb_stats = interface.stock_lb_pool_stats(curr_date)
            
            # 获取封板比统计
            seal_ratio = interface.stock_seal_ratio(curr_date)
            
            # 获取炸板率统计
            broken_rate = interface.stock_broken_limit_rate(curr_date, zt_stats)
            
            # 格式化报告
            report = f"""# A股板块指标分析报告

**分析日期**: {curr_date}

## 🔥 涨幅前10板块

{sector_rank}

## 📈 涨停统计

{zt_stats}

## 💪 强势股池统计

{lb_stats}

## 📊 封板比统计

{seal_ratio}

## ⚠️ 炸板率统计

{broken_rate}

## 📝 板块筛选建议

基于上述数据，请综合分析：
1. 哪些板块具有主线特征（涨幅领先 + 涨停集中 + 封板比高 + 炸板率低）
2. 筛选出2-3个候选板块进入辩论环节
3. 如果只有1个板块符合条件，可直接确认，跳过辩论

**关键指标参考**:
- 封板比 > 1: 主力锁仓意愿强，极牢固
- 炸板率 < 10%: 市场惜售，情绪稳定
- 炸板率 ≥ 25%: 情绪偏弱
- 炸板率 ≥ 40%: 极度不稳，风险大

---
*数据来源: AKShare实时数据*
"""
            
            logger.info(f"✅ [板块指标工具] 数据获取成功，报告长度: {len(report)}")
            return report
            
        except Exception as e:
            error_msg = f"板块指标获取失败: {str(e)}"
            logger.error(f"❌ [板块指标工具] {error_msg}")
            return error_msg
    
    @staticmethod
    @tool
    def get_force_indicators(
        curr_date: Annotated[str, "当前分析日期，格式 YYYY-MM-DD"],
        confirmed_sectors: Annotated[List[str], "确认的主线板块列表"]
    ) -> str:
        """
        获取市场合力指标数据，从确认主线板块中筛选合力股票。
        
        合力股票特征：主力净流入排名靠前、换手率适中、属于主线板块。
        
        Args:
            curr_date: 当前分析日期（格式：YYYY-MM-DD）
            confirmed_sectors: 确认的主线板块列表（如：["贵金属", "半导体"]）
        
        Returns:
            str: 格式化的合力指标报告，包含：
                 - 主线板块个股资金流向
                 - 主力净流入排名TOP10
                 - 候选合力股票列表（2-3支）
                 - 合力方向判断（正向共振/反向分歧/主力主导）
        """
        logger.info(f"📊 [合力指标工具] 获取合力数据: {curr_date}, 主线板块: {confirmed_sectors}")
        
        try:
            # 获取行业资金流向
            industry_flow = interface.stock_industry_fund_flow(curr_date, confirmed_sectors)
            
            # 获取个股资金流向
            individual_flow = interface.stock_individual_fund_flow(curr_date)
            
            # 格式化报告
            report = f"""# 市场合力指标分析报告

**分析日期**: {curr_date}
**主线板块**: {confirmed_sectors}

## 💰 板块资金流向

{industry_flow}

## 📊 个股资金流向（TOP20）

{individual_flow}

## 📝 合力股票筛选建议

基于上述数据，请从主线板块中筛选合力股票：
1. 主力净流入排名靠前（TOP10）
2. 换手率适中（3%-10%为佳）
3. 属于确认的主线板块
4. 筛选出2-3支候选股票进入辩论环节
5. 如果只有1支股票符合条件，可直接确认，跳过辩论

**合力方向判断**:
- 正向共振：主力+散户同向流入
- 反向分歧：主力流入+散户流出（或相反）
- 主力主导：主力大幅流入，散户观望

---
*数据来源: AKShare实时数据*
"""
            
            logger.info(f"✅ [合力指标工具] 数据获取成功，报告长度: {len(report)}")
            return report
            
        except Exception as e:
            error_msg = f"合力指标获取失败: {str(e)}"
            logger.error(f"❌ [合力指标工具] {error_msg}")
            return error_msg
    
    @staticmethod
    @tool
    def get_leader_indicators(
        curr_date: Annotated[str, "当前分析日期，格式 YYYY-MM-DD"],
        quality_stocks: Annotated[List[Dict], "优质标的股票列表"]
    ) -> str:
        """
        获取龙头指标数据，从优质标的中筛选龙头股。
        
        龙头股特征：连板高度最高、板块内排名靠前、成交量放大。
        
        Args:
            curr_date: 当前分析日期（格式：YYYY-MM-DD）
            quality_stocks: 优质标的股票列表（包含code、name字段）
        
        Returns:
            str: 格式化的龙头指标报告，包含：
                 - 连板统计（涨停股连板高度）
                 - 板块内排名
                 - 龙头股筛选结果（1-2支）
        """
        logger.info(f"📊 [龙头指标工具] 获取龙头数据: {curr_date}")
        
        try:
            # 获取涨停股连板统计
            zt_pool_leader = interface.stock_zt_pool_leader(curr_date)
            
            # 获取强势股排名
            strong_rank = interface.stock_strong_rank(curr_date, quality_stocks)
            
            # 格式化报告
            stock_codes = [s.get('code', '') for s in quality_stocks]
            report = f"""# 股票龙头指标分析报告

**分析日期**: {curr_date}
**优质标的**: {stock_codes}

## 🔥 涨停股连板统计

{zt_pool_leader}

## 📊 强势股排名

{strong_rank}

## 📝 龙头股筛选建议

基于上述数据，请从优质标的中筛选龙头股：
1. 连板高度最高（优先选择3连板以上）
2. 板块内排名靠前
3. 成交量放大（换手率5%-15%为佳）
4. 筛选出1-2支龙头股

---
*数据来源: AKShare实时数据*
"""
            
            logger.info(f"✅ [龙头指标工具] 数据获取成功，报告长度: {len(report)}")
            return report
            
        except Exception as e:
            error_msg = f"龙头指标获取失败: {str(e)}"
            logger.error(f"❌ [龙头指标工具] {error_msg}")
            return error_msg
    
    @staticmethod
    @tool
    def get_risk_indicators(
        curr_date: Annotated[str, "当前分析日期，格式 YYYY-MM-DD"],
        leading_stocks: Annotated[List[Dict], "龙头股列表"]
    ) -> str:
        """
        获取风险指标数据，对龙头股进行风险评估。
        
        风险指标：ST状态、新股上市时间、退市风险、财务状况。
        
        Args:
            curr_date: 当前分析日期（格式：YYYY-MM-DD）
            leading_stocks: 龙头股列表（包含code、name字段）
        
        Returns:
            str: 格式化的风险指标报告，包含：
                 - ST股票排除
                 - 新股过滤（上市不足30天）
                 - 退市风险评估
                 - 财务状况分析
                 - 安全标的列表
                 - 风险等级评估（低/中/高）
        """
        logger.info(f"📊 [风险指标工具] 获取风险数据: {curr_date}")
        
        try:
            # 获取新股列表（排除上市不足30天）
            new_list = interface.stock_new_list(curr_date)
            
            # 获取候选股票基本面
            stock_codes = [s.get('code', '') for s in leading_stocks]
            fundamentals = interface.stock_candidate_fundamentals(stock_codes)
            
            # 格式化报告
            report = f"""# 风险指标分析报告

**分析日期**: {curr_date}
**待评估股票**: {stock_codes}

## 📋 新股列表（需排除上市不足30天）

{new_list}

## 📊 基本面分析

{fundamentals}

## 📝 风险评估建议

基于上述数据，请对龙头股进行风险评估：
1. 排除ST股票
2. 排除上市不足30天的新股
3. 排除有退市风险的股票
4. 评估财务状况（PE、PB、负债率）
5. 给出风险等级（低/中/高）
6. 输出安全标的列表

**风险等级判定**:
- 低风险：无ST、无退市风险、财务健康
- 中风险：存在一定财务压力，但无重大风险
- 高风险：存在ST、退市风险、财务恶化

---
*数据来源: AKShare实时数据*
"""
            
            logger.info(f"✅ [风险指标工具] 数据获取成功，报告长度: {len(report)}")
            return report
            
        except Exception as e:
            error_msg = f"风险指标获取失败: {str(e)}"
            logger.error(f"❌ [风险指标工具] {error_msg}")
            return error_msg
```

### 8.3 指标计算模块（数据底层）

```
tradingagents/
└── dataflows/
    └── indicators/
        ├── __init__.py
        ├── market_indicators.py      # 大盘指标
        │   ├── stock_zh_index_daily()      # 指数行情
        │   ├── stock_hsgt_fund_flow()      # 北向资金
        │   └── stock_up_down_count()       # 涨跌家数
        │
        ├── sector_indicators.py      # 板块指标
        │   ├── stock_board_industry_rank()  # 板块涨幅排名
        │   ├── stock_zt_pool_stats()        # 涨停统计
        │   ├── stock_lb_pool_stats()        # 强势股池
        │   ├── stock_seal_ratio()           # 封板比
        │   └── stock_broken_limit_rate()    # 炸板率
        │
        ├── force_indicators.py       # 合力指标
        │   ├── stock_industry_fund_flow()   # 板块资金流向
        │   └── stock_individual_fund_flow() # 个股资金流向
        │
        ├── leader_indicators.py      # 龙头指标
        │   ├── stock_zt_pool_leader()       # 涨停股连板统计
        │   └── stock_strong_rank()          # 强势股排名
        │
        └── risk_indicators.py        # 风险指标
        │   ├── stock_new_list()              # 新股列表
        │   ├── stock_candidate_spot()        # 基本面快照
        │   └── stock_candidate_fundamentals() # 基本面详细
        │
        └── providers/
            └── akshare_provider.py   # AKShare数据源
```

### 8.4 工具与分析师对应关系

| 分析师 | 工具名称 | 工具参数 | 输出State字段 |
|--------|---------|---------|--------------|
| 大盘分析师 | `get_market_indicators` | `curr_date` | `market_report`, `market_sentiment` |
| 板块分析师 | `get_sector_indicators` | `curr_date` | `sector_report`, `main_sectors` |
| 合力分析师 | `get_force_indicators` | `curr_date`, `confirmed_sectors` | `force_report`, `candidate_stocks` |
| 龙头分析师 | `get_leader_indicators` | `curr_date`, `quality_stocks` | `leader_report`, `leading_stocks` |
| 风险分析师 | `get_risk_indicators` | `curr_date`, `leading_stocks` | `risk_report`, `safe_stocks` |
| 决策分析师 | 无工具 | 直接LLM调用 | `decision_report`, `final_decision` |

---

## 9. 与现有系统的关系

### 9.1 代码复用策略

| 复用项 | 来源 | 复用方式 |
|-------|------|---------|
| **LLM适配器** | `tradingagents/llm_adapters/` | 直接复用，无需修改 |
| **配置管理** | `tradingagents/config/` | 复用 `create_analysis_config` |
| **记忆系统** | `tradingagents/agents/utils/memory.py` | 可选复用（辩论员可选记忆） |
| **日志系统** | `tradingagents/utils/logging_init.py` | 直接复用 |
| **API缓存** | `app/utils/api_cache.py` | 直接复用 |
| **JSON压缩** | `app/utils/json_compressor.py` | 直接复用 |
| **结构化提取** | `app/utils/stock_utils.py` | 扩展新的提取函数 |

### 9.2 独立模块

| 新模块 | 路径 | 说明 |
|-------|------|------|
| **AI选股Graph** | `tradingagents/graph/selector/` | 新建，独立编排 |
| **选股Agent** | `tradingagents/agents/selector/` | 新建，独立定义 |
| **选股State** | `tradingagents/agents/selector/utils/agent_states.py` | 新建 |
| **选股Toolkit** | `tradingagents/agents/selector/utils/toolkit.py` | 新建 |
| **指标计算** | `tradingagents/dataflows/indicators/` | 从 `app/services/ai_selector/` 迁移重构 |

### 9.3 共存架构

```
tradingagents/
│
├── agents/
│   ├── analysts/          # 股票分析的分析师（已有）
│   ├── researchers/       # 股票分析的研究员（已有）
│   ├── managers/          # 股票分析的经理（已有）
│   ├── risk_mgmt/         # 股票分析的风险团队（已有）
│   ├── trader/            # 股票分析的交易员（已有）
│   ├── utils/             # 共用工具
│   │
│   └── selector/          # AI选股的Agent（新增）
│       ├── analysts/
│       │   ├── market_analyst.py
│       │   ├── sector_analyst.py
│       │   ├── force_analyst.py
│       │   ├── leader_analyst.py
│       │   ├── risk_analyst.py
│       │   └── decision_analyst.py
│       │
│       ├── debaters/
│       │   ├── sector_bull_researcher.py
│       │   ├── sector_bear_researcher.py
│       │   ├── sector_judge.py
│       │   ├── stock_bull_researcher.py
│       │   ├── stock_bear_researcher.py
│       │   └── stock_judge.py
│       │
│       └── utils/
│           ├── agent_states.py
│           └── toolkit.py
│
├── graph/
│   ├── trading_graph.py   # 股票分析Graph（已有）
│   ├── setup.py           # 股票分析Graph构建（已有）
│   ├── conditional_logic.py
│   │
│   └── selector/          # AI选股Graph（新增）
│       ├── selector_graph.py
│       ├── setup.py
│       ├── conditional_logic.py
│       ├── propagation.py
│       └── signal_processing.py
│
├── dataflows/
│   ├── interface.py       # 股票数据接口（已有）
│   ├── providers/         # 数据提供商（已有）
│   │
│   └── indicators/        # 选股指标计算（新增/迁移）
│       ├── market_indicators.py
│       ├── sector_indicators.py
│       ├── force_indicators.py
│       ├── leader_indicators.py
│       └ risk_indicators.py
```

---

## 10. 文件结构规划

### 10.1 新建文件清单

```
tradingagents/
│
├── agents/selector/
│   ├── __init__.py
│   ├── analysts/
│   │   ├── __init__.py
│   │   ├── market_analyst.py        # 大盘分析师
│   │   ├── sector_analyst.py        # 主线板块分析师
│   │   ├── force_analyst.py         # 市场合力分析师
│   │   ├── leader_analyst.py        # 股票龙头分析师
│   │   ├── risk_analyst.py          # 风险分析师
│   │   └── decision_analyst.py      # 决策分析师
│   │
│   ├── debaters/
│   │   ├── __init__.py
│   │   ├── sector_bull_researcher.py   # 板块看涨研究员
│   │   ├── sector_bear_researcher.py   # 板块看跌研究员
│   │   ├── sector_judge.py             # 板块辩论法官
│   │   ├── stock_bull_researcher.py    # 股票看涨研究员
│   │   ├── stock_bear_researcher.py    # 股票看跌研究员
│   │   └── stock_judge.py              # 股票辩论法官
│   │
│   └ utils/
│       ├── __init__.py
│       ├── agent_states.py          # 状态定义
│       ├── toolkit.py               # 工具包
│       └── prompts.py               # 提示词模板
│
├── graph/selector/
│   ├── __init__.py
│   ├── selector_graph.py            # 主编排器
│   ├── setup.py                     # 图构建
│   ├── conditional_logic.py         # 条件路由
│   ├── propagation.py               # 执行传播
│   └── signal_processing.py         # 信号处理
│
├── dataflows/indicators/
│   ├── __init__.py
│   ├── market_indicators.py         # 大盘指标
│   ├── sector_indicators.py         # 板块指标
│   ├── force_indicators.py          # 合力指标
│   ├── leader_indicators.py         # 龙头指标
│   └── risk_indicators.py           # 风险指标
│
└── default_config.py                # 扩展配置项
```

### 10.2 迁移/重构文件

| 原文件 | 新文件 | 操作 |
|-------|-------|------|
| `app/services/ai_selector/ai_selector_service.py` | `tradingagents/graph/selector/selector_graph.py` | 重构为LangGraph风格 |
| `app/services/ai_selector/compute_indicators.py` | `tradingagents/dataflows/indicators/*.py` | 拆分为模块化结构 |

---

## 11. 已确认与待讨论问题

### 11.1 已确认问题

| 问题编号 | 问题 | 确认结果 |
|---------|------|---------|
| Q1 | 板块辩论方式 | ✅ **整体辩论**：看涨/看跌研究员对所有候选板块综合辩论，法官综合裁决 |
| Q2 | 股票辩论方式 | ✅ **综合辩论**：看涨/看跌研究员对所有候选股票综合辩论，法官筛选优质标的 |
| Q3 | 单候选跳过辩论 | ✅ 只有1个板块或1支股票时，跳过辩论直接确认 |
| Q4 | 辩论轮数 | ✅ 与现有系统一致，默认1轮 |
| Q5 | 跳过辩论配置 | ✅ **不支持**，用户不可配置跳过辩论环节 |
| Q6 | 进度展示 | ✅ 与现有系统一致 |
| Q7 | 辩论员记忆系统 | ✅ 与现有系统一致（不使用ChromaDB） |
| Q8 | 提前终止输出 | ✅ 需要输出已完成的分析师报告 |
| Q9 | 前端接口确认 | ✅ 已查看前端代码，接口格式已确认 |

### 11.2 前端代码分析总结

**前端分析师团队定义（index.vue）**：
```
1. 大盘分析师 📈 - 指数分析、北向资金、涨跌比
2. 主线板块分析师 🔥 - 涨停集中度、5日强度、资金流向
3. 市场合力分析师 💪 - 主力净流入、散户净流入、双向资金
4. 股票龙头分析师 👑 - 连板分析、板块排名、成交量
5. 多空研究员 ⚖️ - 看多论证、看空质疑、辩论评判（新增）
6. 风险分析师 🛡️ - ST排除、新股过滤、退市风险
7. 决策分析师 🎯 - 综合决策、标的推荐、风险评级
```

**前端辩论轮次配置**：
- 支持 0-3 轮辩论配置
- 0 表示跳过辩论（前端已支持此配置）

**前端结果展示格式**：
- analyst_results: 各分析师结论（Tab形式展示）
- decision: 综合决策（包含action、stocks、reasoning、position_suggestion、risk_warning）
- early_stop: 提前终止标识
- early_stop_reason: 提前终止原因
- debate_rounds: 辩论轮次结果（可选）

---

## 附录A：提示词模板示例

### A.1 板块看涨研究员提示词（整体辩论版）

```python
SECTOR_BULL_PROMPT = """
你是一位资深A股板块策略分析师，负责论证候选板块值得追逐的观点。

# 候选板块列表（已通过技术指标筛选）
{candidate_sectors}

# 板块指标数据
{sector_indicators}

# 上游大盘分析结论
{market_report}

# 你的任务
基于上述数据，对所有候选板块进行**综合论证**，说明哪些板块值得追逐。

# 论证要点
1. **板块筛选依据**：说明这些板块是如何通过涨幅、涨停集中度、封板比等指标筛选出来的
2. **持续强度分析**：分析5日/10日涨跌幅，判断是启动还是持续
3. **资金真实性**：分析封板比（>1为极牢固）、炸板率（<10%为情绪强）
4. **涨停质量**：涨停股数量、连板高度、龙头股特征
5. **综合结论**：哪些板块值得追逐？按优先级排序

# 上游看跌论点（需要反驳）
{bear_argument}

# 输出格式（严格遵守JSON格式）
```json
{
  "sectors_analysis": [
    {
      "sector": "板块名称",
      "worth_chasing": true/false,
      "priority": 1/2/3,
      "argument": "看涨论点（含具体数据依据，150字以内）",
      "key_data": ["数据依据1", "数据依据2"]
    }
  ],
  "overall_argument": "整体看涨论点（对值得追逐的板块进行综合论证，200字以内）",
  "confidence": 0.8
}
```

请使用中文回答。
"""
```

### A.2 板块看跌研究员提示词（整体辩论版）

```python
SECTOR_BEAR_PROMPT = """
你是一位资深A股板块策略分析师，负责论证候选板块不值得追逐的观点。

# 候选板块列表（已通过技术指标筛选）
{candidate_sectors}

# 板块指标数据
{sector_indicators}

# 上游大盘分析结论
{market_report}

# 你的任务
基于上述数据，对所有候选板块进行**综合反驳**，指出哪些板块不值得追逐及其风险。

# 反驳要点
1. **涨幅透支风险**：分析今日涨幅是否已透支后续空间
2. **高位风险**：判断板块是否处于高位，追高风险如何
3. **情绪稳定性**：分析炸板率（≥25%为情绪偏弱，≥40%为极度不稳）
4. **资金真实性**：封板比低（<0.5）说明主力意愿不强
5. **跟风股特征**：涨停股多为跟风股而非龙头股
6. **综合风险评估**：哪些板块风险大于收益？

# 上游看涨论点（需要反驳）
{bull_argument}

# 输出格式（严格遵守JSON格式）
```json
{
  "sectors_risk_analysis": [
    {
      "sector": "板块名称",
      "worth_chasing": false,
      "risk_level": "高/中/低",
      "argument": "风险论点（含具体数据依据，150字以内）",
      "key_risk": ["风险点1", "风险点2"]
    }
  ],
  "overall_argument": "整体看跌论点（对不值得追逐的板块进行综合反驳，200字以内）",
  "confidence": 0.8
}
```

请使用中文回答。
"""
```

### A.3 板块辩论法官提示词（综合裁决版）

```python
SECTOR_JUDGE_PROMPT = """
你是一位资深A股投资法官，负责对板块辩论进行综合裁决。

# 候选板块列表
{candidate_sectors}

# 辩论历史
{debate_history}

# 板块指标数据（作为裁决依据）
{sector_indicators}

# 你的任务
综合评估看涨和看跌论点，基于数据客观裁决，输出最终确认的主线板块。

# 裁决原则
1. **数据优先**：不看谁说得更有气势，而是看谁的数据依据更可靠
2. **风险收益比**：看涨论点是否充分考虑了风险？看跌论点是否过度悲观？
3. **持续性判断**：板块是一日游还是可持续主线？
4. **资金真实性**：封板比、炸板率反映资金真实意愿
5. **宁缺毋滥**：如果所有板块风险都大于收益，可以输出空列表

# 输出格式（严格遵守JSON格式）
```json
{
  "confirmed_sectors": ["确认主线板块1"],
  "decision_reasoning": "裁决理由（含数据依据，200字以内）",
  "confidence": 0.75,
  "sectors_evaluation": [
    {
      "sector": "板块名称",
      "decision": "值得追逐/不值得追逐",
      "bull_strength": "看涨论点强度评估（高/中/低）",
      "bear_strength": "看跌论点强度评估（高/中/低）",
      "key_reason": "裁决关键理由（100字以内）"
    }
  ]
}
```

请使用中文回答，保持客观中立。
"""
```

### A.4 股票看涨研究员提示词（综合辩论版）

```python
STOCK_BULL_PROMPT = """
你是一位资深A股个股策略分析师，负责论证候选股票值得追涨的观点。

# 候选股票列表（已从确认主线板块中筛选）
{candidate_stocks}

# 合力指标数据
{force_indicators}

# 确认主线板块
{confirmed_sectors}

# 你的任务
基于上述数据，对所有候选股票进行**综合论证**，说明哪些股票值得追涨。

# 论证要点
1. **主力资金**：分析主力净流入排名、流入金额
2. **量价配合**：换手率（>3%为强信号）、涨跌幅配合情况
3. **主线归属**：是否属于确认的主线板块
4. **技术形态**：是否有突破信号
5. **综合结论**：哪些股票值得追涨？按优先级排序

# 上游看跌论点（需要反驳）
{bear_argument}

# 输出格式（严格遵守JSON格式）
```json
{
  "stocks_analysis": [
    {
      "code": "股票代码",
      "name": "股票名称",
      "worth_chasing": true/false,
      "priority": 1/2/3,
      "argument": "看涨论点（含具体数据依据，150字以内）",
      "key_data": ["数据依据1", "数据依据2"]
    }
  ],
  "overall_argument": "整体看涨论点（200字以内）",
  "confidence": 0.8
}
```

请使用中文回答。
"""
```

### A.5 股票辩论法官提示词（综合裁决版）

```python
STOCK_JUDGE_PROMPT = """
你是一位资深A股投资法官，负责对股票辩论进行综合裁决。

# 候选股票列表
{candidate_stocks}

# 辩论历史
{debate_history}

# 合力指标数据（作为裁决依据）
{force_indicators}

# 确认主线板块
{confirmed_sectors}

# 你的任务
综合评估看涨和看跌论点，基于数据客观裁决，输出最终筛选的优质标的。

# 裁决原则
1. **数据优先**：看涨论点是否有主力净流入、换手率等数据支撑
2. **风险考量**：看跌论点提出的涨幅过大、PE过高、市值过小等风险是否成立
3. **主线优先**：优先选择属于确认主线板块的股票
4. **宁缺毋滥**：如果所有股票风险都大于收益，可以输出空列表

# 输出格式（严格遵守JSON格式）
```json
{
  "quality_stocks": [
    {
      "code": "股票代码",
      "name": "股票名称",
      "decision": "值得追涨",
      "confidence": 0.7,
      "key_reason": "裁决关键理由（100字以内）",
      "risk_note": "风险提示（如有）"
    }
  ],
  "decision_reasoning": "整体裁决理由（200字以内）",
  "overall_confidence": 0.75
}
```

请使用中文回答，保持客观中立。
"""
```

---

## 附录B：前端接口设计（已确认）

### B.1 API接口列表

根据 `frontend/src/api/aiSelector.ts` 和 `app/services/ai_selector/` 代码确认：

| 接口 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 启动任务 | POST | `/api/ai-selector/run` | 启动AI选股任务 |
| 获取状态 | GET | `/api/ai-selector/status/{taskId}` | 获取任务状态（轮询） |
| 获取结果 | GET | `/api/ai-selector/result/{taskId}` | 获取完整结果 |
| 历史列表 | GET | `/api/ai-selector/history` | 获取历史记录列表（分页） |
| 历史详情 | GET | `/api/ai-selector/history/{taskId}` | 获取历史记录详情 |
| 删除历史 | DELETE | `/api/ai-selector/history/{taskId}` | 删除历史记录 |
| 创建定时 | POST | `/api/ai-selector/schedule` | 创建定时任务 |
| 获取定时 | GET | `/api/ai-selector/schedule` | 获取定时任务配置 |
| 删除定时 | DELETE | `/api/ai-selector/schedule` | 删除定时任务 |
| 预览Cron | POST | `/api/ai-selector/schedule/preview` | 预览Cron表达式 |

### B.2 启动任务请求

```typescript
// POST /api/ai-selector/run
interface AiSelectorRunRequest {
  quick_model?: string      // 快速模型（可选）
  deep_model?: string       // 深度模型（可选）
  debate_rounds?: number    // 辩论轮次（0-3，默认1）
}

// Response
{
  success: true,
  data: {
    task_id: "xxx-xxx-xxx",
    status: "pending",
    message: "AI选股任务已创建"
  }
}
```

### B.3 任务状态响应（轮询）

```typescript
// GET /api/ai-selector/status/{taskId}
interface AiSelectorTaskStatus {
  task_id: string
  user_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  progress: number          // 0-100
  current_step: string      // 当前步骤描述
  error_message?: string    // 失败时的错误信息
  created_at: string
  updated_at: string
}
```

### B.4 任务结果响应（完整）

```typescript
// GET /api/ai-selector/result/{taskId}
interface AiSelectorResult {
  task_id: string
  status: string
  progress: number
  current_step: string
  elapsed_time: number      // 执行耗时（秒）
  
  // 提前终止信息
  early_stop?: boolean
  early_stop_reason?: string
  early_stop_node?: string  // 提前终止节点
  
  // 分析师结果列表
  analyst_results: Array<{
    name: string            // 分析师名称
    conclusion: string      // 简要结论
    tag_type: 'success' | 'warning' | 'danger' | 'info'  // 标签类型
    content: string         // 详细报告内容（Markdown）
  }>
  
  // 综合决策
  decision: {
    action: string          // 决策倾向：强烈推荐/谨慎推荐/观望/规避
    stocks: Array<{
      code: string          // 股票代码
      name: string          // 股票名称
      reason?: string       // 推荐理由
    }>
    reasoning: string       // 决策依据
    position_suggestion?: string  // 仓位建议
    risk_warning?: string   // 风险提示
  }
  
  // 决策报告全文
  decision_report: string
  
  // 辩论轮次结果（新增）
  debate_rounds?: Array<{
    round: number           // 轮次编号
    bull: string            // 看涨论点
    bear: string            // 看跌论点
  }>
  
  completed_at: string
}
```

### B.5 前端分析师团队定义（需对应）

根据前端 `index.vue` 定义，后端需输出以下分析师名称：

| 序号 | 分析师名称 | 对应后端Agent | 输出字段 |
|------|-----------|--------------|---------|
| 1 | 大盘分析师 | `MarketAnalyst` | market_report |
| 2 | 主线板块分析师 | `SectorAnalyst` | sector_report |
| 3 | 市场合力分析师 | `ForceAnalyst` | force_report |
| 4 | 股票龙头分析师 | `LeaderAnalyst` | leader_report |
| 5 | 多空研究员 | `SectorBull/Bear + Judge` | debate_rounds |
| 6 | 风险分析师 | `RiskAnalyst` | risk_report |
| 7 | 决策分析师 | `DecisionAnalyst` | decision_report |

### B.6 前端Tab展示逻辑

前端使用 `el-tabs` 展示分析师结果，每个Tab对应一个 `analyst_results` 元素：

```vue
<el-tabs v-model="activeResultTab" type="card">
  <el-tab-pane
    v-for="(result, index) in resultData.analystResults"
    :key="index"
    :label="result.name"
    :name="String(index)"
  >
    <div class="result-pane">
      <div class="result-summary">
        <el-tag :type="result.tagType" size="large">{{ result.conclusion }}</el-tag>
      </div>
      <div class="result-detail" v-html="formatContent(result.content)"></div>
    </div>
  </el-tab-pane>
</el-tabs>
```

### B.7 提前终止展示逻辑

当 `early_stop=true` 时，前端显示提示：

```vue
<div v-if="resultData.earlyStop" class="early-stop-section">
  <el-alert type="info" :closable="false" show-icon>
    <template #title>
      <span style="font-weight: bold;">分析提前终止：{{ resultData.earlyStopReason }}</span>
    </template>
    <template #default>
      <span>上游分析师判断当前市场条件不满足继续分析的要求，后续步骤已自动跳过。</span>
    </template>
  </el-alert>
</div>
```

### B.8 辩论轮次配置（前端已支持）

前端已支持辩论轮次配置（0-3）：

```vue
<el-radio-group v-model="debateRounds" :disabled="running" size="default">
  <el-radio-button :label="0">不辩论</el-radio-button>
  <el-radio-button :label="1">1轮</el-radio-button>
  <el-radio-button :label="2">2轮</el-radio-button>
  <el-radio-button :label="3">3轮</el-radio-button>
</el-radio-group>
```

**注意**：虽然前端支持 `debate_rounds=0`（不辩论），但根据用户确认，后端**不支持跳过辩论**。需要在架构层面明确：
- 前端 `debate_rounds=0` 可视为"跳过辩论"
- 后端处理逻辑：当候选数量只有1个时自动跳过辩论，否则必须辩论

---

## 附录C：前端分析师团队定义（来自index.vue）

```typescript
const analystTeam = [
  {
    name: '大盘分析师',
    emoji: '📈',
    description: '分析大盘整体走势与市场环境',
    tags: ['指数分析', '北向资金', '涨跌比'],
    bgColor: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)'
  },
  {
    name: '主线板块分析师',
    emoji: '🔥',
    description: '识别当前市场主线热点板块',
    tags: ['涨停集中度', '5日强度', '资金流向'],
    bgColor: 'linear-gradient(135deg, #f093fb 0%, #f5576c 100%)'
  },
  {
    name: '市场合力分析师',
    emoji: '💪',
    description: '分析主力与散户资金动向',
    tags: ['主力净流入', '散户净流入', '双向资金'],
    bgColor: 'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)'
  },
  {
    name: '股票龙头分析师',
    emoji: '👑',
    description: '筛选板块龙头与连板强势股',
    tags: ['连板分析', '板块排名', '成交量'],
    bgColor: 'linear-gradient(135deg, #43e97b 0%, #38f9d7 100%)'
  },
  {
    name: '多空研究员',
    emoji: '⚖️',
    description: '看多/看空研究员多轮辩论，评判员综合研判',
    tags: ['看多论证', '看空质疑', '辩论评判'],
    bgColor: 'linear-gradient(135deg, #30cfd0 0%, #330867 100%)'
  },
  {
    name: '风险分析师',
    emoji: '🛡️',
    description: '排除高风险标的，保障安全边际',
    tags: ['ST排除', '新股过滤', '退市风险'],
    bgColor: 'linear-gradient(135deg, #fa709a 0%, #fee140 100%)'
  },
  {
    name: '决策分析师',
    emoji: '🎯',
    description: '综合所有分析师结论，给出最终决策',
    tags: ['综合决策', '标的推荐', '风险评级'],
    bgColor: 'linear-gradient(135deg, #a18cd1 0%, #fbc2eb 100%)'
  }
]
```

**注意**：前端定义了"多空研究员"作为辩论环节的展示名称，这与后端架构中的"板块辩论团队"+"股票辩论团队"对应。需要在后端输出时合并为一个 `analyst_results` 元素，或分别输出两个辩论结果。

---

## 附录D：Tools工具定义详细说明

### D.1 工具设计原则

与现有"AI股票分析功能"保持一致，采用LangGraph的`@tool`装饰器机制：

1. **使用`@tool`装饰器**：将普通Python函数转换为LangChain工具
2. **参数类型注解**：使用`Annotated[type, "description"]`提供参数说明
3. **返回字符串格式**：工具返回格式化的报告文本，便于LLM理解
4. **错误处理机制**：捕获异常并返回错误信息，避免崩溃

### D.2 工具参数注解规范

```python
from typing import Annotated
from langchain_core.tools import tool

@tool
def get_market_indicators(
    curr_date: Annotated[str, "当前分析日期，格式 YYYY-MM-DD"]
) -> str:
    """
    工具描述：清晰说明工具的功能、输出内容。
    
    Args:
        curr_date: 参数描述
    
    Returns:
        str: 返回内容描述
    """
    ...
```

### D.3 工具列表与参数

| 工具名称 | 参数1 | 参数2 | 参数3 | 返回内容 |
|---------|-------|-------|-------|---------|
| `get_market_indicators` | `curr_date: str` | - | - | 大盘指标报告（指数+北向资金+涨跌家数） |
| `get_sector_indicators` | `curr_date: str` | - | - | 板块指标报告（涨幅+涨停+封板比+炸板率） |
| `get_force_indicators` | `curr_date: str` | `confirmed_sectors: List[str]` | - | 合力指标报告（资金流向+候选股票） |
| `get_leader_indicators` | `curr_date: str` | `quality_stocks: List[Dict]` | - | 龙头指标报告（连板+排名+龙头股） |
| `get_risk_indicators` | `curr_date: str` | `leading_stocks: List[Dict]` | - | 风险指标报告（ST+新股+财务+风险等级） |

### D.4 工具调用流程详解

```
┌──────────────────────────────────────────────────────────────────────┐
│                    工具调用完整流程                                    │
│                                                                      │
│  1. Analyst Node（分析师节点）                                        │
│     │                                                                │
│     │ 构建系统提示词（强调必须调用工具）                               │
│     │                                                                │
│     │ prompt = ChatPromptTemplate.from_messages([                   │
│     │     ("system", "你必须调用工具获取真实数据..."),                │
│     │     MessagesPlaceholder(variable_name="messages"),            │
│     │ ])                                                            │
│     │                                                                │
│     │ 绑定工具                                                       │
│     │ tools = [toolkit.get_market_indicators]                       │
│     │ chain = prompt | llm.bind_tools(tools)                        │
│     │                                                                │
│     │ 调用LLM                                                        │
│     │ result = chain.invoke({"messages": state["messages"]})        │
│     │                                                                │
│     ▼                                                                │
│  2. AIMessage（LLM响应）                                             │
│     │                                                                │
│     │ 检查 tool_calls                                                │
│     │                                                                │
│     ├─ 有 tool_calls ───────────────────────────────────────────────┤
│     │   │                                                            │
│     │   │ result.tool_calls = [                                     │
│     │   │     {"name": "get_market_indicators",                     │
│     │   │      "args": {"curr_date": "2026-06-06"},                 │
│     │   │      "id": "call_xxx"}                                    │
│     │   │ ]                                                          │
│     │   │                                                            │
│     │   │ 返回 {"messages": [result]}                               │
│     │   │                                                            │
│     │   └────────────────────────────────────────────────────────► │
│     │                                                                │
│     └─ 无 tool_calls ───────────────────────────────────────────────┤
│         │                                                            │
│         │ 检查是否有ToolMessage（工具已执行）                         │
│         │                                                            │
│         ├─ 有ToolMessage → 基于数据生成报告                          │
│         │   │                                                        │
│         │   │ report = result.content                               │
│         │   │ market_sentiment = extract_sentiment(report)          │
│         │   │                                                        │
│         │   │ return {                                               │
│         │   │     "messages": [AIMessage(content=report)],          │
│         │   │     "market_report": report,                          │
│         │   │     "market_sentiment": market_sentiment              │
│         │   │ }                                                      │
│         │                                                            │
│         └─ 无ToolMessage → 强制调用工具（防护机制）                   │
│             │                                                        │
│             │ forced_data = toolkit.get_market_indicators.invoke()  │
│             │ report = llm.invoke([forced_data])                    │
│             │                                                        │
│             └────────────────────────────────────────────────────► │
│                                                                      │
│  3. ToolNode（工具执行节点）                                          │
│     │                                                                │
│     │ LangGraph自动执行工具                                          │
│     │                                                                │
│     │ tool_result = get_market_indicators(                          │
│     │     curr_date="2026-06-06"                                    │
│     │ )                                                             │
│     │                                                                │
│     │ 返回 ToolMessage                                               │
│     │                                                                │
│     │ ToolMessage(                                                   │
│     │     content="# A股大盘指标分析报告\n...",                       │
│     │     tool_call_id="call_xxx"                                   │
│     │ )                                                             │
│     │                                                                │
│     └────────────────────────────────────────────────────────────► │
│                                                                      │
│  4. 返回 Analyst Node                                                │
│     │                                                                │
│     │ messages = [AIMessage(tool_calls), ToolMessage(data)]         │
│     │                                                                │
│     │ LLM基于工具数据生成最终报告                                    │
│     │                                                                │
│     │ result = llm.invoke(messages + [HumanMessage("生成报告")])    │
│     │                                                                │
│     │ 返回最终报告                                                   │
│     │                                                                │
│     └────────────────────────────────────────────────────────────► │
│                                                                      │
│  5. Msg Clear（消息清理）                                             │
│     │                                                                │
│     │ 清理消息历史（Anthropic兼容性）                                │
│     │                                                                │
│     │ return {"messages": [RemoveMessage(all), HumanMessage("Continue")]│
│     │                                                                │
│     └────────────────────────────────────────────────────────────► │
│                                                                      │
│  6. 条件路由（业务判断）                                              │
│     │                                                                │
│     │ should_continue_after_market(state)                           │
│     │                                                                │
│     │ if market_sentiment == "偏空":                                │
│     │     return "early_stop"                                       │
│     │ else:                                                         │
│     │     return "continue"                                         │
│     │                                                                │
│     └────────────────────────────────────────────────────────────► │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### D.5 工具调用日志示例

```log
📊 [大盘分析师] ===== 分析师节点开始 =====
🔧 [死循环修复] 当前工具调用次数: 0/1
📊 [大盘分析师] 绑定的工具: ['get_market_indicators']
📊 [大盘分析师] 开始调用LLM...
📊 [大盘分析师] LLM调用完成
📊 [大盘分析师] ========== LLM响应开始 ==========
📊 [大盘分析师] 响应类型: AIMessage
📊 [大盘分析师] 工具调用: [{'name': 'get_market_indicators', 'args': {'curr_date': '2026-06-06'}, 'id': 'call_xxx'}]
📊 [大盘分析师] ========== LLM响应结束 ==========
🔀 [条件判断] should_continue_market
🔧 [死循环修复] - 工具调用次数: 0/1
🔀 [条件判断] 🔧 检测到tool_calls → tools_market

📊 [ToolNode] 执行工具: get_market_indicators
📊 [大盘指标工具] 获取大盘数据: 2026-06-06
✅ [大盘指标工具] 数据获取成功，报告长度: 2500

📊 [大盘分析师] ===== 分析师节点继续 =====
🔧 [死循环修复] 当前工具调用次数: 1/1
📊 [大盘分析师] 消息历史包含ToolMessage，生成报告...
✅ [大盘分析师] 生成报告完成，长度: 1800
🔀 [条件判断] ✅ 报告已完成 → Msg Clear Market
🔀 [大盘判断] 偏多 → 继续板块分析
```

### D.6 工具数据源映射

| 工具 | AKShare接口 | 数据内容 | 更新频率 |
|------|------------|---------|---------|
| `get_market_indicators` | `stock_zh_index_daily` | 指数行情 | 实时 |
| | `stock_hsgt_fund_flow_summary` | 北向资金 | 日度 |
| | `stock_up_down_count` | 涨跌家数 | 实时 |
| `get_sector_indicators` | `stock_board_industry_rank` | 板块涨幅排名 | 日度 |
| | `stock_zt_pool_stats` | 涨停统计 | 日度 |
| | `stock_lb_pool_stats` | 强势股池 | 日度 |
| | `stock_seal_ratio` | 封板比 | 日度 |
| | `stock_broken_limit_rate` | 炸板率 | 日度 |
| `get_force_indicators` | `stock_industry_fund_flow` | 板块资金流向 | 日度 |
| | `stock_individual_fund_flow` | 个股资金流向 | 日度 |
| `get_leader_indicators` | `stock_zt_pool_leader` | 涨停股连板统计 | 日度 |
| | `stock_strong_rank` | 强势股排名 | 日度 |
| `get_risk_indicators` | `stock_new_list` | 新股列表 | 日度 |
| | `stock_candidate_fundamentals` | 基本面数据 | 日度 |

### D.7 与现有系统Toolkit对比

| 对比项 | 现有Toolkit（股票分析） | 新SelectorToolkit（AI选股） |
|-------|----------------------|------------------------|
| **工具数量** | 20+工具 | 5个工具 |
| **工具类型** | 全球市场数据 | A股专项数据 |
| **参数注解** | `Annotated[type, "desc"]` | `Annotated[type, "desc"]`（一致） |
| **返回格式** | 格式化字符串报告 | 格式化字符串报告（一致） |
| **绑定方式** | `llm.bind_tools()` | `llm.bind_tools()`（一致） |
| **执行方式** | ToolNode | ToolNode（一致） |

### D.8 工具错误处理机制

```python
@tool
def get_market_indicators(curr_date: Annotated[str, "..."]) -> str:
    """获取大盘指标"""
    try:
        # 正常数据获取
        index_data = interface.stock_zh_index_daily(curr_date)
        ...
        return formatted_report
        
    except Exception as e:
        # 错误处理：返回错误信息，避免崩溃
        error_msg = f"大盘指标获取失败: {str(e)}"
        logger.error(f"❌ [大盘指标工具] {error_msg}")
        return error_msg
```

**错误处理原则**：
1. 工具永远返回字符串（不会抛出异常）
2. 错误信息包含具体原因
3. LLM可根据错误信息做出决策（如：跳过该环节）

---

*本架构文档用于指导AI选股功能的开发，与现有AI股票分析功能风格保持一致。*