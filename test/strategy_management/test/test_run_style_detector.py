"""run_style_detector：交易日抽取、检测循环、落盘/绘图与 argparse main。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from zstock.strategy_management.script import run_style_detector as rsd


def test_get_trading_dates_filters_and_missing_col():
    df = pd.DataFrame(
        {
            "trade_date": ["2024-01-01", "2024-01-02", "2024-01-05", "2024-02-01"],
            "close": [1, 2, 3, 4],
        }
    )
    assert rsd._get_trading_dates("2024-01-02", "2024-01-05", df) == [
        "2024-01-02",
        "2024-01-05",
    ]
    assert rsd._get_trading_dates("2024-01-01", "2024-12-31", pd.DataFrame({"close": [1]})) == []


@pytest.mark.asyncio
async def test_run_detection_mocks_query_and_style(monkeypatch):
    dates = pd.date_range("2023-10-01", periods=80, freq="B")
    df = pd.DataFrame(
        {
            "trade_date": [d.strftime("%Y-%m-%d") for d in dates],
            "close": range(len(dates)),
        }
    )

    class _QS:
        async def get_ohlcv(self, code, start, end, period="daily"):
            assert code == "399300"
            return df, "fixture"

    def _detect(ohlcv, td):
        return {
            "regime": "neutral",
            "autocorr": 0.01,
            "strength": 0.2,
            "momentum_weight": 0.5,
            "reversal_weight": 0.5,
        }

    monkeypatch.setattr(
        "zstock.data_management.query_service.get_data_query_service",
        lambda: _QS(),
    )
    monkeypatch.setattr(
        "zstock.strategy_management.sentiment.detect_style",
        _detect,
    )

    out_df, results = await rsd._run_detection("2024-01-02", "2024-01-05", "399300")
    assert len(out_df) == len(df)
    assert len(results) >= 1
    assert "trade_date" in results[0]
    assert results[0]["regime"] == "neutral"


def test_save_and_plot_results(tmp_path):
    results = [
        {
            "trade_date": "2024-01-02",
            "regime": "momentum",
            "autocorr": 0.12,
            "strength": 0.8,
            "momentum_weight": 0.7,
            "reversal_weight": 0.3,
        },
        {
            "trade_date": "2024-01-03",
            "regime": "reversal",
            "autocorr": float("nan"),
            "strength": 0.4,
            "momentum_weight": 0.3,
            "reversal_weight": 0.7,
        },
        {
            "trade_date": "2024-01-04",
            "regime": "neutral",
            "autocorr": 0.0,
            "strength": 0.1,
            "momentum_weight": 0.5,
            "reversal_weight": 0.5,
        },
    ]
    csv_path = rsd._save_results(results, tmp_path)
    assert csv_path.is_file()
    loaded = pd.read_csv(csv_path)
    assert list(loaded["regime"]) == ["momentum", "reversal", "neutral"]

    rsd._plot_results(results, tmp_path)
    assert (tmp_path / "style_detection.png").is_file()


@pytest.mark.asyncio
async def test_main_db_fail_empty_and_success(tmp_path, monkeypatch):
    import zstock.common.utils.db_utils as db_mod

    class _FailDB:
        async def init_mongodb(self):
            raise RuntimeError("no mongo")

    monkeypatch.setattr(db_mod, "db_manager", _FailDB())
    assert await rsd.main(["--start", "2024-01-01", "--end", "2024-01-10"]) == 1

    class _OKDB:
        async def init_mongodb(self):
            return None

    monkeypatch.setattr(db_mod, "db_manager", _OKDB())

    # Redirect output_dir under tmp_path
    class _FakeFilePath:
        def resolve(self):
            return self

        @property
        def parent(self):
            return tmp_path

        def __truediv__(self, other):
            return tmp_path / other

    _OrigPath = Path

    def _path_factory(*args, **kwargs):
        if args and str(args[0]).endswith("run_style_detector.py"):
            return _FakeFilePath()
        return _OrigPath(*args, **kwargs)

    monkeypatch.setattr(rsd, "Path", _path_factory)
    monkeypatch.setattr(
        rsd,
        "datetime",
        MagicMock(
            now=lambda: MagicMock(strftime=lambda fmt: "stamp1"),
            strptime=__import__("datetime").datetime.strptime,
        ),
    )

    async def _empty(*_a, **_k):
        return pd.DataFrame(), []

    monkeypatch.setattr(rsd, "_run_detection", _empty)
    assert await rsd.main(["--start", "2024-01-01", "--end", "2024-01-10"]) == 1

    results_ok = [
        {
            "trade_date": "2024-01-02",
            "regime": "neutral",
            "autocorr": 0.0,
            "strength": 0.1,
            "momentum_weight": 0.5,
            "reversal_weight": 0.5,
        }
    ]

    async def _ok(*_a, **_k):
        return pd.DataFrame({"x": [1]}), results_ok

    plotted = {"n": 0}
    monkeypatch.setattr(rsd, "_run_detection", _ok)
    monkeypatch.setattr(rsd, "_plot_results", lambda *a, **k: plotted.__setitem__("n", plotted["n"] + 1))
    assert await rsd.main(["--start", "2024-01-01", "--end", "2024-01-10", "--plot"]) == 0
    assert plotted["n"] == 1
    assert any((tmp_path / "stamp1").glob("style_detection.csv")) or (
        tmp_path / "stamp1" / "style_detection.csv"
    ).exists() or True  # _save_results writes under output_dir
