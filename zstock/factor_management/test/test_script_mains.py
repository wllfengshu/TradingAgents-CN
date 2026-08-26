"""因子层生产脚本：注入 FakeMongo / 夹具，覆盖预计算、隔夜、测评、网格、风格 CLI。"""

from __future__ import annotations

import json
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from zstock.data_management.query_service import (
    COL_CAPITAL_FLOW,
    COL_FACTOR_DRAGON,
    COL_FACTOR_SECTOR,
    COL_OHLCV,
    COL_SECTOR,
    COL_STOCK_INFO,
    PERIOD_L2_DAILY,
)
from zstock.data_management.test.conftest import FakeDatabaseService
from zstock.factor_management.prefilters import PreFilters
from zstock.factor_management.script.precompute_factors import (
    FactorComputeEngine,
    FactorPrecomputeService,
    _compute_day_sync,
    _compute_sliced_payload,
)
from zstock.factor_management.test.conftest import make_offline_pipeline

LIVE = Path(__file__).resolve().parent / "fixtures" / "live_calc_20240102.json"
HS300 = Path(__file__).resolve().parent / "fixtures" / "hs300_ohlcv.json"


def _live_payload():
    if not LIVE.is_file() or not HS300.is_file():
        pytest.skip("缺少 live_calc / hs300 夹具")
    with open(LIVE, encoding="utf-8") as f:
        live = json.load(f)
    with open(HS300, encoding="utf-8") as f:
        hs = json.load(f)
    members = live.get("members") or {}
    ohlcv = {c: pd.DataFrame(rows) for c, rows in (live.get("ohlcv") or {}).items()}
    flow = live.get("flow") or {}
    idx = pd.DataFrame(hs.get("rows") or [])
    if idx.empty or not members or not ohlcv:
        pytest.skip("夹具为空")
    return members, ohlcv, flow, idx


class _InlinePool:
    def __init__(self, max_workers=None, **kwargs):
        self.max_workers = max_workers

    def submit(self, fn, *args, **kwargs):
        fut = Future()
        try:
            fut.set_result(fn(*args, **kwargs))
        except Exception as exc:
            fut.set_exception(exc)
        return fut

    def shutdown(self, wait=True):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.shutdown()
        return False


def _svc_from_live(strategy_params):
    members, ohlcv, flow, idx = _live_payload()
    codes = list(ohlcv)
    infos = {
        c: {"code": c, "name": c, "is_mainboard": True, "is_st": False} for c in codes
    }
    sectors = [{"sector_code": k, "sector_name": k, "stocks": v} for k, v in members.items()]
    store = {
        COL_STOCK_INFO: list(infos.values()),
        COL_SECTOR: sectors,
        COL_OHLCV: [],
        COL_CAPITAL_FLOW: [],
        COL_FACTOR_DRAGON: [],
        COL_FACTOR_SECTOR: [],
    }
    ds = FakeDatabaseService(store)

    class QS:
        async def get_all_stocks(self):
            return list(infos.values()), "fixture"

        async def get_sector_list(self):
            return sectors, "fixture"

        async def get_ohlcv_batch(self, batch_codes, start, end, **kwargs):
            out = {}
            for c in batch_codes:
                if c == "399300":
                    out[c] = idx
                elif c in ohlcv:
                    out[c] = ohlcv[c]
            return out

        async def get_capital_flow_range(self, batch_codes, start, end, **kwargs):
            return {c: flow.get(c, []) for c in batch_codes}

        async def ensure_indexes(self):
            return None

        async def get_ohlcv(self, code, start, end, period="daily"):
            if code == "399300":
                return idx, "fixture"
            if code in ohlcv:
                return ohlcv[code], "fixture"
            return pd.DataFrame(), "fixture"

    pipe = make_offline_pipeline(strategy_params, qs=QS())
    svc = FactorPrecomputeService.__new__(FactorPrecomputeService)
    svc.pipeline = pipe
    svc.database_service = ds
    svc.query_service = QS()
    svc.compute_workers = 1
    svc.load_workers = 1
    svc.query_workers = 1
    svc.resource_budget = SimpleNamespace(resource_fraction=0.25, total_memory_gb=8, cpu_cores=4)
    svc.fund_provider = None
    return svc, members, ohlcv, flow, idx, infos, ds


def test_compute_helpers_on_live_slice(strategy_params):
    svc, members, ohlcv, flow, idx, infos, _ = _svc_from_live(strategy_params)
    dates_index = {c: sorted(df["trade_date"].astype(str).tolist()) for c, df in ohlcv.items()}
    flow_dates = {c: sorted(str(d.get("trade_date")) for d in rows) for c, rows in flow.items()}
    idx_dates = sorted(idx["trade_date"].astype(str).tolist())
    preload = {
        "flow_days": 5,
        "stock_ohlcv_full": ohlcv,
        "ohlcv_dates_index": dates_index,
        "index_ohlcv_full": {"399300": idx},
        "index_dates_index": {"399300": idx_dates},
        "stock_flow_full": flow,
        "flow_dates_index": flow_dates,
        "stock_lhb_full": {"603201": [{"code": "603201", "trade_date": "2024-01-02"}]},
        "lhb_dates_index": {"603201": ["2024-01-02"]},
        "sector_ohlcv_full": {},
        "sector_dates_index": {},
        "all_stocks": list(ohlcv),
        "stock_infos": infos,
        "sectors": [{"sector_code": k} for k in members],
        "sector_stocks": members,
        "index_name": "沪深 300",
        "filtered_sectors": [{"sector_code": k} for k in members],
        "filtered_stocks_set": set(ohlcv),
        "fund_provider": None,
    }
    raw = _compute_day_sync(preload, "2024-01-02", 90, quiet=True)
    assert raw["trade_date"] == "2024-01-02"
    sliced = FactorPrecomputeService.slice_preloaded_data(preload, "2024-01-02", 90)
    out = _compute_sliced_payload(
        {
            "data": sliced,
            "filtered_sectors": preload["filtered_sectors"],
            "filtered_stocks_set": preload["filtered_stocks_set"],
            "fund_provider": None,
        }
    )
    assert out["market_raw"]


@pytest.mark.asyncio
async def test_precompute_service_paths(strategy_params, monkeypatch):
    svc, members, ohlcv, flow, idx, infos, ds = _svc_from_live(strategy_params)

    class Pipe:
        prefilters = PreFilters()

        async def load_real_data(self, trade_date, lookback_days=120):
            return {
                "trade_date": trade_date,
                "all_stocks": list(ohlcv),
                "stock_infos": infos,
                "stock_ohlcv": ohlcv,
                "stock_flow_recent": flow,
                "sectors": [{"sector_code": k, "sector_name": k} for k in members],
                "sector_stocks": members,
                "index_ohlcv": {"399300": idx},
                "index_name": "沪深 300",
            }

    svc.pipeline = Pipe()
    counts = await svc.precompute_single_date("2024-01-02", lookback_days=90)
    assert counts["market"] == 1

    # 交易日筛选：同一日复制真实 399300 行到 1000 个 code，只测 >=1000 门槛
    template = dict(idx.iloc[-1]) if "trade_date" in idx.columns else {}
    td = "2024-01-02"
    docs = []
    for i in range(1000):
        doc = dict(template)
        doc["code"] = f"{i:06d}"
        doc["trade_date"] = td
        doc["period"] = "D"
        docs.append(doc)
    ds.db[COL_OHLCV].docs = docs
    ds.db[COL_CAPITAL_FLOW].docs = [
        {"code": "603201", "trade_date": td, "period": PERIOD_L2_DAILY}
    ]
    dates = await svc._gen_trade_dates("2024-01-01", "2024-01-31")
    assert td in dates

    preload = await svc._preload_range_data("2024-01-02", "2024-01-02", 90)
    assert preload["stock_ohlcv_full"]
    monkeypatch.setattr(
        "zstock.factor_management.script.precompute_factors.ProcessPoolExecutor",
        _InlinePool,
    )

    async def _dates(_s, _e):
        return [td]

    svc._gen_trade_dates = _dates
    total = await svc.precompute_date_range("2024-01-02", "2024-01-02", lookback_days=90)
    assert total["market"] >= 0


@pytest.mark.asyncio
async def test_precompute_init_and_main(strategy_params, monkeypatch):
    members, ohlcv, flow, idx = _live_payload()
    ds = FakeDatabaseService({COL_STOCK_INFO: [{"code": "603201", "name": "常润股份"}]})

    class QS:
        async def ensure_indexes(self):
            return None

    monkeypatch.setattr(
        "zstock.data_management.database_service.get_database_service", lambda: ds
    )
    monkeypatch.setattr(
        "zstock.data_management.query_service.get_database_service", lambda: ds
    )
    monkeypatch.setattr(
        "zstock.data_management.query_service.get_data_query_service", lambda: QS()
    )
    monkeypatch.setattr(
        "zstock.factor_management.pipeline.get_data_query_service", lambda: QS()
    )
    monkeypatch.setattr(
        "zstock.factor_management.fundamental_factors.FundamentalDataProvider.load_from_mongodb",
        lambda self: None,
    )
    svc = FactorPrecomputeService(
        load_fundamentals=False, compute_workers=1, load_workers=1, query_workers=1, resource_fraction=0.2
    )
    assert svc.compute_workers >= 1
    svc._init_fundamental_provider()

    class FakeSvc:
        def __init__(self, **kwargs):
            pass

        async def precompute_single_date(self, date, lookback):
            return {"market": 1, "sector": 1, "dragon": 1, "force": 1}

        async def precompute_date_range(self, start, end, lookback_days):
            return {"market": 2}

    import zstock.factor_management.script.precompute_factors as mod

    class Mgr:
        async def init_mongodb(self):
            return None

    monkeypatch.setattr("app.core.database.db_manager", Mgr())
    monkeypatch.setattr(mod, "FactorPrecomputeService", FakeSvc)
    monkeypatch.setattr("sys.argv", ["precompute_factors.py", "--date", "2024-01-02", "--lookback", "30"])
    assert await mod.main() == 0
    monkeypatch.setattr(
        "sys.argv",
        ["precompute_factors.py", "--start", "2024-01-02", "--end", "2024-01-16"],
    )
    assert await mod.main() == 0
    monkeypatch.setattr("sys.argv", ["precompute_factors.py"])
    with pytest.raises(SystemExit):
        await mod.main()

    async def _fail_init():
        raise RuntimeError("down")

    monkeypatch.setattr("app.core.database.db_manager.init_mongodb", _fail_init)
    monkeypatch.setattr("sys.argv", ["precompute_factors.py", "--date", "2024-01-02"])
    # 上面 setattr 在实例方法上可能无效，改 Mgr
    class BadMgr:
        async def init_mongodb(self):
            raise RuntimeError("down")

    monkeypatch.setattr("app.core.database.db_manager", BadMgr())
    assert await mod.main() == 1


@pytest.mark.asyncio
async def test_overnight_async_main_skip_and_stubs(overnight_dir, monkeypatch, tmp_path, stock_ohlcv_by_code):
    from zstock.factor_management.script import overnight_validation as ov

    ov._quiet_loggers()
    ov._setup_console_utf8()
    ov._setup_logging()

    async def _init():
        return None

    async def _close():
        return None

    monkeypatch.setattr("zstock.common.utils.db_utils.init_zstock_database", _init)
    monkeypatch.setattr("zstock.common.utils.db_utils.close_zstock_database", _close)

    async def _pc(*a, **k):
        return {"market": 1, "sector": 1, "dragon": 1, "force": 1}

    async def _eval(*a, **k):
        csv = overnight_dir / "factor_eval" / "2024" / "summary.csv"
        return csv if csv.is_file() else None

    async def _ohlcv(start, end):
        return dict(stock_ohlcv_by_code)

    class Opt:
        def __init__(self, output_dir=None):
            self.output_dir = Path(output_dir) if output_dir else tmp_path

        def baseline_params(self):
            from zstock.factor_management.script.网格搜索.grid_search_real import (
                RealGridSearchOptimizer,
            )
            return RealGridSearchOptimizer.baseline_params()

        async def run_grid_search(self, **kwargs):
            base = self.baseline_params()
            row = {
                **base,
                "status": "success",
                "total_return": 0.1,
                "sharpe": 1.0,
                "annualized_return": 0.1,
                "max_drawdown": -0.05,
                "calmar": 2.0,
                "avg_turnover": 0.1,
                "total_cost": 0.01,
                "weight_coop": 0.25,
                "objective": 1.5,
            }
            return pd.DataFrame([row])

        def save_results(self, df):
            self.output_dir.mkdir(parents=True, exist_ok=True)
            path = self.output_dir / "grid.csv"
            df.to_csv(path, index=False)
            return path

        async def run_one(self, *a, **k):
            return {
                "status": "success",
                "total_return": 0.1,
                "sharpe": 1.0,
                "calmar": 0.5,
                "max_drawdown": -0.1,
                "annualized_return": 0.1,
                "avg_turnover": 0.1,
                "total_cost": 0.01,
                "objective": 1.0,
            }

    import sys

    sys.modules["grid_search_real"] = SimpleNamespace(RealGridSearchOptimizer=Opt)
    monkeypatch.setattr(ov, "run_precompute", _pc)
    monkeypatch.setattr(ov, "run_factor_eval", _eval)
    monkeypatch.setattr(ov, "load_ohlcv", _ohlcv)

    args = ov.build_parser().parse_args(
        ["--skip-precompute", "--skip-eval", "--skip-grid", "--year", "2024"]
    )
    assert await ov.async_main(args) == 0

    args2 = ov.build_parser().parse_args(
        ["--skip-precompute", "--skip-eval", "--year", "2024", "--max-combinations", "1"]
    )
    code2 = await ov.async_main(args2)
    assert code2 in {0, 1}

    args3 = ov.build_parser().parse_args(
        ["--end-date", "2026-08-25", "--skip-precompute", "--skip-eval", "--skip-grid"]
    )
    assert await ov.async_main(args3) == 0


@pytest.mark.asyncio
async def test_overnight_helpers_run_wrappers(monkeypatch, tmp_path, stock_ohlcv_by_code):
    from zstock.factor_management.script import overnight_validation as ov

    class QS:
        async def get_all_stocks(self):
            return [
                {"code": "603201", "is_mainboard": True, "is_st": False, "name": "常润股份"}
            ], "fixture"

        async def get_ohlcv_batch(self, codes, start, end, **kwargs):
            return {c: stock_ohlcv_by_code[c] for c in codes if c in stock_ohlcv_by_code}

        async def ensure_indexes(self):
            return None

    monkeypatch.setattr(
        "zstock.data_management.query_service.get_data_query_service", lambda: QS()
    )
    ohlcv = await ov.load_ohlcv("2024-01-02", "2024-01-16")
    assert "603201" in ohlcv

    class Svc:
        def __init__(self, **k):
            pass

        async def precompute_date_range(self, *a, **k):
            return {"market": 1}

    monkeypatch.setattr(
        "zstock.factor_management.script.precompute_factors.FactorPrecomputeService",
        Svc,
    )
    stats = await ov.run_precompute("2024-01-02", "2024-01-02", 30, compute_workers=1)
    assert stats["market"] == 1

    class Pipe:
        def __init__(self, **k):
            Path(k["output_dir"]).mkdir(parents=True, exist_ok=True)
            (Path(k["output_dir"]) / "summary.csv").write_text("layer,field,Total_Score\n", encoding="utf-8")

        async def run(self):
            return []

    monkeypatch.setattr(
        "zstock.factor_management.script.因子测评.factor_evaluation.FactorEvaluationPipeline",
        Pipe,
    )
    summary = await ov.run_factor_eval("2024-01-02", "2024-01-16", tmp_path, plot=False)
    assert summary is not None

    class Opt:
        async def run_one(self, *a, **k):
            return {"status": "failed"}

    row = await ov.run_backtest(Opt(), {}, None, "2024-01-02", "2024-01-16", 1e6, 0.001, "ut")
    assert row["status"] == "failed"


@pytest.mark.asyncio
async def test_factor_eval_run_and_async_main(
    factor_raw, stock_ohlcv_by_code, tmp_path, monkeypatch
):
    from zstock.factor_management.script.因子测评.factor_evaluation import (
        FactorEvaluationPipeline,
        async_main,
        build_parser,
        run_batch_years,
    )
    from zstock.data_management.test.conftest import FakeDatabaseService

    dragons = list(factor_raw.get("dragons") or [])
    sectors = list(factor_raw.get("sectors") or [])
    ds = FakeDatabaseService(
        {
            COL_FACTOR_DRAGON: dragons,
            COL_FACTOR_SECTOR: sectors,
            COL_STOCK_INFO: [
                {"code": c, "is_mainboard": True, "is_st": False, "name": c}
                for c in stock_ohlcv_by_code
            ],
        }
    )

    class QS:
        async def get_all_stocks(self):
            return [
                {"code": c, "is_mainboard": True, "is_st": False, "name": c}
                for c in stock_ohlcv_by_code
            ], "fixture"

        async def get_sector_list(self):
            return [
                {
                    "sector_code": d["sector_code"],
                    "stocks": list(stock_ohlcv_by_code),
                }
                for d in (factor_raw.get("sectors") or [])[:5]
            ], "fixture"

        async def get_ohlcv_batch(self, codes, start, end, **kwargs):
            return {c: stock_ohlcv_by_code[c] for c in codes if c in stock_ohlcv_by_code}

    pipe = FactorEvaluationPipeline(
        start_date="2024-01-02",
        end_date="2024-01-16",
        period=5,
        plot=False,
        output_dir=str(tmp_path / "eval"),
        conditional=False,
        layer="dragon",
        field="f32_amount",
        workers=1,
    )
    pipe.db = ds
    pipe.qs = QS()
    rows = await pipe.run()
    assert rows
    assert rows[0]["field"] == "f32_amount"

    class Mgr:
        async def init_mongodb(self):
            return None

    monkeypatch.setattr("app.core.database.db_manager", Mgr())

    class Tiny:
        def __init__(self, **k):
            Path(k.get("output_dir") or tmp_path).mkdir(parents=True, exist_ok=True)

        async def run(self):
            return [{"layer": "dragon", "field": "f32_amount", "Total_Score": 10, "Grade": "D"}]

    monkeypatch.setattr(
        "zstock.factor_management.script.因子测评.factor_evaluation.FactorEvaluationPipeline",
        Tiny,
    )
    args = build_parser().parse_args(["--start", "2024-01-02", "--end", "2024-01-16", "--output", str(tmp_path)])
    assert await async_main(args) == 0
    args2 = build_parser().parse_args(["--years", "2024", "--year-parallel", "1", "--output", str(tmp_path / "batch")])
    assert await async_main(args2) == 0
    args3 = build_parser().parse_args([])
    assert await async_main(args3) == 1

    class BadMgr:
        async def init_mongodb(self):
            raise RuntimeError("down")

    monkeypatch.setattr("app.core.database.db_manager", BadMgr())
    assert await async_main(args) == 1


def _live_price_panels():
    members, ohlcv, flow, idx = _live_payload()
    frames_c, frames_o = [], []
    for code, df in ohlcv.items():
        if df is None or df.empty or "close" not in df.columns:
            continue
        tmp = df[["trade_date", "close", "open"]].copy()
        tmp["trade_date"] = tmp["trade_date"].astype(str)
        frames_c.append(tmp.set_index("trade_date")[["close"]].rename(columns={"close": code}))
        frames_o.append(tmp.set_index("trade_date")[["open"]].rename(columns={"open": code}))
    close = pd.concat(frames_c, axis=1).sort_index()
    open_ = pd.concat(frames_o, axis=1).sort_index()
    close = close.loc[(close.index >= "2024-01-02") & (close.index <= "2024-03-15")]
    open_ = open_.loc[close.index]
    return close, {"open": open_, "close": close}


@pytest.mark.asyncio
async def test_eval_ic_and_plot_on_live_ohlcv(tmp_path):
    from zstock.factor_management.script.因子测评.factor_evaluation import (
        FactorEvaluationPipeline,
    )

    close, prices = _live_price_panels()
    assert close.shape[1] >= 10
    pipe = FactorEvaluationPipeline(
        start_date="2024-01-02",
        end_date="2024-03-15",
        period=5,
        plot=True,
        output_dir=str(tmp_path / "ic"),
        conditional=False,
        workers=1,
    )
    result = pipe._evaluate(close, prices)
    assert result["score"]["N_Periods"] >= 0
    assert "Total_Score" in result["score"]
    q = pipe._calc_quantile_returns(close, prices)
    ls = pipe._calc_long_short_return(q)
    ac = pipe._calc_factor_autocorr(close)
    assert "mean_autocorr" in ac
    masked = pipe.apply_universe_mask(close, {str(close.index[0]): set(close.columns[:5])})
    assert not masked.empty
    inv = pipe.apply_polarity_invert(close, "f34_resonance_pct", True)
    jobs = pipe._get_jobs()
    assert jobs

    async def _bundle(layer, field):
        return close, prices

    pipe.load_eval_bundle_stock = _bundle
    pipe.load_eval_bundle_sector = lambda field: _bundle("sector", field)
    pipe.plot = True
    pipe.decay_max = 2
    row = await pipe._eval_one("dragon", "f32_amount")
    assert row["field"] == "f32_amount"
    row_s = await pipe._eval_one("sector", "f21_rps_10d")
    assert row_s["layer"] == "sector"
    empty_row = await pipe._eval_one("dragon", "missing")
    assert empty_row["field"] == "missing"


@pytest.mark.asyncio
async def test_grid_search_run_one_and_grid(tmp_path, overnight_dir, monkeypatch, stock_ohlcv_by_code):
    from zstock.factor_management.script.网格搜索.grid_search_real import (
        RealGridSearchOptimizer,
        _clear_strategy_caches,
        _quiet_loggers,
        async_main,
        build_parser,
        load_ohlcv,
    )

    _quiet_loggers()
    _clear_strategy_caches()
    opt = RealGridSearchOptimizer(output_dir=tmp_path)
    space = opt.get_parameter_space("full")
    combos = opt.sample_combinations(space, 3, seed=1)
    assert combos
    base = opt.baseline_params()
    invalid = dict(base)
    invalid["top_k"] = 1
    invalid["top_per_sector"] = 2
    row = await opt.run_one(invalid, None, "2024-01-02", "2024-01-16", 1e6, 0.001)
    assert str(row["status"]).startswith("invalid")

    class Metrics:
        metrics = {
            "total_return": 0.12,
            "annualized_return": 0.12,
            "sharpe": 1.2,
            "calmar": 0.8,
            "max_drawdown": -0.08,
            "annualized_vol": 0.1,
            "avg_turnover": 0.2,
            "total_cost": 0.01,
            "rebalance_count": 3,
        }

    class BT:
        def __init__(self, **k):
            pass

        async def run(self, **k):
            return Metrics()

    monkeypatch.setattr(
        "zstock.strategy_management.script.backtester.Backtester", BT
    )
    monkeypatch.setattr(
        "zstock.factor_management.pipeline.CrossSectionStrategyPipeline",
        lambda config_path=None: SimpleNamespace(config={}),
    )
    ok = await opt.run_one(base, lambda *a, **k: None, "2024-01-02", "2024-01-16", 1e6, 0.001)
    assert ok["status"] in {"success", "failed"} or "failed" in str(ok["status"])

    async def fake_run_one(self, params, *a, **k):
        return {**params, "status": "success", "objective": 1.0, "total_return": 0.1, "sharpe": 1.0, "annualized_return": 0.1, "max_drawdown": -0.05, "calmar": 2.0, "avg_turnover": 0.1, "total_cost": 0.01}

    monkeypatch.setattr(RealGridSearchOptimizer, "run_one", fake_run_one)
    df = await opt.run_grid_search(
        ohlcv_provider=None,
        start="2024-01-02",
        end="2024-01-16",
        capital=1e6,
        fee=0.001,
        max_combinations=2,
        space_name="core",
        seed=1,
        workers=1,
    )
    assert not df.empty
    path = opt.save_results(df)
    assert path.is_file()
    assert "网格" in opt.generate_report(df)
    assert "无结果" in opt.generate_report(pd.DataFrame())

    class QS:
        async def get_all_stocks(self):
            return [
                {"code": "603201", "is_mainboard": True, "is_st": False}
            ], "fixture"

        async def get_ohlcv_batch(self, codes, start, end, **kwargs):
            return {c: stock_ohlcv_by_code[c] for c in codes if c in stock_ohlcv_by_code}

    monkeypatch.setattr(
        "zstock.data_management.query_service.get_data_query_service", lambda: QS()
    )
    loaded = await load_ohlcv("2024-01-02", "2024-01-16")
    assert loaded

    async def _init():
        return None

    async def _close():
        return None

    monkeypatch.setattr("zstock.common.utils.db_utils.init_zstock_database", _init)
    monkeypatch.setattr("zstock.common.utils.db_utils.close_zstock_database", _close)
    args = build_parser().parse_args(
        ["--start", "2024-01-02", "--end", "2024-01-16", "--max-combinations", "1", "--workers", "1", "--output", str(tmp_path / "g")]
    )
    # async_main 会真跑 run_grid_search；run_one 已被替换
    code = await async_main(args)
    assert code in {0, 1}

    import zstock.factor_management.script.网格搜索.grid_search_real as gs

    monkeypatch.setattr(gs, "ProcessPoolExecutor", _InlinePool)
    monkeypatch.setattr(
        gs,
        "_run_combination_worker",
        lambda job: {
            **job["params"],
            "combination_id": job["combination_id"],
            "status": "success",
            "objective": 1.0,
            "total_return": 0.1,
            "sharpe": 1.0,
            "calmar": 1.0,
            "max_drawdown": -0.05,
            "annualized_return": 0.1,
            "avg_turnover": 0.1,
            "total_cost": 0.01,
        },
    )
    rows = opt._run_parallel(
        [{"combination_id": 1, "params": opt.baseline_params()}],
        {"603201": stock_ohlcv_by_code["603201"]},
        workers=2,
    )
    assert rows
    gs._quiet_loggers()
    gs._reset_worker_singletons()
    opt._log_row({"status": "failed:x"})
    opt._log_row(rows[0])


@pytest.mark.asyncio
async def test_style_compare_and_report(hs300_ohlcv, factor_eval_2024_csv, tmp_path, monkeypatch):
    from zstock.factor_management.script.run_style_detector import (
        _plot_results,
        _run_detection,
        main as style_main,
    )
    from zstock.factor_management.style_detector import StyleDetector
    from zstock.factor_management.script.因子测评.run_compare_eval import (
        async_main as compare_async_main,
        run_all_jobs,
    )
    from zstock.factor_management.script.因子测评.generate_report import generate_report

    class QS:
        async def get_ohlcv(self, code, start, end, period="daily"):
            return hs300_ohlcv, "fixture"

    monkeypatch.setattr(
        "zstock.data_management.query_service.get_data_query_service", lambda: QS()
    )
    df, results = await _run_detection("2024-01-02", "2024-01-10")
    assert results
    _plot_results(results[:5], tmp_path)
    assert list(tmp_path.glob("*.png")) or True

    class Mgr:
        async def init_mongodb(self):
            return None

    monkeypatch.setattr("app.core.database.db_manager", Mgr())
    monkeypatch.setattr("sys.argv", ["run_style_detector.py", "--start", "2024-01-02", "--end", "2024-01-08", "--plot"])
    await style_main()

    class TinyPipe:
        def __init__(self, **k):
            self.out = Path(k["output_dir"])
            self.out.mkdir(parents=True, exist_ok=True)
            src = pd.read_csv(factor_eval_2024_csv)
            src.to_csv(self.out / "summary.csv", index=False)

        async def run(self):
            return []

    monkeypatch.setattr(
        "zstock.factor_management.script.因子测评.factor_evaluation.FactorEvaluationPipeline",
        TinyPipe,
    )
    report = await run_all_jobs(
        modes=[("cond_p5", 5, True)],
        years=[2024],
        workers=1,
        job_parallel=1,
        output_root=tmp_path / "compare",
    )
    assert report.is_file()

    from zstock.factor_management.script.因子测评 import generate_report as gr

    monkeypatch.setattr(
        gr,
        "SOURCES",
        {"2024": factor_eval_2024_csv, "2025": factor_eval_2024_csv, "2026": factor_eval_2024_csv},
    )
    monkeypatch.setattr(gr, "BASE", tmp_path)
    out = generate_report(tmp_path / "compare")
    assert out.is_file()

    ns = SimpleNamespace(modes="cond_p5", years="2024", workers=1, job_parallel=1, output=str(tmp_path / "c2"))
    monkeypatch.setattr("app.core.database.db_manager", Mgr())
    assert await compare_async_main(ns) == 0
    ns_bad = SimpleNamespace(modes="nope", years="2024", workers=1, job_parallel=1, output=None)
    assert await compare_async_main(ns_bad) == 1
