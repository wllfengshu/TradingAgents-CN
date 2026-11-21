"""现场计算：用导出的板块成分 OHLCV + L2 资金流跑 M2/M3/M4 公开接口。"""

from pathlib import Path

import pandas as pd
import pytest

from zstock.factor_management.factors_dragon import DragonFactors
from zstock.factor_management.factors_force import ForceFactors
from zstock.factor_management.factors_sector import SectorFactors

LIVE = Path(__file__).resolve().parent / "fixtures" / "live_calc_20240102.json"


@pytest.fixture(scope="module")
def live_bundle():
    if not LIVE.is_file():
        pytest.skip("缺少 live_calc_20240102.json")
    import json

    with open(LIVE, encoding="utf-8") as f:
        payload = json.load(f)
    ohlcv = {c: pd.DataFrame(rows) for c, rows in (payload.get("ohlcv") or {}).items()}
    return payload.get("members") or {}, ohlcv, payload.get("flow") or {}


def _sector_ohlcv(members, ohlcv):
    parts = []
    for code in members:
        df = ohlcv.get(code)
        if df is None or df.empty:
            continue
        keep = [c for c in ("trade_date", "open", "high", "low", "close", "volume") if c in df.columns]
        parts.append(df[keep].copy())
    if not parts:
        return pd.DataFrame()
    allp = pd.concat(parts, ignore_index=True)
    g = allp.groupby("trade_date", as_index=False).agg(
        {"open": "mean", "high": "max", "low": "min", "close": "mean", "volume": "sum"}
    )
    return g.sort_values("trade_date")


def test_dragon_live_raw_on_auto_parts(live_bundle, strategy_params):
    members, ohlcv, _flow = live_bundle
    sector = next((k for k in members if "汽车" in k or "603201" in members[k]), None)
    if sector is None:
        sector = next(iter(members))
    stocks = [c for c in members[sector] if c in ohlcv]
    assert len(stocks) >= 3
    raw = DragonFactors.calculate_all_dragon_factors_in_sector_raw(
        stocks,
        ohlcv,
        assume_sorted=False,
        trade_date="2024-01-02",
        fund_provider=object(),
    )
    assert raw


def test_force_live_raw_and_score(live_bundle, strategy_params, signal_names_raw):
    _members, ohlcv, flow = live_bundle
    candidates = [
        {"code": d["code"], "sector_code": d["sector_code"], "dragon_score": 70.0}
        for d in signal_names_raw["dragons"]
        if d["code"] in ohlcv
    ]
    assert candidates
    raw = ForceFactors.apply_cooperative_force_raw(
        candidates,
        stock_flow_recent=flow,
        stock_ohlcv=ohlcv,
        stock_lhb_recent={},
        trade_date="2024-01-02",
    )
    assert raw
    assert "fcoop1_main_net_ratio" in raw[0]


def test_sector_live_raw(live_bundle, strategy_params):
    members, ohlcv, flow = live_bundle
    sectors = [{"sector_code": k, "sector_name": k} for k in members]
    market = {sc: _sector_ohlcv(codes, ohlcv) for sc, codes in members.items()}
    market = {k: v for k, v in market.items() if v is not None and not v.empty}
    if len(market) < 2:
        pytest.skip("板块聚合后不足 2 个")
    eligible = set(ohlcv)
    raw = SectorFactors.calculate_all_sector_factors_raw(
        sectors,
        members,
        ohlcv,
        stock_flow_recent=flow,
        sector_ohlcv=market,
        market_sector_ohlcv=market,
        trade_date="2024-01-02",
        eligible_codes=eligible,
    )
    assert "f23_limit_up_density" in raw or "f21_rps" in raw or raw


@pytest.mark.asyncio
async def test_score_signals_live_with_prebuilt(live_bundle, hs300_ohlcv, strategy_params):
    from test.factor_management.test.conftest import make_offline_pipeline

    members, ohlcv, flow = live_bundle
    cfg = dict(strategy_params)
    cfg["style_switching"] = False
    pipe = make_offline_pipeline(cfg)
    infos = {c: {"code": c, "is_mainboard": True, "is_st": False, "name": c} for c in ohlcv}
    prebuilt = {
        "trade_date": "2024-01-02",
        "all_stocks": list(ohlcv),
        "stock_infos": infos,
        "stock_ohlcv": ohlcv,
        "stock_flow_recent": flow,
        "sectors": [{"sector_code": k, "sector_name": k} for k in members],
        "sector_stocks": members,
        "index_ohlcv": {"399300": hs300_ohlcv},
        "index_name": "沪深300",
    }
    df = await pipe.score_signals_live("2024-01-02", prebuilt_data=prebuilt)
    assert df.attrs["market_grade"] in {"green", "yellow", "red"}
    assert isinstance(df, pd.DataFrame)

    class _LoadQS:
        async def get_all_stocks(self):
            docs = [{"code": c, "name": c, "is_st": False, "is_mainboard": True} for c in ohlcv]
            return docs, "fixture"

        async def get_sector_list(self, prefix="SW2"):
            secs = []
            for k, v in members.items():
                secs.append({"sector_code": k, "sector_name": k, "stocks": v})
            return secs, "fixture"

        async def get_ohlcv_batch(self, codes, start, end, period="daily"):
            if codes == ["399300"]:
                return {"399300": hs300_ohlcv}
            return {c: ohlcv[c] for c in codes if c in ohlcv}

        async def get_capital_flow_recent_days(self, codes, end_date, days=5, period=None):
            return {c: flow.get(c, []) for c in codes}

    pipe._query_service = _LoadQS()
    loaded = await pipe.load_real_data("2024-01-02", lookback_days=90, max_stocks=20)
    assert loaded["trade_date"] == "2024-01-02"
    assert loaded["stock_ohlcv"]

    from zstock.common.utils.common_utils import get_index_ohlcv_df

    empty_idx = get_index_ohlcv_df({})
    assert empty_idx is None
    idx = get_index_ohlcv_df({"399300": hs300_ohlcv})
    assert idx is not None
    assert len(idx) == len(hs300_ohlcv)
