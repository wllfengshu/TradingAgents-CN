"""book_data：序列构建与 prior-close 口径。"""

import pandas as pd

from zstock.strategy_management.book_data import build_index_bars, compose_book_inputs


def test_compose_book_excludes_trade_date_bar():
    rows = [
        {"trade_date": "2024-01-02", "close": 10.0, "high": 10.5, "low": 9.5, "volume": 1e6},
        {"trade_date": "2024-01-03", "close": 11.0, "high": 11.5, "low": 10.5, "volume": 1e6},
    ]
    ohlcv = {"399300": pd.DataFrame(rows)}
    out = compose_book_inputs("2024-01-03", ohlcv)
    assert len(out.index_bars) == 1
    assert out.index_closes == [10.0]
    assert out.book_grade in ("green", "yellow", "red")


def test_build_index_bars_shape():
    df = pd.DataFrame([{"close": 100.0, "high": 101.0, "low": 99.0, "volume": 0.0}])
    bars = build_index_bars(df)
    assert len(bars) == 1
    assert bars[0]["close"] == 100.0
