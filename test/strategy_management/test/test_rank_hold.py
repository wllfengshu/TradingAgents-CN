"""每天按排名持有股票：买前 n 只，缓冲带内不卖。"""

import pandas as pd

from zstock.strategy_management.strategy_section.rank_hold import rank_stock_weights


def _signals():
    return pd.DataFrame(
        {
            "code": ["600001", "600002", "600003", "600004", "600005", "600006"],
            "rank": [1, 2, 3, 4, 5, 8],
        }
    )


def test_buys_top_n_at_single_name_cap():
    weights = rank_stock_weights(_signals(), [], n=3, buffer=2, cap=0.2)
    assert list(weights) == ["600001", "600002", "600003"]
    assert abs(sum(weights.values()) - 0.6) < 1e-8


def test_keeps_held_name_inside_buffer_and_does_not_churn():
    weights = rank_stock_weights(
        _signals(),
        ["600006", "600002", "600003"],
        n=3,
        buffer=5,
        cap=0.2,
    )
    assert set(weights) == {"600006", "600002", "600003"}


def test_missing_from_short_list_is_kept():
    weights = rank_stock_weights(
        _signals(),
        ["600009", "600002"],
        n=3,
        buffer=2,
        cap=0.2,
    )
    assert "600009" in weights
    assert "600002" in weights
    assert "600001" in weights
    assert len(weights) == 3


def test_sells_when_rank_leaves_buffer():
    weights = rank_stock_weights(
        _signals(),
        ["600006", "600002"],
        n=3,
        buffer=2,
        cap=0.2,
    )
    assert "600006" not in weights
    assert "600002" in weights
    assert "600001" in weights
    assert len(weights) == 3
