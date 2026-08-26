"""隔夜回测脚本的纯函数与真实 overnight_v16 产物。"""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

import pandas as pd
import pytest

from zstock.strategy_management.script import overnight_backtest as ob
from zstock.strategy_management.test.conftest import (
    DummySignalGenerator,
    ReplayFactorPipeline,
)
from zstock.strategy_management.script.overnight_backtest import (
    _CHECKPOINT_FILE,
    _avg_exposure,
    _append_range,
    _backtest_jobs_current,
    _benchmark_jobs,
    _clip_range,
    _count_steps,
    _default_periods,
    _default_rebalance_freq,
    _elapsed_str,
    _eval_windows,
    _grid_oos_jobs_2024,
    _grid_oos_jobs_2025,
    _grid_oos_jobs_2026,
    _load_checkpoint,
    _load_strategy_summary,
    _mark_done,
    _max_date,
    _min_date,
    _periods_for_year,
    _quarterly_jobs,
    _quiet_loggers,
    _results_to_comparison_csv,
    _save_checkpoint,
    _sensitivity_jobs,
    _serialize_results,
    _setup_console_utf8,
    _setup_logging,
    _step_done,
    _today_str,
    _top_factors,
    backup_strategy_params,
    build_parser,
    generate_report,
)


def test_date_helpers():
    assert _min_date("2024-01-02", "2024-06-03") == "2024-01-02"
    assert _max_date("2024-01-02", "2024-06-03") == "2024-06-03"
    assert _elapsed_str(3661) == "01:01:01"
    assert len(_today_str()) == 10
    assert _today_str()[4] == "-"


def test_clip_and_quarter_jobs():
    assert _clip_range("2026-10-01", "2026-12-31", "2026-08-24") is None
    assert _clip_range("2024-01-01", "2024-12-31", "2024-06-30") == ("2024-01-01", "2024-06-30")
    q = _quarterly_jobs(2024, "2024-08-24")
    keys = [j[0] for j in q]
    assert "current_2024_q3" in keys
    assert "current_2024_q4" not in keys


def test_periods_and_jobs_match_overnight_window():
    periods = _default_periods("2026-08-24")
    assert periods["is_2024_end"] == "2024-09-30"
    assert periods["full_2026_end"] == "2026-08-24"
    jobs = _backtest_jobs_current(periods, include_quarters=True)
    assert any(j[0] == "current_2024" for j in jobs)
    assert any(j[0] == "current_2026_q3" for j in jobs)
    y = _periods_for_year(2024, "2024-12-31")
    yjobs = _backtest_jobs_current(y, include_quarters=True)
    assert yjobs[0][0] == "current_full"
    assert _benchmark_jobs(y)[0][0] == "bench_2024"
    assert _eval_windows(y)[0][0] == "2024"
    assert len(_sensitivity_jobs(periods)) == 9
    assert len(_grid_oos_jobs_2024(periods)) == 3
    assert len(_grid_oos_jobs_2025(periods)) == 3
    fwd = _grid_oos_jobs_2026(periods)
    assert any(j[0] == "grid26_fwd_h2" for j in fwd)


def test_strategy_summary_and_rebalance_from_ssot(strategy_params):
    lines = _load_strategy_summary()
    assert any(strategy_params["version"] in x for x in lines)
    assert _default_rebalance_freq() == int(strategy_params["backtest"]["rebalance_freq"])


def test_avg_exposure_and_serialize():
    assert _avg_exposure([]) == 0.0
    log = [
        {"holdings": [{"weight": 0.2}, {"weight": 0.2}]},
        {"holdings": []},
    ]
    assert _avg_exposure(log) == pytest.approx(0.2)
    ser = _serialize_results({"a": {"x": 1, "nested": {"k": 1}}, "b": [1, 2], "c": object()})
    assert ser["a"] == {"x": 1}
    assert ser["b"] == [1, 2]
    assert isinstance(ser["c"], str)


def test_checkpoint_roundtrip_on_real_file(overnight_dir, tmp_path):
    loaded = _load_checkpoint(overnight_dir)
    assert "preflight" in loaded["completed_steps"]
    assert loaded["results"]["preflight"]["mongo"] == "ok"
    _save_checkpoint(tmp_path, {"completed_steps": ["preflight"], "results": {}})
    assert (tmp_path / _CHECKPOINT_FILE).is_file()
    ckpt = {"completed_steps": [], "results": {}, "output_dir": str(tmp_path)}
    assert _step_done(ckpt, "preflight") is False
    _mark_done(ckpt, "preflight", {"ok": True})
    assert _step_done(ckpt, "preflight") is True


def test_top_factors_from_real_eval(factor_eval_2024_csv):
    lines = _top_factors(str(factor_eval_2024_csv), top_n=8)
    assert lines
    assert any("sector." in x or "dragon." in x or "force." in x or "market." in x for x in lines)
    assert _top_factors(str(factor_eval_2024_csv.parent / "nope.csv"))[0].startswith("    (读取失败")


def test_generate_report_from_overnight_v16(overnight_results, overnight_checkpoint, tmp_path):
    periods = _default_periods("2026-08-24")
    payload = dict(overnight_results)
    cov = overnight_checkpoint.get("results", {}).get("factor_coverage")
    if cov:
        payload["factor_coverage"] = cov
    payload["benchmarks"] = {
        "bench_2024": overnight_checkpoint["results"]["bench_bench_2024"],
        "bench_2025": overnight_checkpoint["results"]["bench_bench_2025"],
        "bench_2026": overnight_checkpoint["results"]["bench_bench_2026"],
    }
    payload["precompute_stats"] = overnight_checkpoint.get("results", {}).get("precompute") or {}
    manifest = overnight_results.get("manifest") or {}
    text = generate_report(payload, periods, manifest)
    assert "隔夜策略回测完整报告" in text
    assert "37.53%" in text
    assert "1.665" in text
    assert "沪深300 基准" in text
    assert "超额(Alpha)" in text
    assert "因子覆盖" in text
    csv_path = tmp_path / "comparison.csv"
    _results_to_comparison_csv(payload, csv_path)
    df = pd.read_csv(csv_path)
    assert "current_2024" in set(df["key"])


def test_backup_and_parser(tmp_path, strategy_params):
    dst = backup_strategy_params(tmp_path)
    copied = json.loads(dst.read_text(encoding="utf-8"))
    assert copied["version"] == strategy_params["version"]
    args = build_parser().parse_args(
        ["--skip-precompute", "--skip-eval", "--skip-grid", "--skip-sensitivity", "--no-quarters", "--year", "2024"]
    )
    periods = _periods_for_year(2024, "2024-12-31")
    n = _count_steps(args, periods)
    assert n >= 3


def test_clear_strategy_caches():
    from zstock.strategy_management.pipeline import StrategyPipeline
    from zstock.strategy_management.risk_manager import _load_risk_limits_from_config

    StrategyPipeline._default_config()
    _load_risk_limits_from_config()
    ob._clear_strategy_caches()
    assert StrategyPipeline._config_cache is None
    assert _load_risk_limits_from_config._cache is None


def test_logging_helpers(tmp_path):
    _setup_console_utf8()
    _quiet_loggers()
    _setup_logging(tmp_path / "run.log")
    logging.getLogger("zstock.strategy_management").info("hello")
    assert (tmp_path / "run.log").is_file()
    counter = ob._StepCounter(2)
    counter.next("unit")
    assert counter.current == 1


def test_append_range_skips_clipped():
    jobs = []
    _append_range(jobs, "x", "2026-10-01", "2026-12-31", "Q4", "2026-08-24")
    assert jobs == []
    _append_range(jobs, "y", "2024-01-01", "2024-03-31", "Q1", "2024-12-31")
    assert jobs[0][0] == "y"


@pytest.mark.asyncio
async def test_run_backtest_current_uses_exported_jan_window(
    ohlcv_by_code, signals_bundle, tmp_path, monkeypatch
):
    from zstock.strategy_management.script.backtester import make_ohlcv_provider_from_dict
    from zstock.strategy_management.test.conftest import ReplayFactorPipeline

    replay = ReplayFactorPipeline(signals_bundle)
    monkeypatch.setattr(
        "zstock.factor_management.pipeline.CrossSectionStrategyPipeline",
        lambda *a, **k: replay,
    )
    monkeypatch.setattr(
        "zstock.strategy_management.pipeline.SignalGenerator",
        DummySignalGenerator,
    )
    row = await ob.run_backtest_current(
        make_ohlcv_provider_from_dict(ohlcv_by_code),
        "2024-01-02",
        "2024-01-10",
        1_000_000.0,
        0.0015,
        5,
        tmp_path,
        "ut_jan",
    )
    assert row["status"] == "success"
    assert row["start"] == "2024-01-02"
    assert (tmp_path / "segments" / "ut_jan" / "summary.txt").is_file()


@pytest.mark.asyncio
async def test_run_backtest_current_failure_is_recorded(tmp_path, monkeypatch):
    async def _boom(*_a, **_k):
        raise RuntimeError("pipeline boom")

    class _BT:
        def __init__(self, **kwargs):
            pass

        async def run(self, **kwargs):
            raise RuntimeError("pipeline boom")

    monkeypatch.setattr("zstock.strategy_management.script.backtester.Backtester", _BT)
    monkeypatch.setattr(
        "zstock.factor_management.pipeline.CrossSectionStrategyPipeline",
        lambda *a, **k: object(),
    )
    row = await ob.run_backtest_current(
        lambda td: {}, "2024-01-02", "2024-01-10", 1e6, 0.0015, 5, tmp_path, "ut_fail"
    )
    assert row["status"].startswith("failed")


@pytest.mark.asyncio
async def test_consistency_run_checks_skip_missing_precompute(monkeypatch):
    from zstock.strategy_management.script import signal_consistency_check as scc

    class _Svc:
        async def validate_consistency(self, td, **kwargs):
            raise ValueError(f"无预计算 M1 数据: {td}")

    async def _init():
        return None

    async def _close():
        return None

    monkeypatch.setattr("zstock.common.utils.db_utils.init_zstock_database", _init)
    monkeypatch.setattr("zstock.common.utils.db_utils.close_zstock_database", _close)
    monkeypatch.setattr(
        "app.services.strategy_signal_service.get_strategy_signal_service",
        lambda: _Svc(),
    )
    rc = await scc._run_checks(["2024-06-03"], include_pipeline=False, tolerance=1e-6)
    assert rc == 1


@pytest.mark.asyncio
async def test_compute_benchmark_hs300_jan(monkeypatch):
    import json
    from pathlib import Path

    payload = json.loads(
        (Path(__file__).resolve().parent / "fixtures" / "hs300_202401.json").read_text(encoding="utf-8")
    )
    df = pd.DataFrame(payload["rows"])

    class _QS:
        async def get_ohlcv(self, code, start, end, period="daily"):
            assert code == "399300"
            return df, None

    monkeypatch.setattr(
        "zstock.data_management.query_service.get_data_query_service",
        lambda: _QS(),
    )
    row = await ob.compute_benchmark("2024-01-02", "2024-01-16")
    assert row["status"] == "success"
    assert row["index"] == "399300"
    c0, c1 = float(df.sort_values("trade_date")["close"].iloc[0]), float(df.sort_values("trade_date")["close"].iloc[-1])
    assert row["total_return"] == pytest.approx(c1 / c0 - 1.0)


@pytest.mark.asyncio
async def test_run_backtest_grid_replays_recorded_is2024(overnight_results):
    recorded = overnight_results["is_main_2024"]

    class _Opt:
        async def run_one(self, params, provider, start, end, capital, fee):
            return dict(recorded)

    row = await ob.run_backtest_grid(_Opt(), {}, lambda td: {}, "2024-10-01", "2024-12-31", 1e6, 0.0015, "Grid24 OOS")
    assert row["status"] == "success"
    assert row["sharpe"] == recorded["sharpe"]
    assert row["label"] == "Grid24 OOS"


@pytest.mark.asyncio
async def test_preflight_and_factor_coverage_use_recorded_counts(monkeypatch, overnight_checkpoint):
    cov = overnight_checkpoint["results"]["factor_coverage"]["by_year"]["2024"]

    class _Coll:
        def __init__(self, n):
            self._n = n

        async def count_documents(self, *_a, **_k):
            return self._n

        async def estimated_document_count(self):
            return overnight_checkpoint["results"]["preflight"]["stock_info_count"]

    class _DB:
        def __getitem__(self, name):
            mapping = {
                "zstock_factor_market": cov["market_days"],
                "zstock_factor_sector": cov["sector_docs"],
                "zstock_factor_dragon": cov["dragon_docs"],
                "zstock_factor_force": cov["force_docs"],
                "zstock_stock_info": overnight_checkpoint["results"]["preflight"]["stock_info_count"],
            }
            return _Coll(mapping.get(name, 0))

        async def command(self, *_a, **_k):
            return {"ok": 1}

    class _Mgr:
        mongo_db = _DB()

    monkeypatch.setattr("app.core.database.db_manager", _Mgr())
    info = await ob.preflight_checks()
    assert info["mongo"] == "ok"
    assert info["stock_info_count"] == 5558
    year = await ob.check_factor_coverage("2024-01-01", "2024-12-31")
    assert year["market_days"] == 242
    by = await ob.check_factor_coverage_by_year(_default_periods("2026-08-24"))
    assert "2024" in by["by_year"]


@pytest.mark.asyncio
async def test_async_main_resume_replays_real_checkpoint(overnight_dir, tmp_path):
    """用真实 checkpoint 断点续跑：跳过已完成步骤，只重新生成报告（不编造回测结果）。"""
    import shutil

    work = tmp_path / "resume"
    shutil.copytree(overnight_dir, work, dirs_exist_ok=True)
    args = build_parser().parse_args(
        [
            "--resume",
            "--output",
            str(work),
            "--skip-precompute",
            "--skip-eval",
            "--skip-grid",
            "--skip-sensitivity",
            "--end-date",
            "2026-08-24",
        ]
    )

    async def _ok_init():
        return None

    async def _ok_close():
        return None

    async def _no_ohlcv(*_a, **_k):
        return {"399300": pd.DataFrame({"trade_date": ["2024-01-02"], "close": [3500.0]})}

    with patch("zstock.common.utils.db_utils.init_zstock_database", _ok_init), patch(
        "zstock.common.utils.db_utils.close_zstock_database", _ok_close
    ), patch(
        "zstock.factor_management.script.网格搜索.grid_search_real.load_ohlcv",
        _no_ohlcv,
    ):
        rc = await ob.async_main(args)
    assert rc == 0
    report = (work / "overnight_backtest_report.txt").read_text(encoding="utf-8")
    assert "隔夜策略回测完整报告" in report
    assert (work / "overnight_backtest_results.json").is_file()
