import json
from tradingagents.agents.selector.utils.prompts import SECTOR_BULL_PROMPT
from tradingagents.utils.logging_init import get_logger
from tradingagents.agents.selector.utils.content_utils import to_text_content, codes_for_log, extract_json_block

logger = get_logger("default")


def create_sector_bull_researcher(llm):
    """创建板块看涨研究员（直接LLM调用，无工具）"""

    def sector_bull_node(state):
        main_sectors = state.get("main_sectors", [])
        sector_report = state.get("sector_report", "")
        market_report = state.get("market_report", "")
        debate_state = state.get("sector_debate_state", {})
        bear_argument = debate_state.get("current_response", "（暂无看跌论点）")

        logger.info(f"📈 [板块看涨研究员] 发言，候选板块: {main_sectors}")

        prompt = SECTOR_BULL_PROMPT.format(
            candidate_sectors=main_sectors,
            sector_report=sector_report,
            market_report=market_report,
            bear_argument=bear_argument,
        )

        result = llm.invoke([{"role": "user", "content": prompt}])
        content = result.content

        new_debate_state = {
            "bull_history": debate_state.get("bull_history", "") + f"\n{content}",
            "bear_history": debate_state.get("bear_history", ""),
            "history": debate_state.get("history", "") + f"\nBull: {content}",
            "current_response": f"Sector Bull Analyst: {content}",
            "current_sector": str(main_sectors),
            "judge_decision": debate_state.get("judge_decision", ""),
            "count": debate_state.get("count", 0) + 1,
        }

        return {"sector_debate_state": new_debate_state}

    return sector_bull_node
