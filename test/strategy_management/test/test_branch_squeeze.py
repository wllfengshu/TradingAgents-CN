"""再挤几条分支：overnight init 回退、selection by_regime、execution 列级 grade、风控。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pandas as pd
import pytest

from zstock.strategy_management.strategy_section import execute as ex
from zstock.strategy_management.strategy_section import exit as exit_policy
from zstock.strategy_management.strategy_section.risk import RiskManager
from zstock.strategy_management.script import overnight_backtest as ob
from zstock.strategy_management.strategy_section.selection import (
    _normalize_sector_ranks,
    resolve_force_gate_settings,
    score_and_compose_final,
)


def test_extract_grade_from_column():
    df = pd.DataFrame({"market_grade": ["green", "green"]})
    assert ex.extract_market_grade(df) == "green"
    df2 = pd.DataFrame({"market_grade": [None]})
    assert ex.extract_market_grade(df2) == "unknown"


def test_normalize_ranks_and_trend_empty():
    assert _normalize_sector_ranks([]) == {}
    assert _normalize_sector_ranks([("S1", 1.0)]) == {"S1": 100.0}
    # target_equity enabled but key 未命中 → 落到 overlay
    assert (
        ex.resolve_market_position_scale(
            "yellow",
            {"target_equity": {"enabled": True, "by_context": {}}, "market_overlay": {"position_scale_yellow": 0.44}},
            regime="momentum",
        )
        == 0.44
    )
    assert (
        exit_policy.trend_force_exit_codes(
            set(),
            "2024-01-02",
            {"trend_exit": {"enabled": True}},
            ohlcv_by_code={},
            f35_by_code={},
        )
        == set()
    )


def test_resolve_gate_by_regime_partial_keys():
    thr, mode, sus = resolve_force_gate_settings(
        {
            "threshold_pct": 0.02,
            "gate_mode": "inflow",
            "min_sustained_days": 1,
            "by_regime": {
                "momentum": {
                    # 只改 mode，threshold/min_sustained 走全局（覆盖 209/211/213 跳过臂）
                    "gate_mode": "outflow",
                }
            },
        },
        "momentum",
        "green",
    )
    assert thr == pytest.approx(0.02)
    assert mode == "outflow"
    assert sus == 1


def test_risk_manager_holdings_and_cash_flags():
    rm = RiskManager()
    over = pd.DataFrame(
        {
            "code": [f"c{i}" for i in range(6)],
            "weight": [0.3, 0.25, 0.2, 0.15, 0.1, 0.1],  # sum=1.1
            "sector_code": ["S"] * 6,
        }
    )
    # allow_cash=True + 非法权重和
    out1 = rm._check_compliance(
        over,
        limits_override={
            "min_holdings": 1,
            "max_holdings": 3,
            "top_k": 2,
            "max_top5_concentration": 0.3,
            "max_weight_per_stock": 0.5,
            "max_sector_exposure": 1.0,
            "weight_sum_tolerance": 0.01,
            "allow_cash": True,
        },
    )
    assert any("权重和" in i for i in out1["issues"])

    # allow_cash=False + 权重和刚好≈1（不报警）
    exact = pd.DataFrame(
        {
            "code": ["a", "b"],
            "weight": [0.5, 0.5],
            "sector_code": ["S1", "S2"],
        }
    )
    out2 = rm._check_compliance(
        exact,
        limits_override={
            "min_holdings": 1,
            "max_holdings": 5,
            "top_k": 5,
            "max_top5_concentration": 1.0,
            "max_weight_per_stock": 0.6,
            "max_sector_exposure": 1.0,
            "weight_sum_tolerance": 0.01,
            "allow_cash": False,
        },
    )
    assert not any("权重和" in i for i in out2["issues"])


@pytest.mark.asyncio
async def test_async_main_init_fallback(tmp_path, monkeypatch):
    args = ob.build_parser().parse_args(
        [
            "--year",
            "2024",
            "--end-date",
            "2024-03-31",
            "--skip-precompute",
            "--skip-eval",
            "--skip-grid",
            "--skip-sensitivity",
            "--no-quarters",
            "--output",
            str(tmp_path),
        ]
    )

    async def _fail_init():
        raise RuntimeError("init fail")

    class DB:
        async def init_mongodb(self):
            return None

    async def _ok_close():
        return None

    monkeypatch.setattr(
        "zstock.common.utils.db_utils.init_zstock_database",
        _fail_init,
    )
    import zstock.common.utils.db_utils as db_mod

    monkeypatch.setattr(db_mod, "db_manager", DB())
    monkeypatch.setattr(db_mod, "close_zstock_database", _ok_close)
    monkeypatch.setattr(ob, "preflight_checks", AsyncMock(return_value={"ok": True, "issues": []}))
    monkeypatch.setattr(ob, "backup_strategy_params", lambda d: d / "bak.json")
    monkeypatch.setattr(
        ob, "check_factor_coverage_by_year", AsyncMock(return_value={"by_year": {}})
    )

    async def _empty(*a, **k):
        return {}

    monkeypatch.setattr(
        "zstock.strategy_management.script.grid_search_real.load_ohlcv",
        _empty,
    )
    # empty ohlcv → 1
    assert await ob.async_main(args) == 1
