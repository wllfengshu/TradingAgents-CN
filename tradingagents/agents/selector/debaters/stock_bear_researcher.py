import json
from tradingagents.agents.selector.utils.prompts import STOCK_BEAR_PROMPT
from tradingagents.utils.logging_init import get_logger
from tradingagents.agents.selector.utils.content_utils import to_text_content, codes_for_log, extract_json_block

logger = get_logger("default")


def create_stock_bear_researcher(llm):
    """创建股票看跌研究员（直接LLM调用，无工具）"""

    def stock_bear_node(state):
        candidate_stocks = state.get("candidate_stocks", [])
        force_report = state.get("force_report", "")
        confirmed_sectors = state.get("confirmed_sectors", state.get("main_sectors", []))
        debate_state = state.get("stock_debate_state", {})
        bull_argument = debate_state.get("current_response", "（暂无看涨论点）")

        logger.info(f"📉 [股票看跌研究员] 发言，候选股票: {[s.get('code') for s in candidate_stocks]}")

        prompt = STOCK_BEAR_PROMPT.format(
            candidate_stocks=candidate_stocks,
            force_report=force_report,
            confirmed_sectors=confirmed_sectors,
            bull_argument=bull_argument,
        )

        result = llm.invoke([{"role": "user", "content": prompt}])
        content = result.content

        new_debate_state = {
            "bull_history": debate_state.get("bull_history", ""),
            "bear_history": debate_state.get("bear_history", "") + f"\n{content}",
            "history": debate_state.get("history", "") + f"\nBear: {content}",
            "current_response": f"Stock Bear Analyst: {content}",
            "current_stock": {},
            "judge_decision": debate_state.get("judge_decision", ""),
            "count": debate_state.get("count", 0) + 1,
            "debated_stocks": debate_state.get("debated_stocks", []),
            "remaining_stocks": debate_state.get("remaining_stocks", []),
        }

        return {"stock_debate_state": new_debate_state}

    return stock_bear_node
