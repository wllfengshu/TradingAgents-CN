"""SignalFunnelMixin：红灯短路、无候选、M4 空、正常路径（预计算 source）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock

import pytest

from zstock.strategy_management.strategy_section.funnel import SignalFunnelMixin
from zstock.strategy_management.strategy_section import schema as signal_schema


class _Host(SignalFunnelMixin):
    def __init__(self):
        self.config: Dict[str, Any] = {
            "universe_mode": "full",
            "sector_layer": {"top_sectors": 5},
            "dragon_layer": {"top_per_sector": 3},
            "final_score": {
                "weights": {"sector": 0.4, "dragon": 0.35, "cooperative": 0.25},
                "top_k": 5,
            },
            "cooperative_force": {
                "threshold_pct": 0.0,
                "gate_mode": "off",
                "min_sustained_days": 0,
            },
            "active_factors": {
                "sector": {
                    "momentum": [{"field": "f21_rps_10d", "weight": 1.0, "polarity": "positive"}],
                    "reversal": [{"field": "f21_rps_10d", "weight": 1.0, "polarity": "positive"}],
                },
                "dragon": {
                    "momentum": [{"field": "f32_amount", "weight": 1.0, "polarity": "positive"}],
                    "reversal": [{"field": "f32_amount", "weight": 1.0, "polarity": "positive"}],
                },
                "force": {
                    "momentum": [
                        {"field": "fcoop1_main_net_ratio", "weight": 1.0, "polarity": "positive"}
                    ],
                    "reversal": [
                        {"field": "fcoop1_main_net_ratio", "weight": 1.0, "polarity": "positive"}
                    ],
                },
            },
            "market_selection": {"factor_stop_on_red": True},
        }
        self.prefilters = None
        self._factor_docs: Dict[str, Any] = {}

    def _factor_stops_on_red(self) -> bool:
        return bool((self.config.get("market_selection") or {}).get("factor_stop_on_red", True))

    def _output_position_scale(self, grade: str, m1_scale: float, regime: str = "neutral") -> float:
        if grade == "red":
            return 0.0
        return float(m1_scale)

    def _universe_mode(self) -> str:
        return str(self.config.get("universe_mode") or "funnel")

    def _cfg_top_sectors(self) -> int:
        return int((self.config.get("sector_layer") or {}).get("top_sectors", 5))

    def _cfg_top_per_sector(self) -> int:
        return int((self.config.get("dragon_layer") or {}).get("top_per_sector", 3))

    def _cfg_final_weights(self) -> Dict[str, float]:
        return dict((self.config.get("final_score") or {}).get("weights") or {})

    def _cfg_layer_gates(self, layer: str) -> Dict[str, Any]:
        return {}

    def _get_active_factors(self, regime: str, grade: str) -> Dict[str, Any]:
        return self.config["active_factors"]

    def _resolve_m4_gate_settings(self, coop_cfg, regime, grade):
        from zstock.strategy_management.strategy_section.selection import resolve_force_gate_settings

        return resolve_force_gate_settings(coop_cfg, regime, grade)

    async def _detect_style(self, td, index_df=None):
        return {"regime": "momentum", "autocorr": 0.2, "momentum_weight": 0.7, "reversal_weight": 0.3}

    async def _load_factor_docs(self, method: str, trade_date: str, **kwargs):
        return self._factor_docs.get(method, [])


@pytest.mark.asyncio
async def test_funnel_red_light_early_exit():
    host = _Host()
    out = await host._run_funnel(
        "2024-01-02",
        {"market_risk_level": "red", "position_scale_factor": 0.0, "market_composite_score": 10},
        source="precomputed",
        all_stocks=["600000"],
        stock_infos={},
        sectors=[],
    )
    assert out["early_exit"] is True
    assert out["exit_reason"] == "market_red_light"
    assert out["funnel"]["stop"] == "red"


@pytest.mark.asyncio
async def test_funnel_no_m3_candidates():
    host = _Host()
    host._factor_docs["get_factor_sectors"] = [
        {"sector_code": "SW2A", "f21_rps_10d": 0.2, "f22_main_flow": 1.0},
    ]
    host._factor_docs["get_factor_dragons"] = []
    out = await host._run_funnel(
        "2024-01-02",
        {"market_risk_level": "yellow", "position_scale_factor": 0.7, "market_composite_score": 50},
        source="precomputed",
        all_stocks=["600000"],
        stock_infos={"600000": {"code": "600000"}},
        sectors=[{"sector_code": "SW2A"}],
    )
    assert out["early_exit"] is True
    assert out["exit_reason"] == "no_candidates"


@pytest.mark.asyncio
async def test_funnel_happy_path_and_m4_empty():
    host = _Host()
    host._factor_docs["get_factor_sectors"] = [
        {
            "sector_code": "SW2A",
            "f21_rps_10d": 0.3,
            "f21_rps_20d": 0.2,
            "f22_main_flow": 1e8,
            "f23_limit_up_density": 0.1,
            "f24_max_consecutive": 1,
            "f25_volume_slope": 0.01,
            "f26_volume_growth": 0.1,
            "f28_consistency": 2,
            "f29_sector_breadth": 0.5,
            "f30_sector_concentration": 0.4,
        },
    ]
    host._factor_docs["get_factor_dragons"] = [
        {
            "code": "600000",
            "sector_code": "SW2A",
            "f32_amount": 5e8,
            "f31_excess_return_5d": 0.05,
            "f33_consecutive_boards": 0,
        },
        {
            "code": "600001",
            "sector_code": "SW2A",
            "f32_amount": 3e8,
            "f31_excess_return_5d": 0.02,
            "f33_consecutive_boards": 0,
        },
    ]
    host._factor_docs["get_factor_forces"] = [
        {
            "code": "600000",
            "fcoop1_main_net_ratio": 0.05,
            "fcoop3_sustained_days": 3,
            "longhu_board_bonus": 0,
        },
        {
            "code": "600001",
            "fcoop1_main_net_ratio": 0.02,
            "fcoop3_sustained_days": 2,
            "longhu_board_bonus": 0,
        },
    ]
    out = await host._run_funnel(
        "2024-01-02",
        {"market_risk_level": "yellow", "position_scale_factor": 0.7, "market_composite_score": 55},
        source="precomputed",
        all_stocks=["600000", "600001"],
        stock_infos={},
        sectors=[{"sector_code": "SW2A"}],
    )
    assert out["early_exit"] is False
    assert len(out["ranked_all"]) >= 1
    assert out["funnel"]["stop"] == "ok"
    assert out["funnel"]["n_m3"] >= 1

    # M4 门禁全杀
    host.config["cooperative_force"] = {
        "threshold_pct": 0.99,
        "gate_mode": "inflow",
        "min_sustained_days": 10,
    }
    empty = await host._run_funnel(
        "2024-01-02",
        {"market_risk_level": "yellow", "position_scale_factor": 0.7, "market_composite_score": 55},
        source="precomputed",
        all_stocks=["600000", "600001"],
        stock_infos={},
        sectors=[{"sector_code": "SW2A"}],
    )
    assert empty["early_exit"] is True
    assert empty["exit_reason"] == "m4_empty"


@pytest.mark.asyncio
async def test_load_raw_precomputed_paths():
    host = _Host()
    host._factor_docs["get_factor_sectors"] = [
        {"sector_code": "SW2A", "f21_rps_10d": 0.1},
        {"sector_code": "SW2B", "f21_rps_10d": 0.2},
    ]
    raw = await host._load_sector_raw(
        source="precomputed",
        trade_date="2024-01-02",
        filtered_sectors=[{"sector_code": "SW2A"}],
        sector_stocks=None,
        stock_ohlcv=None,
        stock_flow_recent=None,
        filtered_stocks={"600000"},
    )
    assert "f21_rps_10d" in raw
    assert "SW2A" in raw["f21_rps_10d"]
    assert "SW2B" not in raw["f21_rps_10d"]

    host._factor_docs["get_factor_dragons"] = [
        {"code": "600000", "sector_code": "SW2A", "f32_amount": 1.0},
        {"code": "600099", "sector_code": "SW2A", "f32_amount": 1.0},
    ]
    by = await host._load_dragon_raw_by_sector(
        source="precomputed",
        trade_date="2024-01-02",
        top_sectors=[("SW2A", 1.0)],
        sector_stocks=None,
        stock_ohlcv=None,
        filtered_stocks={"600000"},
    )
    assert "600000" in by["SW2A"]
    assert "600099" not in by["SW2A"]

    host._factor_docs["get_factor_forces"] = [{"code": "600000", "fcoop1_main_net_ratio": 0.1}]
    fmap = await host._load_force_docs_map(
        source="precomputed",
        trade_date="2024-01-02",
        candidates=[{"code": "600000"}],
        stock_flow_recent=None,
        stock_ohlcv=None,
    )
    assert fmap["600000"]["fcoop1_main_net_ratio"] == 0.1

    with pytest.raises(ValueError):
        host._factor_docs["get_factor_sectors"] = []
        await host._load_sector_raw(
            source="precomputed",
            trade_date="2024-01-02",
            filtered_sectors=[],
            sector_stocks=None,
            stock_ohlcv=None,
            stock_flow_recent=None,
            filtered_stocks=set(),
        )


@pytest.mark.asyncio
async def test_load_fundamental_and_live_force_fallback():
    host = _Host()
    host._query_service = None
    assert await host._load_fundamental_asof("2024-01-02") is None

    class QS:
        async def get_fundamental_asof(self, td):
            raise RuntimeError("boom")

        async def batch_get_lhb_recent(self, codes, td, days=10):
            raise RuntimeError("lhb")

    host._query_service = QS()
    assert await host._load_fundamental_asof("2024-01-02") is None

    # live force：LHB 失败仍算 raw
    from test.factor_management.test.conftest import make_ohlcv

    ohlcv = {"600000": make_ohlcv(30, close=10.0, amount=5e7, turnover=5.0)}
    fmap = await host._load_force_docs_map(
        source="live",
        trade_date=str(ohlcv["600000"]["trade_date"].iloc[-1]),
        candidates=[{"code": "600000", "sector_code": "SW2A"}],
        stock_flow_recent={},
        stock_ohlcv=ohlcv,
    )
    assert "600000" in fmap
