import json
from tradingagents.agents.selector.utils.prompts import STOCK_JUDGE_PROMPT
from tradingagents.utils.logging_init import get_logger
from tradingagents.agents.selector.utils.content_utils import to_text_content, codes_for_log, extract_json_block

logger = get_logger("default")


def _extract_quality_stocks(report: str) -> list:
    """从裁决报告中提取优质标的"""
    try:
        data = extract_json_block(report)
        return list(data.get("quality_stocks", []))
    except Exception:
        logger.error(f"从裁决报告中提取优质标的-失败: {report}")
        pass
    return []


def create_stock_judge(llm):
    """创建股票辩论法官（直接LLM调用，无工具）"""

    def stock_judge_node(state):
        candidate_stocks = state.get("candidate_stocks", [])
        force_report = state.get("force_report", "")
        confirmed_sectors = state.get("confirmed_sectors", state.get("main_sectors", []))
        debate_state = state.get("stock_debate_state", {})
        debate_history = debate_state.get("history", "（无辩论记录）")

        logger.info(f"⚖️ [股票辩论法官] 开始裁决，候选股票: {[s.get('code') for s in candidate_stocks]}")

        prompt = STOCK_JUDGE_PROMPT.format(
            candidate_stocks=candidate_stocks,
            debate_history=debate_history,
            force_report=force_report,
            confirmed_sectors=confirmed_sectors,
        )

        result = llm.invoke([{"role": "user", "content": prompt}])
        content = result.content
        quality_stocks = _extract_quality_stocks(content)

        logger.info(f"⚖️ [股票辩论法官] 裁决完成，优质标的: {[s.get('code') for s in quality_stocks]}")

        new_debate_state = {
            **debate_state,
            "judge_decision": content,
            "current_response": f"Stock Judge: {content}",
        }

        return {
            "stock_debate_state": new_debate_state,
            "quality_stocks": quality_stocks,
        }

    return stock_judge_node
