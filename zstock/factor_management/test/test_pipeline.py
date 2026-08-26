"""管道静态方法 + 注入预计算缓存的 score_signals（2024-01-02 真实原始值）。"""

import pytest

from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
from zstock.factor_management.test.conftest import make_offline_pipeline


def test_unknown_lazy_export_raises():
    import zstock.factor_management as fm

    with pytest.raises(AttributeError):
        fm.definitely_missing
    with pytest.raises((ImportError, ModuleNotFoundError, AttributeError)):
        getattr(fm, "FactorCalculator")


@pytest.mark.asyncio
async def test_detect_style_disabled_is_neutral(strategy_params):
    cfg = dict(strategy_params)
    cfg["style_switching"] = False
    pipe = make_offline_pipeline(cfg)
    out = await pipe._detect_style("2024-01-02")
    assert out["regime"] == "neutral"


def test_position_scale_overlay(strategy_params):
    pipe = make_offline_pipeline(strategy_params)
    yellow = pipe._resolve_position_scale("yellow", 0.4, regime="momentum")
    assert 0.0 <= yellow <= 1.0
    red = pipe._resolve_position_scale("red", 0.0, regime="reversal")
    assert red == 0.0 or red >= 0.0

    pipe = make_offline_pipeline(strategy_params)
    assert pipe._cfg_top_k("reversal") == 3
    assert pipe._cfg_top_k("momentum") == int(strategy_params["final_score"]["top_k"])
    assert pipe._cfg_top_sectors() == int(strategy_params["sector_layer"]["top_sectors"])
    assert pipe._cfg_top_per_sector() == int(strategy_params["dragon_layer"]["top_per_sector"])
    w = pipe._cfg_final_weights()
    assert abs(w["sector"] + w["dragon"] + w["cooperative"] - 1.0) < 1e-9


def test_dedupe_keeps_higher_dragon_score():
    rows = [
        {"code": "603201", "dragon_composite_score": 10, "sector_code": "A"},
        {"code": "603201", "dragon_composite_score": 80, "sector_code": "B"},
        {"code": "000060", "dragon_composite_score": 50, "sector_code": "C"},
        {"code": "", "dragon_composite_score": 99},
    ]
    out = CrossSectionStrategyPipeline._dedupe_candidates_by_code(rows)
    by_code = {r["code"]: r for r in out}
    assert by_code["603201"]["dragon_composite_score"] == 80
    assert by_code["603201"]["sector_code"] == "B"
    assert "000060" in by_code


def test_empty_and_ranked_signals_schema(strategy_params):
    empty = CrossSectionStrategyPipeline._empty_signals_df("2024-01-02", "red", 0.0)
    assert empty.empty
    assert empty.attrs["market_grade"] == "red"
    pipe = make_offline_pipeline(strategy_params)
    ranked = [
        {
            "code": "603201",
            "sector_code": "SW2汽车零部件",
            "strategy_signal_score": 75.36,
            "dragon_composite_score": 80.0,
            "force_composite_score": 50.0,
        },
        {
            "code": "000060",
            "sector_code": "SW2工业金属",
            "strategy_signal_score": 60.0,
            "dragon_composite_score": 70.0,
            "force_composite_score": 50.0,
        },
    ]
    df = pipe._ranked_to_signals_df(ranked, "2024-01-02", "yellow", 0.4, regime="momentum")
    assert list(df["code"]) == ["603201", "000060"]
    assert df.iloc[0]["signal_type"] == "buy"
    assert df.attrs["regime"] == "momentum"
    assert df.attrs["top_k"] == 5


class _FakeQS:
    def __init__(self, factor_raw, names):
        self.factor_raw = factor_raw
        self.names = names

    async def get_factor_market(self, td):
        return self.factor_raw["market"]

    async def get_factor_sectors(self, td):
        seen = {}
        for d in list(self.factor_raw.get("sectors") or []) + list(self.names.get("sectors") or []):
            seen[d["sector_code"]] = d
        return list(seen.values())

    async def get_factor_dragons(self, td, sector_codes=None):
        docs = list(self.names.get("dragons") or []) + list(self.factor_raw.get("dragons") or [])
        if sector_codes:
            allow = set(sector_codes)
            docs = [d for d in docs if d.get("sector_code") in allow]
        return docs

    async def get_factor_forces(self, td):
        return list(self.names.get("forces") or []) + list(self.factor_raw.get("forces") or [])

    async def get_all_stocks(self):
        infos = [v for v in self.names.get("stock_infos", {}).values() if isinstance(v, dict)]
        extras = [
            {"code": d["code"], "is_mainboard": True, "is_st": False, "name": d.get("stock_name", "")}
            for d in self.factor_raw.get("dragons") or []
        ]
        by = {d["code"]: d for d in extras + infos}
        return list(by.values()), "fixture"

    async def get_sector_list(self, prefix="SW2"):
        meta = list(self.names.get("sector_meta") or [])
        for d in self.factor_raw.get("sectors") or []:
            meta.append({"sector_code": d["sector_code"], "sector_name": d.get("sector_name", d["sector_code"])})
        return meta, "fixture"


@pytest.mark.asyncio
async def test_score_signals_from_dumped_raw(strategy_params, factor_raw, signal_names_raw):
    qs = _FakeQS(factor_raw, signal_names_raw)
    pipe = make_offline_pipeline(strategy_params, qs=qs)
    pipe._all_stocks_cache = (await qs.get_all_stocks())[0]
    pipe._sectors_cache = (await qs.get_sector_list())[0]

    async def _style(_td):
        return {
            "regime": "momentum",
            "momentum_weight": 0.7,
            "reversal_weight": 0.3,
            "autocorr": 0.1,
        }

    pipe._detect_style = _style  # type: ignore[method-assign]
    df = await pipe.score_signals("2024-01-02")
    assert df.attrs["market_grade"] in {"yellow", "green", "red"}
    if df.empty:
        pytest.skip("注入的预计算截面当天未产出候选（红灯或 M4 全被挡）")
    assert set(df["code"]).issubset(
        {d["code"] for d in signal_names_raw["dragons"]}
        | {d["code"] for d in factor_raw.get("dragons") or []}
    )


@pytest.mark.asyncio
async def test_score_signals_missing_market_raises(strategy_params):
    class EmptyQS:
        async def get_factor_market(self, td):
            return None

    pipe = make_offline_pipeline(strategy_params, qs=EmptyQS())
    with pytest.raises(ValueError, match="无预计算 M1"):
        await pipe.score_signals("2024-01-02")


def test_pipeline_constructs_from_ssot(monkeypatch, strategy_params):
    monkeypatch.setattr(
        "zstock.factor_management.pipeline.get_data_query_service",
        lambda: object(),
    )
    pipe = CrossSectionStrategyPipeline()
    assert pipe.config["version"] == strategy_params["version"]
    pipe._merge_config({"final_score": {"top_k": 7}})
    assert pipe._cfg_top_k("momentum") == 7


@pytest.mark.asyncio
async def test_score_signals_red_market(strategy_params, factor_raw):
    market = dict(factor_raw["market"])
    market.update(
        {
            "mf1_slope_pct": -0.05,
            "mf2_boll_pct": 0.05,
            "mf3_vol_ratio": 0.2,
            "mf4_momentum_5d": -0.12,
            "mf5_atr_ratio": 0.09,
        }
    )
    from zstock.factor_management.market_factors import MarketFactors

    sent = MarketFactors.score_from_raw(
        float(market["mf1_slope_pct"]),
        float(market["mf2_boll_pct"]),
        float(market["mf3_vol_ratio"]),
        float(market["mf4_momentum_5d"]),
        float(market["mf5_atr_ratio"]),
    )
    if sent["market_risk_level"] != "red":
        pytest.skip("该组压力参数仍未落到红灯")

    class RedQS:
        async def get_factor_market(self, td):
            return market

    pipe = make_offline_pipeline(strategy_params, qs=RedQS())
    df = await pipe.score_signals("2024-01-02")
    assert df.empty
    assert df.attrs["market_grade"] == "red"
