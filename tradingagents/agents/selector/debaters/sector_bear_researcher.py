import json
from tradingagents.agents.selector.utils.prompts import SECTOR_BEAR_PROMPT
from tradingagents.utils.logging_init import get_logger
from tradingagents.agents.selector.utils.content_utils import to_text_content, codes_for_log, extract_json_block

logger = get_logger("default")


def create_sector_bear_researcher(llm):
    """创建板块看跌研究员（直接LLM调用，无工具）"""

    def sector_bear_node(state):
        main_sectors = state.get("main_sectors", [])
        sector_report = state.get("sector_report", "")
        market_report = state.get("market_report", "")
        debate_state = state.get("sector_debate_state", {})
        bull_argument = debate_state.get("current_response", "（暂无看涨论点）")

        logger.info(f"📉 [板块看跌研究员] 发言，候选板块: {main_sectors}")

        prompt = SECTOR_BEAR_PROMPT.format(
            candidate_sectors=main_sectors,
            sector_report=sector_report,
            market_report=market_report,
            bull_argument=bull_argument,
        )

        result = llm.invoke([{"role": "user", "content": prompt}])
        content = result.content

        new_debate_state = {
            "bull_history": debate_state.get("bull_history", ""),
            "bear_history": debate_state.get("bear_history", "") + f"\n{content}",
            "history": debate_state.get("history", "") + f"\nBear: {content}",
            "current_response": f"Sector Bear Analyst: {content}",
            "current_sector": str(main_sectors),
            "judge_decision": debate_state.get("judge_decision", ""),
            "count": debate_state.get("count", 0) + 1,
        }

        return {"sector_debate_state": new_debate_state}

    return sector_bear_node
