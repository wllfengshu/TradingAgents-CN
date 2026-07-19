import os
import sys

# Add the project root directory to the Python path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tradingagents.graph.selector.selector_graph import AiSelectorGraph


def test_format_result_includes_execution_trace_and_mandatory_status():
    graph = AiSelectorGraph.__new__(AiSelectorGraph)

    state = {
        "market_report": "market ok",
        "sector_report": "sector ok",
        "force_report": "force ok",
        "leader_report": "leader ok",
        "risk_report": "risk ok",
        "decision_report": "decision ok",
        "final_decision": {"action": "观望", "stocks": []},
        "executed_nodes": [
            "Market Analyst",
            "Sector Analyst",
            "Force Analyst",
            "Leader Analyst",
            "Risk Analyst",
            "Decision Analyst",
        ],
    }

    result = graph._format_result(state, "2026-06-07")

    trace = result.get("execution_trace", {})
    assert trace.get("executed_nodes", [])
    assert trace.get("node_counts", {}).get("Leader Analyst", 0) == 1

    mandatory = trace.get("mandatory_stage_status", {})
    assert mandatory.get("Leader Analyst") is True
    assert mandatory.get("Risk Analyst") is True
    assert mandatory.get("Decision Analyst") is True

