"""SignalFunnelMixin：prefilter 宇宙 + live M3 路径（mock）。"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from zstock.strategy_management.strategy_section.funnel import SignalFunnelMixin
from test.factor_management.test.conftest import make_ohlcv


class _Host(SignalFunnelMixin):
    def __init__(self):
        self.config: Dict[str, Any] = {
            "universe_mode": "prefilter",
            "sector_layer": {"top_sectors": 2},
            "dragon_layer": {"top_per_sector": 2},
            "final_score": {
                "weights": {"sector": 0.4, "dragon": 0.35, "cooperative": 0.25},
                "top_k": 3,
            },
            "cooperative_force": {
                "threshold_pct": 0.0,
                "gate_mode": "off",
                "min_sustained_days": 0,
            },
            "active_factors": {
                "sector": {
                    "momentum": [{"field": "f21_rps_10d", "weight": 1.0, "polarity": "positive"}]
                },
                "dragon": {
                    "momentum": [{"field": "f32_amount", "weight": 1.0, "polarity": "positive"}]
                },
                "force": {
                    "momentum": [
                        {"field": "fcoop1_main_net_ratio", "weight": 1.0, "polarity": "positive"}
                    ]
                },
            },
            "market_selection": {"factor_stop_on_red": True},
        }
        self.prefilters = MagicMock()
        self.prefilters.filter_universe.return_value = {
            "stocks": ["600000"],
            "sectors": [{"sector_code": "SW2A"}],
        }
        self._factor_docs: Dict[str, Any] = {}
        self._query_service = None

    def _factor_stops_on_red(self) -> bool:
        return True

    def _output_position_scale(self, grade, m1_scale, regime="neutral"):
        return float(m1_scale)

    def _universe_mode(self) -> str:
        return str(self.config.get("universe_mode") or "funnel")

    def _cfg_top_sectors(self) -> int:
        return 2

    def _cfg_top_per_sector(self) -> int:
        return 2

    def _cfg_final_weights(self) -> Dict[str, float]:
        return {"sector": 0.4, "dragon": 0.35, "cooperative": 0.25}

    def _cfg_layer_gates(self, layer: str):
        return []

    def _get_active_factors(self, regime, grade):
        return self.config["active_factors"]

    def _resolve_m4_gate_settings(self, coop_cfg, regime, grade):
        from zstock.strategy_management.strategy_section.selection import resolve_force_gate_settings

        return resolve_force_gate_settings(coop_cfg, regime, grade)

    async def _detect_style(self, td, index_df=None):
        return {"regime": "momentum", "autocorr": 0.2, "momentum_weight": 0.7, "reversal_weight": 0.3}

    async def _load_factor_docs(self, method, trade_date, **kwargs):
        return self._factor_docs.get(method, [])


@pytest.mark.asyncio
async def test_prefilter_universe_path():
    host = _Host()
    host._factor_docs["get_factor_sectors"] = [
        {
            "sector_code": "SW2A",
            "f21_rps_10d": 0.3,
            "f22_main_flow": 1.0,
            "f23_limit_up_density": 0.1,
        }
    ]
    host._factor_docs["get_factor_dragons"] = [
        {"code": "600000", "sector_code": "SW2A", "f32_amount": 5e8, "f31_excess_return_5d": 0.05}
    ]
    host._factor_docs["get_factor_forces"] = [
        {"code": "600000", "fcoop1_main_net_ratio": 0.05, "fcoop3_sustained_days": 3}
    ]
    out = await host._run_funnel(
        "2024-01-02",
        {"market_risk_level": "yellow", "position_scale_factor": 0.7, "market_composite_score": 55},
        source="precomputed",
        all_stocks=["600000", "600001"],
        stock_infos={},
        sectors=[{"sector_code": "SW2A"}, {"sector_code": "SW2B"}],
    )
    host.prefilters.filter_universe.assert_called_once()
    assert out["early_exit"] is False or out.get("ranked_all") is not None


@pytest.mark.asyncio
async def test_live_dragon_load(monkeypatch):
    host = _Host()
    host.config["universe_mode"] = "full"
    ohlcv = {"600000": make_ohlcv(40, close=10.0, amount=5e7, turnover=5.0)}
    td = str(ohlcv["600000"]["trade_date"].iloc[-1])

    class FakeDragon:
        @staticmethod
        def calculate_all_dragon_factors_raw(jobs, stock_ohlcv, trade_date=None, fund_provider=None):
            return {
                "SW2A": {
                    "600000": {"f32_amount": 1e8, "f31_excess_return_5d": 0.05, "f35_bollinger_trend": 60.0}
                }
            }

        @staticmethod
        def raw_from_doc(d):
            return d

    monkeypatch.setattr(
        "zstock.strategy_management.strategy_section.funnel.DragonFactors",
        FakeDragon,
    )
    by = await host._load_dragon_raw_by_sector(
        source="live",
        trade_date=td,
        top_sectors=[("SW2A", 1.0)],
        sector_stocks={"SW2A": ["600000"]},
        stock_ohlcv=ohlcv,
        filtered_stocks={"600000"},
    )
    assert "SW2A" in by
    assert "600000" in by["SW2A"]
