"""pipeline：load_real_data / compute_signals / m3_universe（mock QS）。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pandas as pd
import pytest

from zstock.strategy_management.pipeline import StrategyPipeline


@pytest.mark.asyncio
async def test_load_real_data_mocked(monkeypatch):
    monkeypatch.setattr(
        "zstock.strategy_management.pipeline.get_data_query_service",
        lambda: object(),
    )
    monkeypatch.setattr(
        "zstock.strategy_management.pipeline.PreFilters",
        lambda: object(),
    )
    pipe = StrategyPipeline(config_override={"universe_mode": "full"})

    class QS:
        async def get_all_stocks(self):
            return (
                [
                    {"code": "600000", "name": "A", "is_st": False, "is_mainboard": True},
                    {"code": "600001", "name": "B", "is_st": False, "is_mainboard": True},
                ],
                None,
            )

        async def get_sector_list(self):
            return (
                [{"sector_code": "SW2A", "stocks": ["600000", "600001"]}],
                None,
            )

        async def get_ohlcv_batch(self, codes, start, end, **kwargs):
            out = {}
            for c in codes:
                out[c] = pd.DataFrame(
                    {"trade_date": [end], "close": [10.0], "amount": [1e7]}
                )
            return out

        async def get_capital_flow_recent_days(self, codes, end_date=None, days=15):
            return {c: [] for c in codes}

    pipe._query_service = QS()
    data = await pipe.load_real_data("2024-01-10", lookback_days=30, max_stocks=2)
    assert data["trade_date"] == "2024-01-10"
    assert set(data["all_stocks"]) == {"600000", "600001"}
    assert "SW2A" in data["sector_stocks"]
    assert "600000" in data["stock_ohlcv"]


@pytest.mark.asyncio
async def test_compute_signals_prefer_precomputed_fallback(monkeypatch):
    monkeypatch.setattr(
        "zstock.strategy_management.pipeline.get_data_query_service",
        lambda: object(),
    )
    monkeypatch.setattr(
        "zstock.strategy_management.pipeline.PreFilters",
        lambda: object(),
    )
    pipe = StrategyPipeline(config_override={})

    async def boom_score(td):
        raise ValueError("no precompute")

    live_df = pd.DataFrame({"code": ["600000"], "signal_type": ["buy"]})
    live_df.attrs["market_grade"] = "yellow"

    async def fake_live(**kwargs):
        return live_df

    pipe.score_signals = boom_score
    pipe.score_signals_live = fake_live
    out = await pipe.compute_signals("2024-01-02", prefer_precomputed=True)
    assert len(out) == 1


@pytest.mark.asyncio
async def test_m3_universe_codes(monkeypatch):
    monkeypatch.setattr(
        "zstock.strategy_management.pipeline.get_data_query_service",
        lambda: object(),
    )
    monkeypatch.setattr(
        "zstock.strategy_management.pipeline.PreFilters",
        lambda: object(),
    )
    pipe = StrategyPipeline(config_override={"universe_mode": "full"})

    async def fake_funnel(td):
        return {"m3_codes": {"600000", "600001"}, "ranked_all": []}

    pipe._run_funnel_precomputed = fake_funnel
    codes = await pipe.m3_universe_codes("2024-01-02")
    assert codes == {"600000", "600001"}
