"""查询窗口切分。"""

from zstock.data_management.query_service import _iter_date_windows


def test_iter_date_windows_real_backtest_range():
    wins = _iter_date_windows("2024-01-01", "2024-03-15", 90)
    assert wins[0][0] == "2024-01-01"
    assert wins[-1][1] == "2024-03-15"
    assert all(a <= b for a, b in wins)
    assert _iter_date_windows("2024-03-15", "2024-01-01", 30) == []
    single = _iter_date_windows("20240102", "20240102", 0)
    assert single == [("2024-01-02", "2024-01-02")]
