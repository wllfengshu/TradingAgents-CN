"""宇宙模式规范化与构建短路。"""

import pytest

from zstock.strategy_management.strategy_section.universe import (
    build_universe_by_date,
    normalize_universe_mode,
)
from zstock.strategy_management.pipeline import StrategyPipeline


def test_normalize_universe_mode():
    assert normalize_universe_mode("full") == "full"
    assert normalize_universe_mode("PREFILTER") == "prefilter"
    assert normalize_universe_mode(None, default="funnel") == "funnel"
    with pytest.raises(ValueError):
        normalize_universe_mode("bogus")


@pytest.mark.asyncio
async def test_build_full_returns_none():
    assert await build_universe_by_date("2024-01-01", "2024-01-31", mode="full") is None


def test_signal_pipeline_universe_mode_default_and_override(monkeypatch):
    monkeypatch.setattr(
        "zstock.strategy_management.pipeline.get_data_query_service",
        lambda: object(),
    )
    monkeypatch.setattr(
        "zstock.strategy_management.pipeline.PreFilters",
        lambda: object(),
    )
    pipe = StrategyPipeline(config_override={"universe_mode": "full"})
    assert pipe._universe_mode() == "full"
    pipe2 = StrategyPipeline(config_override={"universe_mode": "prefilter"})
    assert pipe2._universe_mode() == "prefilter"
    pipe3 = StrategyPipeline(config_override={})
    assert pipe3._universe_mode() == "funnel"


def test_api_bodies_accept_universe_mode():
    pytest.importorskip("fastapi")
    from web.backend.routers.data import TriggerBody
    from web.backend.routers.strategies import BacktestRequest

    t = TriggerBody(action="factor_eval", universe_mode="prefilter")
    assert t.universe_mode == "prefilter"
    b = BacktestRequest(universe_mode="full")
    assert b.universe_mode == "full"
    assert BacktestRequest().universe_mode == "funnel"
