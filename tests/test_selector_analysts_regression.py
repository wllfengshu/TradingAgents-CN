import os
import sys


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tradingagents.agents.selector.analysts.market_analyst import create_market_analyst
from tradingagents.agents.selector.analysts.sector_analyst import create_sector_analyst
from tradingagents.agents.selector.analysts.force_analyst import create_force_analyst
from tradingagents.agents.selector.analysts.decision_analyst import create_decision_analyst


class _Result:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    def __init__(self, content: str):
        self._content = content

    def invoke(self, _messages):
        return _Result(self._content)

    def __call__(self, _input):
        return _Result(self._content)


class _FakeTool:
    def __init__(self, content: str):
        self._content = content
        self.called = 0

    def invoke(self, _args):
        self.called += 1
        return self._content


class _FakeToolkit:
    def __init__(self, market=None, sector=None, force=None):
        self.get_market_indicators = _FakeTool(market or "market")
        self.get_sector_indicators = _FakeTool(sector or "sector")
        self.get_force_indicators = _FakeTool(force or "force")


def test_market_should_not_skip_first_run_with_stale_report():
    toolkit = _FakeToolkit(market="{\"market_sentiment\": \"偏多\"}")
    node = create_market_analyst(_FakeLLM("{\"market_sentiment\": \"偏多\"}"), toolkit)

    state = {
        "messages": [],
        "analysis_date": "2026-06-09",
        "market_tool_call_count": 0,
        "market_report": "X" * 120,
    }

    out = node(state)

    assert toolkit.get_market_indicators.called == 1
    assert out["market_tool_call_count"] == 1
    assert out["market_sentiment"] == "偏多"


def test_sector_should_not_skip_first_run_with_stale_report():
    toolkit = _FakeToolkit(sector="{\"has_main_sector\": true, \"main_sectors\": [\"AI\"]}")
    node = create_sector_analyst(_FakeLLM("{\"has_main_sector\": true, \"main_sectors\": [\"AI\"]}"), toolkit)

    state = {
        "messages": [],
        "analysis_date": "2026-06-09",
        "sector_tool_call_count": 0,
        "sector_report": "X" * 120,
    }

    out = node(state)

    assert toolkit.get_sector_indicators.called == 1
    assert out["sector_tool_call_count"] == 1
    assert out["main_sectors"] == ["AI"]


def test_force_should_not_skip_first_run_with_stale_report():
    toolkit = _FakeToolkit(force="{\"force_direction\": \"上行\", \"candidate_stocks\": [\"600519\"]}")
    node = create_force_analyst(
        _FakeLLM("{\"force_direction\": \"上行\", \"candidate_stocks\": [\"600519\"]}"),
        toolkit,
    )

    state = {
        "messages": [],
        "analysis_date": "2026-06-09",
        "force_tool_call_count": 0,
        "force_report": "X" * 120,
    }

    out = node(state)

    assert toolkit.get_force_indicators.called == 1
    assert out["force_tool_call_count"] == 1
    assert out["force_direction"] == "上行"


def test_decision_should_tolerate_non_dict_safe_stocks_in_log_path():
    node = create_decision_analyst(_FakeLLM("{\"action\": \"偏多\", \"stocks\": []}"))
    state = {
        "market_report": "ok",
        "sector_report": "ok",
        "force_report": "ok",
        "leader_report": "ok",
        "risk_report": "ok",
        "safe_stocks": ["600519", None, {"code": "000001"}],
    }

    out = node(state)

    assert out["final_decision"]["action"] == "偏多"


