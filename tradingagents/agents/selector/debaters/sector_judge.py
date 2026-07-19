import json
from tradingagents.agents.selector.utils.prompts import SECTOR_JUDGE_PROMPT
from tradingagents.utils.logging_init import get_logger
from tradingagents.agents.selector.utils.content_utils import to_text_content, codes_for_log, extract_json_block

logger = get_logger("default")


def _extract_confirmed_sectors(report: str) -> list:
    """从裁决报告中提取确认的主线板块"""
    try:
        data = extract_json_block(report)
        return list(data.get("confirmed_sectors", []))
    except Exception:
        logger.error(f"从裁决报告中提取确认的主线板块-失败: {report}")
        pass
    return []


def create_sector_judge(llm):
    """创建板块辩论法官（直接LLM调用，无工具）"""

    def sector_judge_node(state):
        main_sectors = state.get("main_sectors", [])
        sector_report = state.get("sector_report", "")
        debate_state = state.get("sector_debate_state", {})
        debate_history = debate_state.get("history", "（无辩论记录）")

        logger.info(f"⚖️ [板块辩论法官] 开始裁决，候选板块: {main_sectors}")

        prompt = SECTOR_JUDGE_PROMPT.format(
            candidate_sectors=main_sectors,
            debate_history=debate_history,
            sector_report=sector_report,
        )

        result = llm.invoke([{"role": "user", "content": prompt}])
        content = result.content
        confirmed_sectors = _extract_confirmed_sectors(content)

        logger.info(f"⚖️ [板块辩论法官] 裁决完成，确认主线: {confirmed_sectors}")

        new_debate_state = {
            **debate_state,
            "judge_decision": content,
            "current_response": f"Sector Judge: {content}",
        }

        return {
            "sector_debate_state": new_debate_state,
            "confirmed_sectors": confirmed_sectors,
        }

    return sector_judge_node
