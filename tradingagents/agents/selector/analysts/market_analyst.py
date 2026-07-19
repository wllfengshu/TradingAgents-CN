import json
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AIMessage, ToolMessage

from tradingagents.agents.selector.utils.prompts import MARKET_ANALYST_PROMPT
from tradingagents.agents.selector.utils.llm_logging import log_llm_input, log_llm_output
from tradingagents.agents.selector.utils.content_utils import to_text_content, codes_for_log, extract_json_block
from tradingagents.utils.logging_init import get_logger
logger = get_logger("default")


def _extract_market_sentiment(report: str) -> str:
    """从LLM报告中提取市场情绪"""
    try:
        data = extract_json_block(report)
        return data.get("market_sentiment", "偏空")
    except Exception:
        logger.error(f"📈 [大盘分析师] 报告解析失败")
        pass

    return "偏空"


def create_market_analyst(llm, toolkit):
    """创建大盘分析师节点（采用LangGraph Tools机制）"""

    def market_analyst_node(state):
        tool_call_count = state.get("market_tool_call_count", 0)
        max_tool_calls = 1
        analysis_date = state.get("analysis_date", "")

        logger.info(f"📈 [大盘分析师] 开始分析，日期: {analysis_date}，工具调用次数: {tool_call_count}/{max_tool_calls}")

        # 仅在同一次运行中“二次进入节点”时复用报告，避免脏状态导致首轮被跳过。
        existing_report = state.get("market_report", "")
        if existing_report and len(existing_report) > 100 and tool_call_count > 0:
            logger.info("📈 [大盘分析师] 检测到已生成报告，复用并跳过重复执行")
            return {}

        if tool_call_count >= max_tool_calls:
            msg = "📈 [大盘分析师] 达到最大取数次数，终止工作流"
            logger.error(msg)
            raise RuntimeError(msg)

        logger.info("📈 [大盘分析师] 先取数后分析：正在调用 get_market_indicators")
        try:
            tool_report = to_text_content(
                toolkit.get_market_indicators.invoke({"curr_date": analysis_date})
            )
        except Exception as e:
            msg = f"📈 [大盘分析师] 工具取数失败: {e}"
            logger.error(msg)
            raise RuntimeError(msg)

        tool_msg = ToolMessage(
            content=tool_report,
            name="get_market_indicators",
            tool_call_id="manual_get_market_indicators",
        )

        prompt = ChatPromptTemplate.from_messages([
            ("system", MARKET_ANALYST_PROMPT),
            MessagesPlaceholder(variable_name="messages"),
        ])

        analysis_messages = [*state.get("messages", []), tool_msg]
        chain = prompt | llm
        rendered_messages = prompt.format_messages(messages=analysis_messages)
        log_llm_input("大盘分析师", rendered_messages)
        result = chain.invoke({"messages": analysis_messages})
        log_llm_output("大盘分析师", result)

        report = to_text_content(getattr(result, "content", ""))
        if not report.strip():
            report = tool_report

        sentiment = _extract_market_sentiment(report)
        logger.info(f"📈 [大盘分析师] 报告生成完成，情绪: {sentiment}")
        logger.info(f"📈 [大盘分析师] 报告内容: {report}")
        clean_message = AIMessage(content=report)
        return {
            "messages": [tool_msg, clean_message],
            "market_report": report,
            "market_sentiment": sentiment,
            "market_tool_call_count": tool_call_count + 1,
        }


    return market_analyst_node
