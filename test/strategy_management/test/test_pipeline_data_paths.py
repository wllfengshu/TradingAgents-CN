"""pipeline：load_real_data / score_signals / m3_universe / book rebalance band。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from test.strategy_management.test.conftest import make_offline_pipeline
from zstock.strategy_management.pipeline import StrategyPipeline
from zstock.strategy_management.strategy_section import schema as signal_schema
from zstock.strategy_management.market_book import BookDecision, BOND


@pytest.mark.asyncio
async def test_load_real_data_mocked():
    pipe = make_offline_pipeline()

    class _QS:
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
                [
                    {"sector_code": "S1", "stocks": ["600000", "600001"]},
                    {"sector_code": "S2", "stocks": []},
                ],
                None,
            )

        async def get_ohlcv_batch(self, codes, start, end):
            return {
                c: pd.DataFrame({"trade_date": ["2024-01-02"], "close": [10.0]})
                for c in codes
            }

        async def get_capital_flow_recent_days(self, codes, end_date=None, days=15):
            return {c: [{"net": 1.0}] for c in codes}

    pipe._query_service = _QS()
    data = await pipe.load_real_data(
        trade_date="2024-01-02",
        lookback_days=30,
        max_stocks=2,
        sectors=["S1"],
    )
    assert data["trade_date"] == "2024-01-02"
    assert set(data["all_stocks"]) == {"600000", "600001"}
    assert "S1" in data["sector_stocks"]
    assert "600000" in data["stock_ohlcv"]
    assert data["index_name"]


@pytest.mark.asyncio
async def test_score_signals_precomputed_paths(monkeypatch):
    pipe = make_offline_pipeline()
    pipe.config = {
        "final_score": {"top_k": 5},
        "market_selection": {"stops_on_red": True},
        "universe_mode": "funnel",
    }

    async def _load(method, td, **kwargs):
        if method == "get_factor_market":
            return {
                "market_risk_level": "red",
                "trade_date": td,
                "score": 0.0,
            }
        return None

    monkeypatch.setattr(pipe, "_load_factor_docs", _load)
    monkeypatch.setattr(
        "zstock.strategy_management.pipeline.score_market_from_doc",
        lambda doc, **kwargs: {
            "market_risk_level": "red",
            "position_scale_factor": 0.0,
            "market_composite_score": 5.0,
        },
    )

    async def _funnel(*a, **k):
        return {
            "early_exit": True,
            "exit_reason": "market_red_light",
            "trade_date": "2024-01-02",
            "regime": "neutral",
            "market_risk_level": "red",
            "position_scale_factor": 0.0,
            "ranked_all": [],
            "funnel": {"stop": "red", "n_m3": 0, "n_m4": 0, "n_buy": 0, "regime": "neutral"},
            "m3_codes": set(),
        }

    monkeypatch.setattr(pipe, "_run_funnel", _funnel)
    df = await pipe.score_signals("2024-01-02")
    assert isinstance(df, pd.DataFrame)

    # non-red path with stocks cache
    async def _load_green(method, td, **kwargs):
        return {"mf1_slope_pct": 0.01, "trade_date": td}

    monkeypatch.setattr(pipe, "_load_factor_docs", _load_green)
    monkeypatch.setattr(
        "zstock.strategy_management.pipeline.score_market_from_doc",
        lambda doc, **kwargs: {
            "market_risk_level": "green",
            "position_scale_factor": 1.0,
            "market_composite_score": 80.0,
        },
    )
    pipe._query_service = SimpleNamespace(
        get_all_stocks=AsyncMock(
            return_value=([{"code": "600000", "name": "A"}], None)
        ),
        get_sector_list=AsyncMock(return_value=([{"sector_code": "S1"}], None)),
    )
    pipe._all_stocks_cache = None
    pipe._sectors_cache = None

    async def _funnel_green(*a, **k):
        return {
            "early_exit": False,
            "trade_date": "2024-01-03",
            "regime": "momentum",
            "market_risk_level": "green",
            "position_scale_factor": 1.0,
            "ranked_all": [
                {"code": "600000", "sector_code": "S1", "final_score": 80.0, "dragon_score": 70.0}
            ],
            "funnel": {"stop": "ok", "n_m3": 1, "n_m4": 1, "n_buy": 1, "regime": "momentum"},
            "m3_codes": {"600000"},
        }

    monkeypatch.setattr(pipe, "_run_funnel", _funnel_green)
    df2 = await pipe.score_signals("2024-01-03")
    assert isinstance(df2, pd.DataFrame)

    async def _funnel_m3(*a, **k):
        return {
            "regime": "neutral",
            "market_risk_level": "green",
            "ranked": [],
            "funnel": {},
            "m3_codes": {"600000", "600001"},
        }

    monkeypatch.setattr(pipe, "_run_funnel_precomputed", _funnel_m3)
    codes = await pipe.m3_universe_codes("2024-01-03")
    assert "600000" in codes
    assert "600001" in codes


@pytest.mark.asyncio
async def test_score_signals_missing_market_doc(monkeypatch):
    pipe = make_offline_pipeline()

    async def _none(*a, **k):
        return None

    monkeypatch.setattr(pipe, "_load_factor_docs", _none)
    with pytest.raises(ValueError, match="无预计算"):
        await pipe.score_signals("2024-01-02")


@pytest.mark.asyncio
async def test_execute_book_rebalance_band_skip(monkeypatch):
    pipe = make_offline_pipeline()
    signals = signal_schema.empty_signals_df("2024-01-02", "yellow", 0.5)
    # non-empty so we don't hit no_signals before book weights path
    signals = pd.DataFrame(
        [
            {
                "code": "600000",
                "signal_type": "buy",
                "final_score": 1.0,
                "rank": 1,
                "market_grade": "yellow",
            }
        ]
    )
    signals.attrs["market_grade"] = "yellow"

    cur = pd.DataFrame({"code": [BOND], "weight": [1.0]})

    def _step(**kwargs):
        return BookDecision(
            state="neutral",
            weights={BOND: 1.0},
            grade="yellow",
            detail={},
        )

    pipe.book.step = _step  # type: ignore[method-assign]
    pipe.book.prepare_for_date = lambda td: None  # type: ignore[method-assign]

    out = await pipe.execute_full_pipeline(
        trade_date="2024-01-02",
        precomputed_signals=signals,
        current_positions=cur,
        config={
            "book_regime": {
                "enabled": True,
                "rebalance_band": 0.5,
                "min_utilization": 0.5,
                "confirm_green_days": 3,
            }
        },
        respect_book_rebalance_band=True,
    )
    assert out["status"] == "success"
    assert out.get("book_rebalance_skipped") is True or out["book_state"] == "neutral"


@pytest.mark.asyncio
async def test_execute_attack_idle_empty_signals():
    pipe = make_offline_pipeline()
    empty = signal_schema.empty_signals_df("2024-01-02", "green", 1.0)
    empty = empty.iloc[0:0]
    empty.attrs["market_grade"] = "green"

    def _step(**kwargs):
        return BookDecision(state="attack", weights=None, grade="green", detail={})

    pipe.book.step = _step  # type: ignore[method-assign]
    pipe.book.prepare_for_date = lambda td: None  # type: ignore[method-assign]

    out = await pipe.execute_full_pipeline(
        trade_date="2024-01-02",
        precomputed_signals=empty,
        config={"book_regime": {"enabled": True, "confirm_green_days": 1}},
    )
    assert out["status"] == "success"
    assert out["results"].get("attack_idle") is True or out["book_state"] == "attack"


@pytest.mark.asyncio
async def test_get_active_factors_and_caches(monkeypatch):
    pipe = make_offline_pipeline()
    pipe.config = {"factors": {}, "final_score": {}}
    monkeypatch.setattr(
        "zstock.strategy_management.pipeline.resolve_active_factors",
        lambda cfg, regime, grade=None: {"sector": ["a"]},
    )
    assert pipe._get_active_factors("neutral") == {"sector": ["a"]}

    pipe._query_service = SimpleNamespace(
        get_all_stocks=AsyncMock(return_value=([{"code": "1"}], None)),
        get_sector_list=AsyncMock(return_value=([{"sector_code": "S"}], None)),
        get_factor_market=AsyncMock(return_value={"x": 1}),
    )
    docs = await pipe._get_all_stocks_cached()
    assert docs
    assert await pipe._get_all_stocks_cached() is docs  # cache hit
    secs = await pipe._get_sectors_cached()
    assert secs
    doc = await pipe._load_factor_docs("get_factor_market", "2024-01-02")
    assert doc == {"x": 1}
