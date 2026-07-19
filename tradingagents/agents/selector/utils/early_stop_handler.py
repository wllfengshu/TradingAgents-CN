from tradingagents.utils.logging_init import get_logger
logger = get_logger("default")


def _infer_stop_context(state) -> tuple:
    """从状态推断终止节点和原因（条件边无法写入状态，故在此推断）"""
    reason = state.get("early_stop_reason", "")
    node = state.get("early_stop_node", "")
    if reason and node:
        return node, reason

    market_sentiment = state.get("market_sentiment", "")
    has_main_sector = state.get("has_main_sector", False)
    main_sectors = state.get("main_sectors", [])
    confirmed_sectors = state.get("confirmed_sectors", [])
    candidate_stocks = state.get("candidate_stocks", [])
    quality_stocks = state.get("quality_stocks", [])
    leading_stocks = state.get("leading_stocks", [])
    risk_level = state.get("risk_level", "")
    safe_stocks = state.get("safe_stocks", [])

    if market_sentiment == "偏空":
        return "Market Analyst", "大盘偏空，市场环境不佳"
    if not has_main_sector or not main_sectors:
        return "Sector Analyst", "无主线板块，市场缺乏明确方向"
    if not confirmed_sectors:
        return "Sector Judge", "板块辩论未确认任何主线板块"
    if not candidate_stocks:
        return "Force Analyst", "无合力股票，主线板块内无明确标的"
    if not quality_stocks:
        return "Stock Judge", "股票辩论未筛选出优质标的"
    if not leading_stocks:
        return "Leader Analyst", "未找到龙头股"
    if risk_level == "高" or not safe_stocks:
        return "Risk Analyst", f"风险等级为{risk_level or '高'}，无安全标的"
    return "未知节点", "未知原因"


def create_early_stop_handler():
    """创建提前终止处理器"""

    def early_stop_handler(state):
        node, reason = _infer_stop_context(state)

        logger.info(f"🛑 [提前终止] 在节点 [{node}] 终止，原因: {reason}")

        # 收集已完成的分析师报告
        completed_reports = {}
        for key in ["market_report", "sector_report", "force_report", "leader_report", "risk_report"]:
            val = state.get(key, "")
            if val and len(val) > 50:
                completed_reports[key] = val

        final_decision = {
            "action": "规避" if "高风险" in reason else "观望",
            "stocks": [],
            "position_suggestion": "空仓，等待更好机会",
            "risk_warning": f"因{reason}，建议观望",
            "reasoning": f"分析在【{node}】节点提前终止：{reason}",
            "early_stop": True,
            "early_stop_reason": reason,
            "early_stop_node": node,
            "completed_reports": completed_reports,
        }

        return {
            "early_stop": True,
            "final_decision": final_decision,
            "decision_report": f"# 提前终止报告\n\n**终止节点**: {node}\n**终止原因**: {reason}\n\n建议**{final_decision['action']}**，空仓等待。",
        }

    return early_stop_handler
