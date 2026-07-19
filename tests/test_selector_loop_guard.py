import os
import sys

# Add the project root directory to the Python path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from langchain_core.messages import AIMessage, ToolMessage

from tradingagents.graph.selector.conditional_logic import SelectorConditionalLogic


def _build_state(tool_name: str, counter_key: str, with_tool_result: bool):
    pending_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": tool_name,
                "args": {"curr_date": "2025-04-08"},
                "id": "tc-1",
                "type": "tool_call",
            }
        ],
    )

    messages: list = [pending_call]
    if with_tool_result:
        messages.append(ToolMessage(content="ok", tool_call_id="tc-1", name=tool_name))
        messages.append(pending_call)

    return {
        "messages": messages,
        counter_key: 0,
        "market_report": "",
        "sector_report": "",
        "force_report": "",
        "leader_report": "",
        "risk_report": "",
    }


def test_should_continue_sector_avoids_repeat_call_when_tool_result_exists():
    cl = SelectorConditionalLogic({})
    state = _build_state("get_sector_indicators", "sector_tool_call_count", with_tool_result=True)
    assert cl.should_continue_sector(state) == "Msg Clear Sector"


def test_should_continue_sector_ignores_tool_call_when_no_tool_result_yet():
    cl = SelectorConditionalLogic({})
    state = _build_state("get_sector_indicators", "sector_tool_call_count", with_tool_result=False)
    assert cl.should_continue_sector(state) == "Msg Clear Sector"


def test_should_continue_market_avoids_repeat_call_when_tool_result_exists():
    cl = SelectorConditionalLogic({})
    state = _build_state("get_market_indicators", "market_tool_call_count", with_tool_result=True)
    assert cl.should_continue_market(state) == "Msg Clear Market"


def test_should_continue_market_ignores_tool_call_when_no_tool_result_yet():
    cl = SelectorConditionalLogic({})
    state = _build_state("get_market_indicators", "market_tool_call_count", with_tool_result=False)
    assert cl.should_continue_market(state) == "Msg Clear Market"


def test_should_continue_force_avoids_repeat_call_when_tool_result_exists():
    cl = SelectorConditionalLogic({})
    state = _build_state("get_force_indicators", "force_tool_call_count", with_tool_result=True)
    assert cl.should_continue_force(state) == "Msg Clear Force"


def test_should_continue_force_ignores_tool_call_when_no_tool_result_yet():
    cl = SelectorConditionalLogic({})
    state = _build_state("get_force_indicators", "force_tool_call_count", with_tool_result=False)
    assert cl.should_continue_force(state) == "Msg Clear Force"


def test_should_continue_leader_avoids_repeat_call_when_tool_result_exists():
    cl = SelectorConditionalLogic({})
    state = _build_state("get_leader_indicators", "leader_tool_call_count", with_tool_result=True)
    assert cl.should_continue_leader(state) == "Msg Clear Leader"


def test_should_continue_leader_ignores_tool_call_when_no_tool_result_yet():
    cl = SelectorConditionalLogic({})
    state = _build_state("get_leader_indicators", "leader_tool_call_count", with_tool_result=False)
    assert cl.should_continue_leader(state) == "Msg Clear Leader"


def test_should_continue_risk_avoids_repeat_call_when_tool_result_exists():
    cl = SelectorConditionalLogic({})
    state = _build_state("get_risk_indicators", "risk_tool_call_count", with_tool_result=True)
    assert cl.should_continue_risk(state) == "Msg Clear Risk"


def test_should_continue_risk_ignores_tool_call_when_no_tool_result_yet():
    cl = SelectorConditionalLogic({})
    state = _build_state("get_risk_indicators", "risk_tool_call_count", with_tool_result=False)
    assert cl.should_continue_risk(state) == "Msg Clear Risk"


def test_should_continue_after_leader_requires_completed_stage():
    cl = SelectorConditionalLogic({})
    state = {
        "leader_tool_call_count": 0,
        "leader_report": "",
        "leading_stocks": [{"code": "600519"}],
    }
    assert cl.should_continue_after_leader(state) == "early_stop"
    assert state.get("early_stop_node") == "Leader Analyst"


def test_should_continue_after_risk_requires_completed_stage():
    cl = SelectorConditionalLogic({})
    state = {
        "risk_tool_call_count": 0,
        "risk_report": "",
        "risk_level": "低",
        "safe_stocks": [{"code": "600519"}],
    }
    assert cl.should_continue_after_risk(state) == "early_stop"
    assert state.get("early_stop_node") == "Risk Analyst"


