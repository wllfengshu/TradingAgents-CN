from langgraph.graph import StateGraph, END, START

from tradingagents.agents.selector.utils.agent_states import SelectorState
from tradingagents.agents.selector.utils.toolkit import SelectorToolkit
from tradingagents.agents.selector.analysts.market_analyst import create_market_analyst
from tradingagents.agents.selector.analysts.sector_analyst import create_sector_analyst
from tradingagents.agents.selector.analysts.force_analyst import create_force_analyst
from tradingagents.agents.selector.analysts.leader_analyst import create_leader_analyst
from tradingagents.agents.selector.analysts.risk_analyst import create_risk_analyst
from tradingagents.agents.selector.analysts.decision_analyst import create_decision_analyst
from tradingagents.agents.selector.debaters.sector_bull_researcher import create_sector_bull_researcher
from tradingagents.agents.selector.debaters.sector_bear_researcher import create_sector_bear_researcher
from tradingagents.agents.selector.debaters.sector_judge import create_sector_judge
from tradingagents.agents.selector.debaters.stock_bull_researcher import create_stock_bull_researcher
from tradingagents.agents.selector.debaters.stock_bear_researcher import create_stock_bear_researcher
from tradingagents.agents.selector.debaters.stock_judge import create_stock_judge
from tradingagents.agents.selector.utils.early_stop_handler import create_early_stop_handler
from tradingagents.agents.utils.agent_utils import create_msg_delete
from tradingagents.graph.selector.conditional_logic import SelectorConditionalLogic

from tradingagents.utils.logging_init import get_logger
logger = get_logger("default")


class SelectorGraphSetup:
    """AI选股图构建器"""

    def __init__(
        self,
        quick_thinking_llm,
        deep_thinking_llm,
        toolkit: SelectorToolkit,
        conditional_logic: SelectorConditionalLogic,
        config: dict = None,
    ):
        self.quick_llm = quick_thinking_llm
        self.deep_llm = deep_thinking_llm
        self.toolkit = toolkit
        self.conditional_logic = conditional_logic
        self.config = config or {}

    def setup_graph(self):
        workflow = StateGraph(SelectorState)

        # ===== 创建节点实例 =====
        market_analyst_node = create_market_analyst(self.quick_llm, self.toolkit)
        sector_analyst_node = create_sector_analyst(self.quick_llm, self.toolkit)
        force_analyst_node = create_force_analyst(self.quick_llm, self.toolkit)
        leader_analyst_node = create_leader_analyst(self.quick_llm, self.toolkit)
        risk_analyst_node = create_risk_analyst(self.quick_llm, self.toolkit)
        decision_analyst_node = create_decision_analyst(self.deep_llm)

        sector_bull_node = create_sector_bull_researcher(self.quick_llm)
        sector_bear_node = create_sector_bear_researcher(self.quick_llm)
        sector_judge_node = create_sector_judge(self.deep_llm)
        stock_bull_node = create_stock_bull_researcher(self.quick_llm)
        stock_bear_node = create_stock_bear_researcher(self.quick_llm)
        stock_judge_node = create_stock_judge(self.deep_llm)

        early_stop_node = create_early_stop_handler()

        # ===== 注册分析师节点 =====
        workflow.add_node("Market Analyst", market_analyst_node)
        workflow.add_node("Sector Analyst", sector_analyst_node)
        workflow.add_node("Force Analyst", force_analyst_node)
        workflow.add_node("Leader Analyst", leader_analyst_node)
        workflow.add_node("Risk Analyst", risk_analyst_node)
        workflow.add_node("Decision Analyst", decision_analyst_node)

        # ===== 注册消息清理节点 =====
        workflow.add_node("Msg Clear Market", create_msg_delete())
        workflow.add_node("Msg Clear Sector", create_msg_delete())
        workflow.add_node("Msg Clear Force", create_msg_delete())
        workflow.add_node("Msg Clear Leader", create_msg_delete())
        workflow.add_node("Msg Clear Risk", create_msg_delete())

        # ===== 注册辩论节点 =====
        workflow.add_node("Sector Bull Debater", sector_bull_node)
        workflow.add_node("Sector Bear Debater", sector_bear_node)
        workflow.add_node("Sector Judge", sector_judge_node)
        workflow.add_node("Stock Bull Debater", stock_bull_node)
        workflow.add_node("Stock Bear Debater", stock_bear_node)
        workflow.add_node("Stock Judge", stock_judge_node)

        # ===== 注册提前终止节点 =====
        workflow.add_node("Early Stop Handler", early_stop_node)

        cl = self.conditional_logic

        # ===== 阶段1：大盘分析 =====
        workflow.add_edge(START, "Market Analyst")
        workflow.add_conditional_edges(
            "Market Analyst",
            cl.should_continue_market,
            {"Msg Clear Market": "Msg Clear Market"},
        )
        workflow.add_conditional_edges(
            "Msg Clear Market",
            cl.should_continue_after_market,
            {"early_stop": "Early Stop Handler", "continue": "Sector Analyst"},
        )

        # ===== 阶段2：板块分析 =====
        workflow.add_conditional_edges(
            "Sector Analyst",
            cl.should_continue_sector,
            {"Msg Clear Sector": "Msg Clear Sector"},
        )
        workflow.add_conditional_edges(
            "Msg Clear Sector",
            cl.should_continue_after_sector,
            {
                "early_stop": "Early Stop Handler",
                "skip_debate": "Force Analyst",
                "enter_debate": "Sector Bull Debater",
            },
        )

        # ===== 阶段3：板块辩论 =====
        workflow.add_conditional_edges(
            "Sector Bull Debater",
            cl.should_continue_sector_debate,
            {"continue": "Sector Bear Debater", "end": "Sector Judge"},
        )
        workflow.add_conditional_edges(
            "Sector Bear Debater",
            cl.should_continue_sector_debate,
            {"continue": "Sector Bull Debater", "end": "Sector Judge"},
        )
        workflow.add_conditional_edges(
            "Sector Judge",
            cl.should_continue_after_sector_judge,
            {"early_stop": "Early Stop Handler", "continue": "Force Analyst"},
        )

        # ===== 阶段4：合力分析 =====
        workflow.add_conditional_edges(
            "Force Analyst",
            cl.should_continue_force,
            {"Msg Clear Force": "Msg Clear Force"},
        )
        workflow.add_conditional_edges(
            "Msg Clear Force",
            cl.should_continue_after_force,
            {
                "early_stop": "Early Stop Handler",
                "skip_debate": "Leader Analyst",
                "enter_debate": "Stock Bull Debater",
            },
        )

        # ===== 阶段5：股票辩论 =====
        workflow.add_conditional_edges(
            "Stock Bull Debater",
            cl.should_continue_stock_debate,
            {"continue": "Stock Bear Debater", "end": "Stock Judge"},
        )
        workflow.add_conditional_edges(
            "Stock Bear Debater",
            cl.should_continue_stock_debate,
            {"continue": "Stock Bull Debater", "end": "Stock Judge"},
        )
        workflow.add_conditional_edges(
            "Stock Judge",
            cl.should_continue_after_stock_judge,
            {"early_stop": "Early Stop Handler", "continue": "Leader Analyst"},
        )

        # ===== 阶段6：龙头分析 =====
        workflow.add_conditional_edges(
            "Leader Analyst",
            cl.should_continue_leader,
            {"Msg Clear Leader": "Msg Clear Leader"},
        )
        workflow.add_conditional_edges(
            "Msg Clear Leader",
            cl.should_continue_after_leader,
            {"early_stop": "Early Stop Handler", "continue": "Risk Analyst"},
        )

        # ===== 阶段7：风险分析 =====
        workflow.add_conditional_edges(
            "Risk Analyst",
            cl.should_continue_risk,
            {"Msg Clear Risk": "Msg Clear Risk"},
        )
        workflow.add_conditional_edges(
            "Msg Clear Risk",
            cl.should_continue_after_risk,
            {"early_stop": "Early Stop Handler", "continue": "Decision Analyst"},
        )

        # ===== 阶段8：决策 =====
        workflow.add_edge("Decision Analyst", END)
        workflow.add_edge("Early Stop Handler", END)

        logger.info("✅ [图构建] AI选股Graph构建完成")
        return workflow.compile()
