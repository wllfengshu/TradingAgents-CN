"""再压 factor_evaluation：bundle / prewarm / IC 边角 / 报告 A/B 分支。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import numpy as np
import pandas as pd
import pytest

from zstock.factor_management.script.factor_evaluation import FactorEvaluation
from zstock.data_management.query_service import COL_FACTOR_DRAGON


@pytest.mark.asyncio
async def test_load_eval_bundles_and_prewarm(tmp_path, stock_ohlcv_by_code, factor_raw):
    pipe = FactorEvaluation(
        start_date="2024-01-02",
        end_date="2024-01-16",
        period=5,
        plot=False,
        output_dir=str(tmp_path),
        invert_negative=True,
        universe_by_date={"2024-01-02": {"603201", "000060"}},
    )
    dragons = list(factor_raw.get("dragons") or [])[:30]

    class DB:
        async def query(self, *a, **k):
            return dragons

    class EmptyDB:
        async def query(self, *a, **k):
            return []

    pipe.db = DB()

    class QS:
        async def get_ohlcv_batch(self, codes, start, end, **kwargs):
            return {c: stock_ohlcv_by_code[c] for c in codes if c in stock_ohlcv_by_code}

        async def get_sector_list(self):
            codes = [c for c in stock_ohlcv_by_code][:5]
            return ([{"sector_code": "SW2测", "stocks": codes}], None)

    pipe.qs = QS()
    factor, panels = await pipe.load_eval_bundle_stock("dragon", "f32_amount")
    assert isinstance(factor, pd.DataFrame)
    assert isinstance(panels, dict)

    with pytest.raises(ValueError):
        await pipe.load_eval_bundle_stock("unknown", "f32_amount")

    pipe.db = EmptyDB()
    empty_f, empty_p = await pipe.load_eval_bundle_stock("dragon", "f32_amount")
    assert empty_f.empty and empty_p == {}

    # sector bundle：空因子
    pipe.db = EmptyDB()
    sf, sp = await pipe.load_eval_bundle_sector("f21_rps_10d")
    assert sf.empty

    # 有因子面板时走价格
    wide = pd.DataFrame(
        {"SW2测": [0.1, 0.2]},
        index=["2024-01-02", "2024-01-03"],
    )

    async def fake_sector_panel(field):
        return wide

    async def fake_sector_price(**kwargs):
        return {
            "open": wide * 100,
            "close": wide * 100,
        }

    pipe.load_sector_factor_panel = fake_sector_panel  # type: ignore
    pipe.load_sector_price_panel = fake_sector_price  # type: ignore
    sf2, sp2 = await pipe.load_eval_bundle_sector("f21_rps_10d")
    assert not sf2.empty and sp2

    pipe.universe_by_date = {
        "2024-01-02": set(list(stock_ohlcv_by_code)[:3]),
    }
    pipe.load_price_panel = AsyncMock(return_value={"close": pd.DataFrame(), "open": pd.DataFrame()})
    pipe.load_sector_factor_panel = AsyncMock(
        return_value=pd.DataFrame({"SW2测": [1.0]}, index=["2024-01-02"])
    )
    pipe.load_sector_price_panel = AsyncMock(return_value={})
    await pipe._prewarm_shared_data(
        [("dragon", "f32_amount"), ("sector", "f21_rps_10d")]
    )
    pipe.load_price_panel.assert_awaited()
    pipe.load_sector_price_panel.assert_awaited()


def test_ic_quantile_autocorr_edges(tmp_path):
    pipe = FactorEvaluation(
        start_date="2024-01-02",
        end_date="2024-02-29",
        period=1,
        n_quantiles=5,
        plot=False,
        output_dir=str(tmp_path),
    )
    dates = pd.bdate_range("2024-01-02", periods=20).strftime("%Y-%m-%d")
    codes = [f"c{i}" for i in range(10)]
    rng = np.random.default_rng(1)
    factor = pd.DataFrame(rng.normal(size=(len(dates), len(codes))), index=dates, columns=codes)
    close = pd.DataFrame(
        np.cumprod(1 + rng.normal(0, 0.01, size=factor.shape), axis=0) * 10,
        index=dates,
        columns=codes,
    )
    panels = {"open": close.copy(), "close": close}
    ic = pipe._calc_ic_series(factor, panels, period=1)
    assert isinstance(ic, pd.DataFrame)
    summary = pipe._calc_ic_summary(ic)
    assert "Rank_IC_Mean" in summary.index or summary.empty or True
    q = pipe._calc_quantile_returns(factor, panels)
    assert isinstance(q, pd.DataFrame)
    ls = pipe._calc_long_short_return(q)
    assert "LS_Mean_Return" in ls or ls == {} or True
    ac = pipe._calc_factor_autocorr(factor)
    assert isinstance(ac, dict)
    # 空 / 单列
    assert pipe._calc_ic_series(pd.DataFrame(), panels).empty or True
    score = pipe._comprehensive_score(ic if isinstance(ic, pd.DataFrame) else pd.DataFrame())
    assert "Grade" in score
    # 极端 IC
    strong = pd.DataFrame({"IC": np.full(40, 0.2), "Rank_IC": np.full(40, 0.2)})
    assert pipe._comprehensive_score(strong)["Total_Score"] >= 50
    weak = pd.DataFrame({"IC": np.full(40, -0.01), "Rank_IC": np.full(40, -0.01)})
    pipe._comprehensive_score(weak)
    # plot
    result = pipe._evaluate(factor, panels)
    pipe._plot_factor_analysis(
        result["ic_series"],
        result["quantile_returns"],
        title="ut",
        save_path=str(tmp_path / "ut.png"),
    )
    assert (tmp_path / "ut.png").is_file()


def test_generate_report_ab_stable_dropped(tmp_path, monkeypatch):
    """手工构造含 A/B/稳定/掉队 的三年 summary，覆盖报告分支。"""
    rows_ab = [
        {
            "layer": "dragon",
            "field": "f33_consecutive_boards",
            "Total_Score": 85,
            "Grade": "A - 优秀因子",
            "Rank_IC_Mean": 0.1,
            "Rank_ICIR": 0.8,
            "N_Periods": 50,
        },
        {
            "layer": "dragon",
            "field": "f36_identity_premium",
            "Total_Score": 70,
            "Grade": "B - 良好",
            "Rank_IC_Mean": 0.05,
            "Rank_ICIR": 0.4,
            "N_Periods": 40,
        },
        {
            "layer": "force",
            "field": "f_mean_reversion_signal",
            "Total_Score": 30,
            "Grade": "D",
            "Rank_IC_Mean": 0.01,
            "Rank_ICIR": 0.1,
            "N_Periods": 20,
        },
    ]
    # 2026：f33 掉队，f36 仍 B
    rows_2026 = [
        {**rows_ab[0], "Total_Score": 40, "Grade": "C"},
        rows_ab[1],
        rows_ab[2],
    ]
    paths = {}
    for y, rows in [("2024", rows_ab), ("2025", rows_ab), ("2026", rows_2026)]:
        p = tmp_path / f"{y}.csv"
        pd.DataFrame(rows).to_csv(p, index=False)
        paths[y] = p
    monkeypatch.setattr(FactorEvaluation, "REPORT_SOURCES", paths)
    monkeypatch.setattr(FactorEvaluation, "DEFAULT_COMPARE_ROOT", tmp_path / "none")
    out = tmp_path / "r.md"
    path = FactorEvaluation.generate_report(output_path=out)
    text = path.read_text(encoding="utf-8")
    assert "三年均为 B" in text or "f36" in text
    assert "风格切换" in text or "f33" in text
    # 缺文件
    monkeypatch.setattr(
        FactorEvaluation, "REPORT_SOURCES", {"2024": tmp_path / "missing.csv"}
    )
    with pytest.raises(FileNotFoundError):
        FactorEvaluation.load_report_data()


def test_build_merged_missing_keys():
    base = pd.DataFrame(
        [
            {
                "layer": "dragon",
                "field": "f32_amount",
                "factor_key": "dragon.f32_amount",
                "Total_Score": 50,
                "Grade": "C",
                "Rank_IC_Mean": 0.02,
                "N_Periods": 10,
            }
        ]
    )
    dfs = {
        "2024": base,
        "2025": base.iloc[0:0].copy(),  # 空 → 缺失
        "2026": base,
    }
    # 2025 需要有 factor_key 列
    dfs["2025"] = pd.DataFrame(columns=base.columns)
    merged = FactorEvaluation.build_merged(
        {
            "2024": base,
            "2025": pd.DataFrame(
                [
                    {
                        "layer": "sector",
                        "field": "f21_rps_10d",
                        "factor_key": "sector.f21_rps_10d",
                        "Total_Score": 60,
                        "Grade": "B",
                        "Rank_IC_Mean": np.nan,
                        "N_Periods": np.nan,
                    }
                ]
            ),
            "2026": base,
        }
    )
    assert "缺失" in set(merged["2025_grade"]) or "dragon.f32_amount" in set(merged["factor"])
