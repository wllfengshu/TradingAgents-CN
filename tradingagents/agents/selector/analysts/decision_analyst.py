from tradingagents.agents.selector.utils.prompts import DECISION_ANALYST_PROMPT
from tradingagents.agents.selector.utils.llm_logging import log_llm_input, log_llm_output
from tradingagents.agents.selector.utils.content_utils import to_text_content, codes_for_log, extract_json_block
from tradingagents.utils.logging_init import get_logger
logger = get_logger("default")


def create_decision_analyst(llm):
    """创建决策分析师节点（无工具调用，直接LLM推理）"""

    def decision_analyst_node(state):
        market_report = state.get("market_report", "无大盘报告")
        sector_report = state.get("sector_report", "无板块报告")
        force_report = state.get("force_report", "无合力报告")
        leader_report = state.get("leader_report", "无龙头报告")
        risk_report = state.get("risk_report", "无风险报告")
        safe_stocks = state.get("safe_stocks", [])

        logger.info(f"🎯 [决策分析师] 开始最终决策，安全标的: {codes_for_log(safe_stocks)}")

        prompt = DECISION_ANALYST_PROMPT.format(
            market_report=market_report,
            sector_report=sector_report,
            force_report=force_report,
            leader_report=leader_report,
            risk_report=risk_report,
            safe_stocks=safe_stocks,
        )

        decision_messages = [{"role": "user", "content": prompt}]
        log_llm_input("决策分析师", decision_messages)
        result = llm.invoke(decision_messages)
        log_llm_output("决策分析师", result)
        report = to_text_content(getattr(result, "content", ""))
        final_decision = extract_json_block(report)
        if final_decision is None:
            logger.error(f"🎯 [决策分析师] 错误：数据解析失败！")
            fallback_decision = {
                "action": "偏空",
                "stocks": [],
                "position_suggestion": "数据解析失败！空仓观望",
                "risk_warning": "",
                "reasoning": "",
            }
            return {
                "decision_report": report,
                "final_decision": fallback_decision,
            }

        logger.info(f"🎯 [决策分析师] 决策完成，行动: {final_decision}")
        logger.info(f"🎯 [决策分析师] 报告内容: {report}")
        return {
            "decision_report": report,
            "final_decision": final_decision,
        }

    return decision_analyst_node
