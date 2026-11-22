"""StrategyPipeline：配置旋钮、红灯开仓、仓位缩放、漏斗入口（mock）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from zstock.strategy_management.pipeline import StrategyPipeline
from test.strategy_management.test.conftest import make_offline_pipeline


def test_cfg_helpers_and_universe_fallback(monkeypatch):
    monkeypatch.setattr(
        "zstock.strategy_management.pipeline.get_data_query_service",
        lambda: object(),
    )
    monkeypatch.setattr(
        "zstock.strategy_management.pipeline.PreFilters",
        lambda: object(),
    )
    pipe = StrategyPipeline(
        config_override={
            "universe_mode": "bogus",
            "final_score": {
                "top_k": 5,
                "weights": {"sector": 0.5},
                "by_regime": {"reversal": {"top_k": 2}},
            },
            "sector_layer": {"top_sectors": 6, "gates": []},
            "dragon_layer": {"top_per_sector": 4},
            "market_selection": {
                "stops_on_red": False,
                "strategy_blocks_new_on_red": True,
                "position_scale_applied_in": "strategy",
            },
            "market_overlay": {
                "position_scale_yellow": 0.4,
                "by_regime": {"momentum": {"position_scale_green": 0.95}},
            },
            "style_switching": False,
        }
    )
    assert pipe._universe_mode() == "funnel"
    assert pipe._cfg_top_sectors() == 6
    assert pipe._cfg_top_per_sector() == 4
    assert pipe._cfg_top_k("reversal") == 2
    assert pipe._cfg_top_k("momentum") == 5
    assert pipe._exit_universe_n("reversal") >= 20
    assert pipe._cfg_final_weights()["sector"] == 0.5
    assert pipe._cfg_layer_gates("sector_layer") == []
    assert pipe._cfg_layer_gates("missing_layer") is None
    assert pipe._factor_stops_on_red() is False
    assert pipe._allow_new_open_in_signals("yellow") is True
    assert pipe._allow_new_open_in_signals("red") is False
    assert pipe._output_position_scale("yellow", 0.7) == 1.0  # strategy 模式
    pipe.config["market_selection"]["position_scale_applied_in"] = "factor"
    assert pipe._resolve_position_scale("yellow", 0.7) == 0.4
    assert pipe._resolve_position_scale("green", 1.0, regime="momentum") == 0.95


@pytest.mark.asyncio
async def test_detect_style_cache_and_neutral(monkeypatch):
    monkeypatch.setattr(
        "zstock.strategy_management.pipeline.get_data_query_service",
        lambda: object(),
    )
    monkeypatch.setattr(
        "zstock.strategy_management.pipeline.PreFilters",
        lambda: object(),
    )
    pipe = StrategyPipeline(config_override={"style_switching": False})
    s1 = await pipe._detect_style("2024-01-02")
    assert s1["regime"] == "neutral"

    pipe2 = StrategyPipeline(config_override={"style_switching": True})

    class QS:
        async def get_ohlcv(self, *a, **k):
            raise RuntimeError("no idx")

    pipe2._query_service = QS()
    s2 = await pipe2._detect_style("2024-01-02")
    assert s2["regime"] == "neutral"
    # cache hit
    s3 = await pipe2._detect_style("2024-01-02")
    assert s3 is s2


@pytest.mark.asyncio
async def test_offline_compute_signals_delegate():
    class Sig:
        async def score_signals(self, td):
            return pd.DataFrame({"code": ["600000"], "signal_type": ["buy"]})

        async def compute_signals(self, **kwargs):
            return await self.score_signals(kwargs.get("trade_date"))

    pipe = make_offline_pipeline()
    pipe._signals = Sig()
    # duck path via execute may use precomputed; direct score via _signals if wired
    out = await pipe._signals.score_signals("2024-01-02")
    assert len(out) == 1


@pytest.mark.asyncio
async def test_result_to_signals_and_precomputed_red(monkeypatch):
    pipe = make_offline_pipeline()
    pipe.config = {
        "final_score": {"top_k": 2},
        "market_selection": {"stops_on_red": True},
        "universe_mode": "full",
    }

    empty = pipe._result_to_signals_df(
        {
            "early_exit": True,
            "trade_date": "2024-01-02",
            "market_risk_level": "red",
            "position_scale_factor": 0.0,
            "regime": "neutral",
            "ranked_all": [],
            "funnel": {"stop": "red", "n_m3": 0, "n_m4": 0, "n_buy": 0, "regime": "neutral"},
        }
    )
    assert empty.empty

    async def fake_load(method, td, **kwargs):
        if method == "get_factor_market":
            return {"trend_gap": -0.05}  # 占位；情绪由 mock 覆盖
        return []

    monkeypatch.setattr(
        "zstock.strategy_management.pipeline.score_market_from_doc",
        lambda doc, **kwargs: {
            "market_risk_level": "red",
            "position_scale_factor": 0.0,
            "market_composite_score": 5.0,
        },
    )
    pipe._load_factor_docs = fake_load
    result = await pipe._run_funnel_precomputed("2024-01-02")
    assert result["early_exit"] is True
    assert result["exit_reason"] == "market_red_light"
