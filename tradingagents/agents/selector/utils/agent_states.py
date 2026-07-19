from typing import Annotated, List, Dict
from typing_extensions import TypedDict
from langgraph.graph import MessagesState


class SectorDebateState(TypedDict):
    """板块辩论状态"""
    bull_history: Annotated[str, "板块看涨方历史发言"]
    bear_history: Annotated[str, "板块看跌方历史发言"]
    history: Annotated[str, "完整辩论历史"]
    current_response: Annotated[str, "最新发言"]
    current_sector: Annotated[str, "当前辩论的板块"]
    judge_decision: Annotated[str, "法官裁决"]
    count: Annotated[int, "发言计数"]


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


class SelectorState(MessagesState):
    """AI选股主状态"""

    # 分析日期
    analysis_date: Annotated[str, "分析日期"]

    # 阶段1：大盘分析
    market_report: Annotated[str, "大盘分析报告"]
    market_sentiment: Annotated[str, "大盘情绪：偏多/偏空/中性"]
    market_tool_call_count: Annotated[int, "大盘工具调用计数"]

    # 阶段2：板块分析
    sector_report: Annotated[str, "板块分析报告"]
    main_sectors: Annotated[List[str], "主线板块列表"]
    has_main_sector: Annotated[bool, "是否有主线板块"]
    sector_tool_call_count: Annotated[int, "板块工具调用计数"]

    # 阶段3：板块辩论
    sector_debate_state: Annotated[SectorDebateState, "板块辩论状态"]
    confirmed_sectors: Annotated[List[str], "辩论确认的主线板块"]

    # 阶段4：合力分析
    force_report: Annotated[str, "合力分析报告"]
    candidate_stocks: Annotated[List[Dict], "候选合力股票列表（2-3支）"]
    force_direction: Annotated[str, "合力方向：正向共振/反向分歧/主力主导"]
    force_tool_call_count: Annotated[int, "合力工具调用计数"]

    # 阶段5：股票辩论
    stock_debate_state: Annotated[StockDebateState, "股票辩论状态"]
    quality_stocks: Annotated[List[Dict], "辩论筛选的优质标的"]

    # 阶段6：龙头分析
    leader_report: Annotated[str, "龙头分析报告"]
    leading_stocks: Annotated[List[Dict], "龙头股列表（1-2支）"]
    leader_tool_call_count: Annotated[int, "龙头工具调用计数"]

    # 阶段7：风险分析
    risk_report: Annotated[str, "风险分析报告"]
    risk_level: Annotated[str, "风险等级：低/中/高"]
    safe_stocks: Annotated[List[Dict], "安全标的列表"]
    risk_tool_call_count: Annotated[int, "风险工具调用计数"]

    # 阶段8：最终决策
    decision_report: Annotated[str, "决策报告"]
    final_decision: Annotated[Dict, "最终决策结构化数据"]

    # 提前终止
    early_stop: Annotated[bool, "是否提前终止"]
    early_stop_reason: Annotated[str, "提前终止原因"]
    early_stop_node: Annotated[str, "提前终止节点"]
