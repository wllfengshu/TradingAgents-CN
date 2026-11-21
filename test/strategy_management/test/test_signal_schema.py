"""signal_schema：候选胶水与信号 DataFrame 契约。"""

from __future__ import annotations

import pandas as pd

from zstock.strategy_management.strategy_section import schema as ss


def test_make_candidate_and_dedupe():
    a = ss.make_candidate("600000", "SW2A", 80.0)
    b = ss.make_candidate("600000", "SW2B", 90.0)
    c = ss.make_candidate("600001", "SW2A", 70.0)
    out = ss.dedupe_candidates_by_code([a, b, c, {"code": None}])
    assert len(out) == 2
    by = {x["code"]: x for x in out}
    assert by["600000"]["dragon_score"] == 90.0
    assert by["600000"]["sector_code"] == "SW2B"


def test_ranked_to_signals_df_buy_watch():
    ranked = [
        {"code": f"c{i}", "sector_code": "S", "final_score": 100 - i, "dragon_score": 50}
        for i in range(1, 8)
    ]
    df = ss.ranked_to_signals_df(
        ranked, "2024-01-02", "yellow", 0.7, top_k=3, regime="momentum"
    )
    assert len(df) == 7
    assert (df["signal_type"] == "buy").sum() == 3
    assert (df["signal_type"] == "watch").sum() == 4
    assert df.attrs["market_grade"] == "yellow"
    assert df.attrs["regime"] == "momentum"
    assert df.attrs["m1_allow_new_open"] is True

    blocked = ss.ranked_to_signals_df(
        ranked[:2], "2024-01-02", "yellow", 0.5, top_k=5, allow_new_open=False
    )
    assert set(blocked["signal_type"]) == {"watch"}


def test_empty_and_result_to_signals_df():
    empty = ss.empty_signals_df(
        "2024-01-02",
        "red",
        0.0,
        funnel={"n_m3": 1, "n_m4": 0, "stop": "m4_empty", "regime": "reversal"},
    )
    assert empty.empty
    assert empty.attrs["funnel_stop"] == "m4_empty"
    assert empty.attrs["regime"] == "reversal"

    early = ss.result_to_signals_df(
        {
            "early_exit": True,
            "trade_date": "2024-01-02",
            "market_risk_level": "red",
            "position_scale_factor": 0.0,
            "funnel": {"stop": "red", "n_m3": 0, "n_m4": 0},
        },
        exit_universe_n=20,
        top_k=5,
        allow_new_open=True,
    )
    assert early.empty and early.attrs["funnel_stop"] == "red"

    ranked = [
        ss.make_candidate("600000", "SW2", 80.0, final_score=88.0, force_composite_score=70.0),
        ss.make_candidate("600001", "SW2", 70.0, final_score=77.0, force_composite_score=60.0),
    ]
    ok = ss.result_to_signals_df(
        {
            "early_exit": False,
            "trade_date": "2024-01-02",
            "market_risk_level": "green",
            "position_scale_factor": 1.0,
            "regime": "momentum",
            "ranked_all": ranked,
            "funnel": {"n_m3": 10, "n_m4": 2, "stop": "ok"},
        },
        exit_universe_n=20,
        top_k=1,
        allow_new_open=True,
    )
    assert len(ok) == 2
    assert ok.attrs["funnel_n_buy"] == 1
    assert (ok["signal_type"] == "buy").sum() == 1

    no_ranked = ss.result_to_signals_df(
        {
            "early_exit": False,
            "trade_date": "2024-01-02",
            "market_risk_level": "yellow",
            "position_scale_factor": 0.7,
            "ranked_all": [],
            "funnel": {"stop": "ok"},
        },
        exit_universe_n=10,
        top_k=5,
        allow_new_open=True,
    )
    assert no_ranked.empty
