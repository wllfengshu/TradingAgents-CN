"""同步脚本的纯函数与参数解析，不跑全市场 xtquant。"""

import sys

import pandas as pd

from zstock.data_management.script.sync_ohlcv import (
    INDEX_CODES as SYNC_INDEXES,
    _enrich_turnover_rate,
    _iter_day_windows,
    _iter_year_windows,
    _parse_args,
    _parse_freqs,
)


def test_index_codes_include_hs300():
    sync_codes = {c for c, _ in SYNC_INDEXES}
    assert "399300" in sync_codes
    assert "399001" in sync_codes


def test_parse_args_defaults(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["sync_ohlcv.py"])
    args = _parse_args()
    assert args.start == "2025-01-01"
    assert args.end


def test_parse_freqs_aliases_and_invalid():
    assert _parse_freqs("1d,d,day") == ["1d"]
    assert _parse_freqs("5m,1m") == ["5m", "1m"]
    try:
        _parse_freqs("bad")
        assert False, "应拒绝非法 freq"
    except SystemExit:
        pass


def test_iter_windows_edges():
    years = list(_iter_year_windows("2024-01-02", "2024-03-15", 1))
    assert years[0][0] == "2024-01-02"
    assert years[-1][1] == "2024-03-15"
    days = list(_iter_day_windows("2024-01-02", "2024-01-10", 5))
    assert len(days) >= 2
    assert list(_iter_year_windows("2024-01-02", "2024-01-02", 0)) == [
        ("2024-01-02", "2024-01-02")
    ]
    assert list(_iter_day_windows("2024-01-02", "2024-01-02", 0)) == [
        ("2024-01-02", "2024-01-02")
    ]


def test_enrich_turnover_empty():
    assert _enrich_turnover_rate(pd.DataFrame(), ["603201"]).empty


def test_enrich_turnover_with_float_volume_column(stock_ohlcv_docs, monkeypatch):
    df = pd.DataFrame([d for d in stock_ohlcv_docs if d["code"] == "603201"][:5])
    assert not df.empty
    out = _enrich_turnover_rate(df, ["603201"])
    assert "turnover_rate" in out.columns
    assert float(out["turnover_rate"].fillna(0).max()) == 0.0
