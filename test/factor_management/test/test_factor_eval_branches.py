"""factor_evaluation：打分档位、极性、_evaluate 用真实截面拼面板。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from zstock.factor_management.script import factor_evaluation as fe_mod
from zstock.factor_management.script.factor_evaluation import FactorEvaluation


def _ic_frame(ic_mean: float, n: int = 30) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 0.02, size=n)
    ic = np.full(n, ic_mean) + noise
    ric = ic * 0.9
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.DataFrame({"IC": ic, "Rank_IC": ric}, index=idx)


def test_comprehensive_score_tiers():
    pipe = FactorEvaluation(
        start_date="2024-01-02", end_date="2024-01-16", period=5, plot=False, output_dir="."
    )
    strong = pipe._comprehensive_score(_ic_frame(0.12))
    assert strong["Total_Score"] >= 40
    mid = pipe._comprehensive_score(_ic_frame(0.06))
    assert mid["IC_Score"] in {8, 15, 25}
    weak = pipe._comprehensive_score(_ic_frame(0.005))
    assert weak["IC_Score"] == 0
    empty = pipe._comprehensive_score(pd.DataFrame(columns=["IC", "Rank_IC"]))
    assert empty["Total_Score"] == 0
    assert empty["Grade"].startswith("D")


def test_apply_polarity_and_grade_helpers():
    field = next(iter(fe_mod.INVERT_FIELDS_FOR_EVAL), "fcoop1_main_net_ratio")
    df = pd.DataFrame({"603201": [1.0, 2.0], "000060": [3.0, 4.0]})
    inv = FactorEvaluation.apply_polarity_invert(df, field, True)
    assert (inv.values == -df.values).all()
    assert FactorEvaluation.apply_polarity_invert(df, "not_invert", True) is df
    assert FactorEvaluation.apply_polarity_invert(df, field, False) is df
    assert FactorEvaluation._max_drawdown(pd.Series(dtype=float)) == 0.0
    assert FactorEvaluation._get_grade(90).startswith("A")
    assert FactorEvaluation._get_grade(70).startswith("B")
    assert FactorEvaluation._get_grade(50).startswith("C")
    assert FactorEvaluation._get_grade(10).startswith("D")


def test_evaluate_on_real_dragon_panel(factor_raw, stock_ohlcv_by_code, tmp_path):
    dragons = list(factor_raw.get("dragons") or [])
    field = "f32_amount"
    rows = []
    for d in dragons:
        code = d.get("code")
        if code not in stock_ohlcv_by_code:
            continue
        if d.get(field) is None:
            continue
        rows.append({"trade_date": d.get("trade_date") or "2024-01-02", "code": code, field: d[field]})
    if len({r["code"] for r in rows}) < 2:
        pytest.skip("因子夹具与 OHLCV 交集不足")
    wide = (
        pd.DataFrame(rows)
        .pivot_table(index="trade_date", columns="code", values=field, aggfunc="last")
        .sort_index()
    )
    # 用同一截面值复制若干交易日，凑出 IC 序列输入
    dates = sorted(
        {
            str(x)
            for code in wide.columns
            for x in stock_ohlcv_by_code[code]["trade_date"].astype(str)
            if "2024-01" <= str(x) <= "2024-03"
        }
    )[:20]
    if len(dates) < 5:
        pytest.skip("日期不足")
    factor = pd.DataFrame(
        {c: [float(wide[c].dropna().iloc[0])] * len(dates) for c in wide.columns},
        index=dates,
    )
    closes, opens = [], []
    for code in factor.columns:
        df = stock_ohlcv_by_code[code]
        s = df.set_index(df["trade_date"].astype(str))["close"].reindex(dates)
        o = df.set_index(df["trade_date"].astype(str))["open"].reindex(dates)
        closes.append(s.rename(code))
        opens.append(o.rename(code))
    price = {"close": pd.concat(closes, axis=1), "open": pd.concat(opens, axis=1)}
    pipe = FactorEvaluation(
        start_date=dates[0],
        end_date=dates[-1],
        period=1,
        plot=False,
        output_dir=str(tmp_path),
    )
    result = pipe._evaluate(factor, price)
    assert "score" in result
    assert "ic_series" in result
    summary = pipe._calc_ic_summary(result["ic_series"])
    assert isinstance(summary, pd.Series)


@pytest.mark.asyncio
async def test_eval_one_no_data_and_jobs(tmp_path):
    pipe = FactorEvaluation(
        start_date="2024-01-02",
        end_date="2024-01-03",
        period=5,
        plot=False,
        output_dir=str(tmp_path),
        layer="dragon",
        field="f32_amount",
    )

    class EmptyDB:
        async def query(self, *a, **k):
            return []

    pipe.db = EmptyDB()
    out = await pipe._eval_one("dragon", "f32_amount")
    assert out.get("error") == "no_data" or out.get("Grade") == "N/A"
    assert pipe._get_jobs() == [("dragon", "f32_amount")]
    all_jobs = FactorEvaluation(
        start_date="2024-01-02",
        end_date="2024-01-03",
        plot=False,
        output_dir=str(tmp_path),
    )._get_jobs()
    assert any(layer == "sector" for layer, _ in all_jobs)
