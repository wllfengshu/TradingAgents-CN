import json
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AIMessage, ToolMessage

from tradingagents.agents.selector.utils.prompts import SECTOR_ANALYST_PROMPT
from tradingagents.agents.selector.utils.llm_logging import log_llm_input, log_llm_output
from tradingagents.agents.selector.utils.content_utils import to_text_content, codes_for_log, extract_json_block
from tradingagents.utils.logging_init import get_logger
logger = get_logger("default")


def _extract_sector_info(report: str) -> tuple:
    """从报告中提取 has_main_sector 和 main_sectors"""
    try:
        data = extract_json_block(report)
        has_main = data.get("has_main_sector", False)
        sectors = data.get("main_sectors", [])
        return bool(has_main), list(sectors)
    except Exception:
        logger.error(f"🔥 [板块分析师] 提取报告信息失败")
        pass

    return False, []


def create_sector_analyst(llm, toolkit):
    """创建主线板块分析师节点"""

    def sector_analyst_node(state):
        tool_call_count = state.get("sector_tool_call_count", 0)
        max_tool_calls = 1
        analysis_date = state.get("analysis_date", "")

        logger.info(f"🔥 [板块分析师] 开始分析，日期: {analysis_date}，工具调用次数: {tool_call_count}/{max_tool_calls}")

        existing_report = state.get("sector_report", "")
        # 仅在同一次运行中“二次进入节点”时复用报告，避免脏状态导致首轮被跳过。
        if existing_report and len(existing_report) > 100 and tool_call_count > 0:
            logger.info("🔥 [板块分析师] 检测到已生成报告，复用并跳过重复执行")
            return {}

        if tool_call_count >= max_tool_calls:
            msg = "🔥 [板块分析师] 达到最大取数次数，终止工作流"
            logger.error(msg)
            raise RuntimeError(msg)

        logger.info("🔥 [板块分析师] 先取数后分析：正在调用 get_sector_indicators")
        try:
            tool_report = to_text_content(
                toolkit.get_sector_indicators.invoke({"curr_date": analysis_date})
            )
        except Exception as e:
            msg = f"🔥 [板块分析师] 工具取数失败: {e}"
            logger.error(msg)
            raise RuntimeError(msg)

        tool_msg = ToolMessage(
            content=tool_report,
            name="get_sector_indicators",
            tool_call_id="manual_get_sector_indicators",
        )

        prompt = ChatPromptTemplate.from_messages([
            ("system", SECTOR_ANALYST_PROMPT),
            MessagesPlaceholder(variable_name="messages"),
        ])

        analysis_messages = [*state.get("messages", []), tool_msg]
        chain = prompt | llm
        rendered_messages = prompt.format_messages(messages=analysis_messages)
        log_llm_input("板块分析师", rendered_messages)
        result = chain.invoke({"messages": analysis_messages})
        log_llm_output("板块分析师", result)

        report = to_text_content(getattr(result, "content", ""))
        if not report.strip():
            report = tool_report

        has_main, main_sectors = _extract_sector_info(report)
        logger.info(f"🔥 [板块分析师] 报告生成完成，主线板块: {main_sectors}")
        logger.info(f"🔥 [板块分析师] 报告内容: {report}")
        clean_message = AIMessage(content=report)
        return {
            "messages": [tool_msg, clean_message],
            "sector_report": report,
            "has_main_sector": has_main,
            "main_sectors": main_sectors,
            "sector_tool_call_count": tool_call_count + 1,
        }


    return sector_analyst_node
