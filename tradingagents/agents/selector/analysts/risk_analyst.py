import json
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AIMessage, ToolMessage

from tradingagents.agents.selector.utils.prompts import RISK_ANALYST_PROMPT
from tradingagents.agents.selector.utils.llm_logging import log_llm_input, log_llm_output
from tradingagents.agents.selector.utils.content_utils import to_text_content, codes_for_log, extract_json_block
from tradingagents.utils.logging_init import get_logger
logger = get_logger("default")


def _extract_risk_info(report: str) -> tuple:
    """从报告中提取 risk_level 和 safe_stocks"""
    try:
        data = extract_json_block(report)
        risk_level = data.get("risk_level", "高风险")
        safe_stocks = list(data.get("safe_stocks", []))
        return risk_level, safe_stocks
    except Exception:
        logger.error(f"🛡️ [风险分析师] 提取风险信息失败")
        pass

    return "高风险", []


def create_risk_analyst(llm, toolkit):
    """创建风险分析师节点"""

    def risk_analyst_node(state):
        tool_call_count = state.get("risk_tool_call_count", 0)
        max_tool_calls = 1
        analysis_date = state.get("analysis_date", "")
        leading_stocks = state.get("leading_stocks", [])

        logger.info(f"🛡️ [风险分析师] 开始分析，日期: {analysis_date}，龙头股数: {len(leading_stocks)}")

        existing_report = state.get("risk_report", "")
        # 仅在同一次运行中“二次进入节点”时复用报告，避免脏状态导致首轮被跳过。
        if existing_report and len(existing_report) > 100 and tool_call_count > 0:
            logger.info("🛡️ [风险分析师] 检测到已生成报告，复用并跳过重复执行")
            return {}

        if tool_call_count >= max_tool_calls:
            msg = "🛡️ [风险分析师] 达到最大取数次数，终止工作流"
            logger.error(msg)
            raise RuntimeError(msg)

        logger.info("🛡️ [风险分析师] 先取数后分析：正在调用 get_risk_indicators")
        tool_report = to_text_content(
            toolkit.get_risk_indicators.invoke(
                {"curr_date": analysis_date, "leading_stocks": leading_stocks}
            )
        )

        tool_msg = ToolMessage(
            content=tool_report,
            name="get_risk_indicators",
            tool_call_id="manual_get_risk_indicators",
        )

        prompt = ChatPromptTemplate.from_messages([
            ("system", RISK_ANALYST_PROMPT),
            MessagesPlaceholder(variable_name="messages"),
        ])

        analysis_messages = [*state.get("messages", []), tool_msg]
        chain = prompt | llm
        rendered_messages = prompt.format_messages(messages=analysis_messages)
        log_llm_input("风险分析师", rendered_messages)
        result = chain.invoke({"messages": analysis_messages})
        log_llm_output("风险分析师", result)

        report = to_text_content(getattr(result, "content", ""))
        if not report.strip():
            report = tool_report

        risk_level, safe_stocks = _extract_risk_info(report)
        logger.info(f"🛡️ [风险分析师] 报告生成完成，风险等级: {risk_level}，安全标的: {codes_for_log(safe_stocks)}")
        logger.info(f"🛡️ [风险分析师] 报告内容: {report}")
        clean_message = AIMessage(content=report)
        return {
            "messages": [tool_msg, clean_message],
            "risk_report": report,
            "risk_level": risk_level,
            "safe_stocks": safe_stocks,
            "risk_tool_call_count": tool_call_count + 1,
        }

    return risk_analyst_node
