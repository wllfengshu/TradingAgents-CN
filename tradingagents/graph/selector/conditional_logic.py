from tradingagents.utils.logging_init import get_logger
logger = get_logger("default")


class SelectorConditionalLogic:
    """AI选股条件路由逻辑（分析师节点内手动取数）"""

    def __init__(self, config: dict = None):
        config = config or {}
        self.max_sector_debate_rounds = config.get("max_sector_debate_rounds", 1)
        self.max_stock_debate_rounds = config.get("max_stock_debate_rounds", 1)

    # ===== 工具调用检测函数 =====

    def should_continue_market(self, state) -> str:
        """作用：分析师节点内工具调用检测，确保每个阶段至少执行一次工具调用且产出报告，避免LLM直接跳过工具调用导致后续流程异常。逻辑：如果没有消息，直接清理；如果有消息但没有产出报告，继续清理；如果有报告但长度异常，继续清理；否则继续正常流程。"""
        messages = state.get("messages", [])
        if not messages:
            return "Msg Clear Market"

        last_message = messages[-1]
        market_report = state.get("market_report", "")

        logger.info(f"🔀 [路由] should_continue_market: 报告长={len(market_report)}")

        if market_report and len(market_report) > 100:
            return "Msg Clear Market"

        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            logger.warning("🔧 [路由] 检测到遗留tool_calls，忽略并继续到消息清理节点")

        return "Msg Clear Market"

    def should_continue_sector(self, state) -> str:
        messages = state.get("messages", [])
        if not messages:
            return "Msg Clear Sector"

        last_message = messages[-1]
        sector_report = state.get("sector_report", "")

        if sector_report and len(sector_report) > 100:
            return "Msg Clear Sector"

        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            logger.warning("🔧 [路由] 检测到遗留tool_calls，忽略并继续到消息清理节点")

        return "Msg Clear Sector"

    def should_continue_force(self, state) -> str:
        messages = state.get("messages", [])
        if not messages:
            return "Msg Clear Force"

        last_message = messages[-1]
        force_report = state.get("force_report", "")

        if force_report and len(force_report) > 100:
            return "Msg Clear Force"

        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            logger.warning("🔧 [路由] 检测到遗留tool_calls，忽略并继续到消息清理节点")

        return "Msg Clear Force"

    def should_continue_leader(self, state) -> str:
        messages = state.get("messages", [])
        if not messages:
            return "Msg Clear Leader"

        last_message = messages[-1]
        leader_report = state.get("leader_report", "")

        if leader_report and len(leader_report) > 100:
            return "Msg Clear Leader"

        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            logger.warning("🔧 [路由] 检测到遗留tool_calls，忽略并继续到消息清理节点")

        return "Msg Clear Leader"

    def should_continue_risk(self, state) -> str:
        messages = state.get("messages", [])
        if not messages:
            return "Msg Clear Risk"

        last_message = messages[-1]
        risk_report = state.get("risk_report", "")

        if risk_report and len(risk_report) > 100:
            return "Msg Clear Risk"

        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            logger.warning("🔧 [路由] 检测到遗留tool_calls，忽略并继续到消息清理节点")

        return "Msg Clear Risk"

    # ===== 分析师后业务判断 =====

    def should_continue_after_market(self, state) -> str:
        market_sentiment = state.get("market_sentiment", "中性")
        logger.info(f"🔀 [路由] 大盘情绪: {market_sentiment}")
        # todo 这里先注释来测试，后续恢复
        # if market_sentiment == "偏空":
        #     state["early_stop_reason"] = "大盘偏空，市场环境不佳"
        #     state["early_stop_node"] = "Market Analyst"
        #     return "early_stop"
        return "continue"

    def should_continue_after_sector(self, state) -> str:
        has_main_sector = state.get("has_main_sector", False)
        main_sectors = state.get("main_sectors", [])
        logger.info(f"🔀 [路由] 主线板块: {main_sectors}, has_main: {has_main_sector}")

        if not has_main_sector or not main_sectors:
            state["early_stop_reason"] = "无主线板块，市场缺乏明确方向"
            state["early_stop_node"] = "Sector Analyst"
            return "early_stop"

        if len(main_sectors) == 1:
            state["confirmed_sectors"] = main_sectors
            logger.info(f"🔀 [路由] 只有1个板块，跳过辩论直接确认: {main_sectors}")
            return "skip_debate"

        return "enter_debate"

    def should_continue_after_sector_judge(self, state) -> str:
        confirmed_sectors = state.get("confirmed_sectors", [])
        logger.info(f"🔀 [路由] 板块辩论确认: {confirmed_sectors}")

        if not confirmed_sectors:
            state["early_stop_reason"] = "板块辩论未确认任何主线板块"
            state["early_stop_node"] = "Sector Judge"
            return "early_stop"
        return "continue"

    def should_continue_after_force(self, state) -> str:
        candidate_stocks = state.get("candidate_stocks", [])
        logger.info(f"🔀 [路由] 候选股票: {[s.get('code') for s in candidate_stocks]}")

        if not candidate_stocks:
            state["early_stop_reason"] = "无合力股票，主线板块内无明确标的"
            state["early_stop_node"] = "Force Analyst"
            return "early_stop"

        if len(candidate_stocks) == 1:
            state["quality_stocks"] = candidate_stocks
            logger.info(f"🔀 [路由] 只有1支股票，跳过辩论直接确认")
            return "skip_debate"

        return "enter_debate"

    def should_continue_after_stock_judge(self, state) -> str:
        quality_stocks = state.get("quality_stocks", [])
        logger.info(f"🔀 [路由] 优质标的: {[s.get('code') for s in quality_stocks]}")

        if not quality_stocks:
            state["early_stop_reason"] = "股票辩论未筛选出优质标的"
            state["early_stop_node"] = "Stock Judge"
            return "early_stop"
        return "continue"

    def should_continue_after_leader(self, state) -> str:
        leader_tool_call_count = state.get("leader_tool_call_count", 0)
        leader_report = state.get("leader_report", "")
        leading_stocks = state.get("leading_stocks", [])
        quality_stocks = state.get("quality_stocks", [])
        if not quality_stocks:
            quality_stocks = state.get("candidate_stocks", [])
        logger.info(f"🔀 [路由] 龙头股: {[s.get('code') for s in leading_stocks]}")

        # 强制阶段完整性：龙头分析节点至少执行一次且产出报告。
        if leader_tool_call_count < 1 or not str(leader_report).strip():
            state["early_stop_reason"] = "龙头分析阶段未完整执行，已阻断后续流程"
            state["early_stop_node"] = "Leader Analyst"
            return "early_stop"

        if not leading_stocks:
            state["early_stop_reason"] = "未找到龙头股"
            state["early_stop_node"] = "Leader Analyst"
            return "early_stop"

        source_codes = {str(s.get("code", "")).strip() for s in quality_stocks if isinstance(s, dict)}
        selected_codes = {str(s.get("code", "")).strip() for s in leading_stocks if isinstance(s, dict)}
        if source_codes and not selected_codes.issubset(source_codes):
            state["early_stop_reason"] = "龙头结果与上游优质标的不一致"
            state["early_stop_node"] = "Leader Analyst"
            return "early_stop"
        return "continue"

    def should_continue_after_risk(self, state) -> str:
        risk_tool_call_count = state.get("risk_tool_call_count", 0)
        risk_report = state.get("risk_report", "")
        risk_level = state.get("risk_level", "中")
        safe_stocks = state.get("safe_stocks", [])
        logger.info(f"🔀 [路由] 风险等级: {risk_level}, 安全标的: {[s.get('code') for s in safe_stocks]}")

        # 强制阶段完整性：风险分析节点至少执行一次且产出报告。
        if risk_tool_call_count < 1 or not str(risk_report).strip():
            state["early_stop_reason"] = "风险分析阶段未完整执行，已阻断后续流程"
            state["early_stop_node"] = "Risk Analyst"
            return "early_stop"

        if risk_level == "高" or not safe_stocks:
            state["early_stop_reason"] = f"风险等级为{risk_level}，无安全标的"
            state["early_stop_node"] = "Risk Analyst"
            return "early_stop"
        return "continue"

    # ===== 辩论条件判断 =====

    def should_continue_sector_debate(self, state) -> str:
        debate_state = state.get("sector_debate_state", {})
        count = debate_state.get("count", 0)
        max_count = 2 * self.max_sector_debate_rounds
        current_speaker = debate_state.get("current_response", "")

        logger.info(f"🔀 [路由] 板块辩论进度: {count}/{max_count}")

        if count >= max_count:
            return "end"

        return "continue"

    def should_continue_stock_debate(self, state) -> str:
        debate_state = state.get("stock_debate_state", {})
        count = debate_state.get("count", 0)
        max_count = 2 * self.max_stock_debate_rounds

        logger.info(f"🔀 [路由] 股票辩论进度: {count}/{max_count}")

        if count >= max_count:
            return "end"

        return "continue"
