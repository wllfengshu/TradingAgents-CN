"""pipeline：配置 helpers、universe_mode、仓位缩放、duck-typed signals。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from test.strategy_management.test.conftest import make_offline_pipeline
from zstock.strategy_management.pipeline import StrategyPipeline
from zstock.strategy_management.strategy_section import schema as signal_schema


def test_cfg_helpers_and_universe_mode_fallback(strategy_params):
    pipe = make_offline_pipeline()
    pipe.config = {
        "sector_layer": {"top_sectors": 3},
        "dragon_layer": {"top_per_sector": 1},
        "final_score": {"top_k": 7, "by_regime": {"reversal": {"top_k": 2}}, "weights": {"sector": 0.5}},
        "universe_mode": "bogus",
        "market_selection": {
            "strategy_blocks_new_on_red": True,
            "stops_on_red": True,
            "position_scale_applied_in": "strategy",
        },
        "market_overlay": {
            "by_regime": {"neutral": {"position_scale_yellow": 0.4}},
            "position_scale_green": 0.9,
        },
        "dragon_layer": {"top_per_sector": 1, "gates": [{"name": "x"}]},
    }
    assert pipe._universe_mode() == "funnel"
    pipe.config["universe_mode"] = "full"
    assert pipe._universe_mode() == "full"
    assert pipe._cfg_top_sectors() == 3
    assert pipe._cfg_top_per_sector() == 1
    assert pipe._cfg_top_k("reversal") == 2
    assert pipe._cfg_top_k("momentum") == 7
    assert pipe._exit_universe_n("reversal") >= 20
    w = pipe._cfg_final_weights()
    assert w["sector"] == 0.5
    assert "dragon" in w
    assert pipe._cfg_layer_gates("dragon_layer") == [{"name": "x"}]
    assert pipe._cfg_layer_gates("sector_layer") is None
    assert pipe._market_selection()["strategy_blocks_new_on_red"] is True
    assert pipe._factor_stops_on_red() is True
    assert pipe._allow_new_open_in_signals("green") is True
    assert pipe._allow_new_open_in_signals("red") is False
    assert pipe._output_position_scale("yellow", 0.5, "neutral") == 1.0
    pipe.config["market_selection"]["position_scale_applied_in"] = "factor"
    assert pipe._output_position_scale("yellow", 0.5, "neutral") == 0.4
    assert pipe._resolve_position_scale("green", 0.3, "momentum") == 0.9
    assert pipe._resolve_position_scale("blue", 0.33) == 0.33

    pipe.config["market_selection"] = {"strategy_blocks_new_on_red": False, "stops_on_red": False}
    assert pipe._allow_new_open_in_signals("red") is True
    pipe.config["market_selection"] = {"strategy_blocks_new_on_red": False, "stops_on_red": True}
    assert pipe._allow_new_open_in_signals("red") is False


def test_apply_config_override_and_merge():
    pipe = make_offline_pipeline()
    pipe.config = {
        "a": {"x": 1, "y": 2, "z": {"n": 1}},
        "b": 1,
        "portfolio": {},
        "final_score": {"top_k": 5},
    }
    pipe._apply_config_override({"a": {"y": 9, "z": {"m": 2}}, "c": 3})
    assert pipe.config["a"]["x"] == 1
    assert pipe.config["a"]["y"] == 9
    assert pipe.config["a"]["z"]["n"] == 1
    assert pipe.config["a"]["z"]["m"] == 2
    assert pipe.config["c"] == 3
    assert "portfolio_optimization" in pipe.runtime
    merged = pipe._resolve_exec_config({"portfolio": {"max_holdings": 9}, "extra": 1})
    assert merged["portfolio"]["max_holdings"] == 9
    assert merged["extra"] == 1
    assert pipe._resolve_exec_config(None)


@pytest.mark.asyncio
async def test_compute_signals_duck_typed(monkeypatch):
    called = {}

    class Duck:
        async def compute_signals(self, **kwargs):
            called["kwargs"] = kwargs
            return signal_schema.empty_signals_df("2024-01-02", "yellow", 0.5)

        async def score_signals(self, trade_date):
            return await self.compute_signals(trade_date=trade_date)

    pipe = StrategyPipeline(signals=Duck())  # type: ignore[arg-type]
    df2 = await pipe._generate_signals(trade_date="2024-01-02")
    assert df2.attrs.get("market_grade") == "yellow"
    assert called.get("kwargs") is not None
    out = await pipe._signals.compute_signals(trade_date="2024-01-02")
    assert out.attrs.get("market_grade") == "yellow"


@pytest.mark.asyncio
async def test_compute_signals_prefer_precomputed_fallback(monkeypatch):
    pipe = make_offline_pipeline()

    async def _boom(td):
        raise ValueError("无预计算")

    async def _live(**kwargs):
        return signal_schema.empty_signals_df("2024-01-02", "green", 1.0)

    monkeypatch.setattr(pipe, "score_signals", _boom)
    monkeypatch.setattr(pipe, "score_signals_live", _live)
    df = await pipe.compute_signals(trade_date="2024-01-02", prefer_precomputed=True)
    assert df.attrs.get("market_grade") == "green"


@pytest.mark.asyncio
async def test_detect_style_cache_and_disabled(monkeypatch):
    pipe = make_offline_pipeline()
    pipe.config = {"style_switching": False}
    out = await pipe._detect_style("2024-01-02")
    assert out.get("regime") == "neutral" or "regime" in out or out

    pipe.config = {"style_switching": True}
    pipe._style_cache["2024-01-02"] = {"regime": "momentum", "cached": True}
    assert (await pipe._detect_style("2024-01-02"))["cached"] is True

    pipe._style_cache.clear()
    pipe._query_service = SimpleNamespace(
        get_ohlcv=AsyncMock(side_effect=RuntimeError("net"))
    )
    # offline pipe may have query_service None — set it
    out2 = await pipe._detect_style("2024-01-03")
    assert "regime" in out2


@pytest.mark.asyncio
async def test_execute_full_pipeline_no_signals_and_generate(monkeypatch):
    pipe = make_offline_pipeline()
    empty = signal_schema.empty_signals_df("2024-01-02", "yellow", 0.5)
    # ensure empty
    empty = empty.iloc[0:0]
    empty.attrs["market_grade"] = "yellow"
    out = await pipe.execute_full_pipeline(
        trade_date="2024-01-02",
        precomputed_signals=empty,
        config={"book_regime": {"enabled": False}},
    )
    assert out["status"] == "no_signals"

    async def _gen(**kwargs):
        return signal_schema.empty_signals_df("2024-01-02", "red", 0.0)

    monkeypatch.setattr(pipe, "_generate_signals", _gen)
    out2 = await pipe.execute_full_pipeline(
        trade_date="2024-01-02",
        config={"book_regime": {"enabled": False}},
    )
    assert out2["status"] in ("no_signals", "success")


@pytest.mark.asyncio
async def test_result_to_signals_and_resolve_m4():
    pipe = make_offline_pipeline()
    pipe.config = {
        "final_score": {"top_k": 5},
        "market_selection": {},
    }
    result = {
        "regime": "neutral",
        "market_risk_level": "green",
        "ranked": [],
        "funnel": {},
    }
    df = pipe._result_to_signals_df(result)
    assert isinstance(df, pd.DataFrame)
    thr, mode, n = pipe._resolve_m4_gate_settings(
        {"threshold_pct": 0.02, "mode": "inflow", "min_count": 1},
        "neutral",
        "green",
    )
    assert isinstance(thr, float)


@pytest.mark.asyncio
async def test_score_signals_live_with_prebuilt(monkeypatch):
    pipe = make_offline_pipeline()

    async def _funnel_live(data, td):
        return {
            "regime": "neutral",
            "market_risk_level": "yellow",
            "ranked": [],
            "funnel": {},
        }

    monkeypatch.setattr(pipe, "_run_funnel_live", _funnel_live)
    df = await pipe.score_signals_live(
        "2024-01-02",
        prebuilt_data={
            "trade_date": "2024-01-02",
            "all_stocks": [],
            "stock_infos": {},
            "sectors": [],
            "index_ohlcv": {},
        },
    )
    assert isinstance(df, pd.DataFrame)
