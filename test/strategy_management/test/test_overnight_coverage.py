"""overnight_backtest：worker stubs、precompute/eval mock、generate_report 边角。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from zstock.strategy_management.script import overnight_backtest as ob


@pytest.mark.asyncio
async def test_run_precompute_and_factor_eval_mocked(tmp_path, monkeypatch):
    class _QS:
        async def ensure_indexes(self):
            return None

    class _Svc:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def precompute_date_range(self, start, end, lookback_days=60):
            return {"days": 2, "ok": 2}

    monkeypatch.setattr(
        "zstock.data_management.query_service.get_data_query_service",
        lambda: _QS(),
    )
    monkeypatch.setattr(
        "zstock.factor_management.script.precompute_factors.FactorPrecomputeService",
        _Svc,
    )
    stats = await ob.run_precompute(
        "2024-01-02",
        "2024-01-10",
        30,
        workers=1,
        load_workers=1,
        query_workers=1,
        resource_fraction=0.5,
    )
    assert stats["ok"] == 2

    class _Eval:
        def __init__(self, **kwargs):
            self.output_dir = kwargs.get("output_dir")

        async def run(self):
            p = Path(self.output_dir)
            p.mkdir(parents=True, exist_ok=True)
            (p / "summary.csv").write_text("factor,ic\na,0.1\n", encoding="utf-8")

    async def _uni(*a, **k):
        return None

    monkeypatch.setattr(
        "zstock.factor_management.script.factor_evaluation.FactorEvaluation",
        _Eval,
    )
    monkeypatch.setattr(
        "zstock.strategy_management.strategy_section.universe.build_universe_by_date",
        _uni,
    )
    path = await ob.run_factor_eval(
        "2024-01-02",
        "2024-01-10",
        tmp_path,
        "2024",
        plot=False,
        period=20,
    )
    assert path and Path(path).is_file()


def test_generate_report_minimal_and_csv(tmp_path):
    periods = ob._default_periods("2026-08-24")
    payload = {
        "current_2024": {
            "status": "success",
            "total_return": 0.1,
            "annualized_return": 0.08,
            "sharpe": 1.0,
            "calmar": 0.5,
            "max_drawdown": -0.1,
            "avg_turnover": 0.2,
            "avg_exposure": 0.8,
            "start": "2024-01-01",
            "end": "2024-12-31",
        },
        "benchmarks": {
            "bench_2024": {
                "status": "success",
                "total_return": 0.05,
                "annualized_return": 0.04,
                "max_drawdown": -0.08,
            }
        },
        "factor_coverage": {"by_year": {"2024": {"market_days": 10}}},
        "precompute_stats": {"days": 1},
        "factor_eval": {"2024": str(tmp_path / "summary.csv")},
    }
    (tmp_path / "summary.csv").write_text("factor,rank_ic\nsector.x,0.1\n", encoding="utf-8")
    text = ob.generate_report(payload, periods, {"version": "t"})
    assert "隔夜策略回测完整报告" in text
    csv_path = tmp_path / "cmp.csv"
    ob._results_to_comparison_csv(payload, csv_path)
    assert csv_path.is_file()


def test_load_checkpoint_missing_and_mark(tmp_path):
    assert ob._load_checkpoint(tmp_path) == {"completed_steps": [], "results": {}}
    ckpt = {"completed_steps": [], "results": {}, "output_dir": str(tmp_path)}
    ob._mark_done(ckpt, "preflight", {"ok": True})
    assert ob._step_done(ckpt, "preflight")
    loaded = ob._load_checkpoint(tmp_path)
    assert "preflight" in loaded["completed_steps"]


def test_count_steps_variants():
    periods = ob._default_periods("2026-08-24")
    args = ob.build_parser().parse_args(
        ["--skip-precompute", "--skip-eval", "--skip-grid", "--skip-sensitivity", "--no-quarters"]
    )
    n = ob._count_steps(args, periods)
    assert n >= 3
    args2 = ob.build_parser().parse_args([])
    assert ob._count_steps(args2, periods) > n


def test_main_entry(monkeypatch):
    async def _fake_async_main(args):
        return 0

    monkeypatch.setattr(ob, "async_main", _fake_async_main)
    monkeypatch.setattr(
        "sys.argv",
        ["overnight_backtest", "--skip-precompute", "--skip-eval", "--skip-grid", "--skip-sensitivity"],
    )
    assert ob.main() == 0


@pytest.mark.asyncio
async def test_preflight_mongo_fail(monkeypatch, tmp_path):
    class _DB:
        async def command(self, *_a, **_k):
            raise RuntimeError("down")

        def __getitem__(self, name):
            class _C:
                async def estimated_document_count(self):
                    raise RuntimeError("no")

            return _C()

    class _Mgr:
        mongo_db = _DB()

    monkeypatch.setattr("zstock.common.utils.db_utils.db_manager", _Mgr())
    # strategy params path exists in repo
    info = await ob.preflight_checks()
    assert info["ok"] is False or info["issues"]
