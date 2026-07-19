from datetime import datetime
from typing import Dict, Any, Optional, Callable
from collections import Counter

from langchain_core.messages import HumanMessage
from tradingagents.agents.selector.utils.toolkit import SelectorToolkit
from tradingagents.graph.selector.setup import SelectorGraphSetup
from tradingagents.graph.selector.conditional_logic import SelectorConditionalLogic

from tradingagents.utils.logging_init import get_logger
logger = get_logger("default")


class AiSelectorGraph:
    """AI选股主编排器（类似 TradingAgentsGraph）"""

    def __init__(
        self,
        config: Dict[str, Any] = None,
        quick_llm=None,
        deep_llm=None,
    ):
        self.config = config or {}
        self._graph = None

        # 优先使用外部传入的 LLM，否则自己创建
        self.quick_llm = quick_llm or self._create_llm(self.config.get("quick_model", ""))
        self.deep_llm = deep_llm or self._create_llm(self.config.get("deep_model", ""))

        # 初始化工具包
        self.toolkit = SelectorToolkit()

        # 初始化条件逻辑
        self.conditional_logic = SelectorConditionalLogic(self.config)

    def _create_llm(self, model_name: str):
        """创建LLM实例（复用现有适配器）"""
        try:
            from tradingagents.llm_adapters import create_llm_adapter
            if model_name:
                return create_llm_adapter(model_name, self.config)
        except Exception as e:
            logger.warning(f"⚠️ create_llm_adapter 不可用: {e}，尝试备用方案")

        try:
            from tradingagents.default_config import DEFAULT_CONFIG
            fallback_model = model_name or DEFAULT_CONFIG.get("deep_think_llm", "")
            if not fallback_model:
                fallback_model = DEFAULT_CONFIG.get("quick_llm", "")

            # 检测模型类型并创建
            if "gpt" in fallback_model.lower() or "openai" in fallback_model.lower():
                from langchain_openai import ChatOpenAI
                return ChatOpenAI(model=fallback_model, temperature=0)
            elif "claude" in fallback_model.lower():
                from langchain_anthropic import ChatAnthropic
                return ChatAnthropic(model=fallback_model, temperature=0)
            else:
                from langchain_openai import ChatOpenAI
                return ChatOpenAI(model=fallback_model, temperature=0)
        except Exception as e:
            logger.error(f"❌ LLM创建失败: {e}")
            raise

    def create_graph(self):
        """创建并编译Graph"""
        setup = SelectorGraphSetup(
            quick_thinking_llm=self.quick_llm,
            deep_thinking_llm=self.deep_llm,
            toolkit=self.toolkit,
            conditional_logic=self.conditional_logic,
            config=self.config,
        )
        self._graph = setup.setup_graph()
        return self._graph

    def run(
        self,
        analysis_date: Optional[str] = None,
        progress_callback: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """执行AI选股分析

        Args:
            analysis_date: 分析日期（默认当天）
            progress_callback: 进度回调函数（可选）

        Returns:
            Dict: 包含 final_decision、analyst_results、early_stop 等字段
        """
        if self._graph is None:
            self.create_graph()

        if analysis_date is None:
            analysis_date = datetime.now().strftime("%Y-%m-%d")

        logger.info(f"🚀 [AI选股] 开始分析，日期: {analysis_date}")

        initial_state = {
            "messages": [HumanMessage(content=f"请分析 {analysis_date} 的市场情况并给出选股建议。")],
            "analysis_date": analysis_date,
            "market_tool_call_count": 0,
            "sector_tool_call_count": 0,
            "force_tool_call_count": 0,
            "leader_tool_call_count": 0,
            "risk_tool_call_count": 0,
            "early_stop": False,
        }

        # 防护：限制图最大递归步数，避免异常路由导致高成本循环
        recursion_limit = int(self.config.get("selector_recursion_limit", 40))
        if recursion_limit < 10:
            recursion_limit = 10

        try:
            final_state = initial_state.copy()
            executed_nodes = []

            if progress_callback:
                progress_state = {"last_progress": 0, "last_message": ""}
                for chunk in self._graph.stream(
                    initial_state,
                    config={"recursion_limit": recursion_limit},
                    stream_mode="updates",
                ):
                    self._emit_progress_update(chunk, progress_callback, progress_state)
                    for node_name, node_update in chunk.items():
                        if not str(node_name).startswith("__") and isinstance(node_update, dict):
                            executed_nodes.append(str(node_name))
                            final_state.update(node_update)
            else:
                for chunk in self._graph.stream(
                    initial_state,
                    config={"recursion_limit": recursion_limit},
                    stream_mode="updates",
                ):
                    for node_name, node_update in chunk.items():
                        if not str(node_name).startswith("__") and isinstance(node_update, dict):
                            executed_nodes.append(str(node_name))
                            final_state.update(node_update)

            final_state["executed_nodes"] = executed_nodes
            result = self._format_result(final_state, analysis_date)
            logger.info(f"✅ [AI选股] 分析完成，行动: {result.get('decision', {}).get('action', '未知')}")
            return result
        except Exception as e:
            logger.error(f"❌ [AI选股] 分析失败: {e}")
            raise

    def _emit_progress_update(self, chunk: Dict[str, Any], progress_callback: Callable, progress_state: Dict[str, Any]) -> None:
        """基于实际执行节点发送阶段进度，避免前端进度与后端阶段脱节。"""
        if not isinstance(chunk, dict):
            return

        node_name = None
        for key in chunk.keys():
            if not str(key).startswith("__"):
                node_name = key
                break

        if not node_name:
            return

        node_progress_mapping = {
            "Market Analyst": (25, "正在分析市场环境..."),
            "Sector Analyst": (35, "正在识别主线板块..."),
            "Sector Bull Debater": (45, "正在进行板块多空辩论..."),
            "Sector Bear Debater": (48, "正在进行板块多空辩论..."),
            "Sector Judge": (55, "正在确认主线板块..."),
            "Force Analyst": (65, "正在筛选市场合力标的..."),
            "Stock Bull Debater": (72, "正在进行个股多空辩论..."),
            "Stock Bear Debater": (75, "正在进行个股多空辩论..."),
            "Stock Judge": (80, "正在确认候选标的..."),
            "Leader Analyst": (86, "正在确认板块龙头..."),
            "Risk Analyst": (92, "正在评估风险..."),
            "Decision Analyst": (97, "正在生成最终结论..."),
        }

        mapped = node_progress_mapping.get(node_name)
        if not mapped:
            return

        progress, message = mapped
        progress = max(progress_state["last_progress"], progress)

        if progress == progress_state["last_progress"] and message == progress_state["last_message"]:
            return

        progress_state["last_progress"] = progress
        progress_state["last_message"] = message
        progress_callback(progress, message)

    def _format_result(self, state: Dict, analysis_date: str) -> Dict[str, Any]:
        """格式化输出结果（与前端接口设计一致）"""
        analyst_results = []
        report_map = [
            ("market_report", "大盘分析师", "market_sentiment"),
            ("sector_report", "主线板块分析师", None),
            ("force_report", "市场合力分析师", None),
            ("leader_report", "股票龙头分析师", None),
            ("risk_report", "风险分析师", "risk_level"),
            ("decision_report", "决策分析师", None),
        ]

        for report_key, name, conclusion_key in report_map:
            report = state.get(report_key, "")
            if report:
                conclusion = state.get(conclusion_key, "") if conclusion_key else ""
                analyst_results.append({
                    "name": name,
                    "conclusion": conclusion,
                    "content": report,
                })

        final_decision = state.get("final_decision", {})
        early_stop = state.get("early_stop", False)
        executed_nodes = list(state.get("executed_nodes", []))
        node_counts = dict(Counter(executed_nodes))

        mandatory_nodes = ["Leader Analyst", "Risk Analyst", "Decision Analyst"]
        mandatory_stage_status = {node: node_counts.get(node, 0) > 0 for node in mandatory_nodes}

        return {
            "analysis_date": analysis_date,
            "analyst_results": analyst_results,
            "decision": {
                "action": final_decision.get("action", "观望"),
                "stocks": final_decision.get("stocks", []),
                "position_suggestion": final_decision.get("position_suggestion", ""),
                "risk_warning": final_decision.get("risk_warning", ""),
                "reasoning": final_decision.get("reasoning", ""),
            },
            "early_stop": early_stop,
            "early_stop_reason": state.get("early_stop_reason", ""),
            "early_stop_node": state.get("early_stop_node", ""),
            "execution_trace": {
                "executed_nodes": executed_nodes,
                "node_counts": node_counts,
                "mandatory_stage_status": mandatory_stage_status,
            },
            "debate_rounds": {
                "sector": state.get("sector_debate_state", {}),
                "stock": state.get("stock_debate_state", {}),
            },
        }
