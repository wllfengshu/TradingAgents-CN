from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AIMessage, ToolMessage

from tradingagents.agents.selector.utils.prompts import LEADER_ANALYST_PROMPT
from tradingagents.agents.selector.utils.llm_logging import log_llm_input, log_llm_output
from tradingagents.agents.selector.utils.content_utils import to_text_content, codes_for_log, extract_json_block
from tradingagents.utils.logging_init import get_logger
logger = get_logger("default")


def _extract_leading_stocks(report: str) -> list:
    """从报告中提取 leading_stocks"""
    try:
        data = extract_json_block(report)
        return list(data.get("leading_stocks", []))
    except Exception:
        logger.error(f"👑 [龙头分析师] 提取报告失败")
        pass
    return []


def create_leader_analyst(llm, toolkit):
    """创建股票龙头分析师节点"""

    def leader_analyst_node(state):
        tool_call_count = state.get("leader_tool_call_count", 0)
        max_tool_calls = 1
        analysis_date = state.get("analysis_date", "")
        quality_stocks = state.get("quality_stocks", [])
        if not quality_stocks:
            # skip_debate 分支下，若未显式写回 quality_stocks，则兜底使用 candidate_stocks。
            quality_stocks = state.get("candidate_stocks", [])
            if quality_stocks:
                logger.warning("👑 [龙头分析师] quality_stocks 为空，回退使用 candidate_stocks")

        logger.info(f"👑 [龙头分析师] 开始分析，日期: {analysis_date}，优质标的数: {len(quality_stocks)}")

        existing_report = state.get("leader_report", "")
        # 仅在同一次运行中“二次进入节点”时复用报告，避免脏状态导致首轮被跳过。
        if existing_report and len(existing_report) > 100 and tool_call_count > 0:
            logger.info("👑 [龙头分析师] 检测到已生成报告，复用并跳过重复执行")
            return {}

        if tool_call_count >= max_tool_calls:
            msg = "👑 [龙头分析师] 达到最大取数次数，终止工作流"
            logger.error(msg)
            raise RuntimeError(msg)

        if not quality_stocks:
            logger.warning("👑 [龙头分析师] 无可用优质标的，输出空龙头结果并交由路由终止")
            report = "{\"leading_stocks\": [], \"analysis_brief\": \"无可用优质标的，无法进行龙头分析\"}"
            clean_message = AIMessage(content=report)
            return {
                "messages": [clean_message],
                "leader_report": report,
                "leading_stocks": [],
                "leader_tool_call_count": tool_call_count + 1,
            }

        logger.info("👑 [龙头分析师] 先取数后分析：正在调用 get_leader_indicators")
        try:
            tool_report = to_text_content(
                toolkit.get_leader_indicators.invoke(
                    {"curr_date": analysis_date, "quality_stocks": quality_stocks}
                )
            )
        except Exception as e:
            msg = f"👑 [龙头分析师] 工具取数失败: {e}"
            logger.error(msg)
            raise RuntimeError(msg)

        tool_msg = ToolMessage(
            content=tool_report,
            name="get_leader_indicators",
            tool_call_id="manual_get_leader_indicators",
        )

        prompt = ChatPromptTemplate.from_messages([
            ("system", LEADER_ANALYST_PROMPT),
            MessagesPlaceholder(variable_name="messages"),
        ])

        analysis_messages = [*state.get("messages", []), tool_msg]
        chain = prompt | llm
        rendered_messages = prompt.format_messages(messages=analysis_messages)
        log_llm_input("龙头分析师", rendered_messages)
        result = chain.invoke({"messages": analysis_messages})
        log_llm_output("龙头分析师", result)

        report = to_text_content(getattr(result, "content", ""))
        if not report.strip():
            report = tool_report

        leading_stocks = _extract_leading_stocks(report)
        logger.info(f"👑 [龙头分析师] 报告生成完成，龙头股: {codes_for_log(leading_stocks)}")
        logger.info(f"👑 [龙头分析师] 报告内容: {report}")
        clean_message = AIMessage(content=report)
        return {
            "messages": [tool_msg, clean_message],
            "leader_report": report,
            "leading_stocks": leading_stocks,
            "leader_tool_call_count": tool_call_count + 1,
        }


    return leader_analyst_node
