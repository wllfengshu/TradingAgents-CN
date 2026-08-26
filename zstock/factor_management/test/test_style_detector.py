"""风格检测：用导出的 399300 收盘价，不编造收益率。"""

import numpy as np
import pytest

from zstock.factor_management.style_detector import StyleDetector


def test_detect_on_hs300_around_jan2024(hs300_ohlcv):
    det = StyleDetector()
    out = det.detect(hs300_ohlcv, trade_date="2024-01-02")
    assert out["regime"] in {"momentum", "reversal", "neutral"}
    assert 0.0 <= out["strength"] <= 1.0
    assert abs(out["momentum_weight"] + out["reversal_weight"] - 1.0) < 1e-9
    autocorr = det.compute_rank_autocorr(
        hs300_ohlcv[hs300_ohlcv["trade_date"] <= "2024-01-02"]["close"].astype(float)
    )
    assert np.isfinite(autocorr)
    classified = det.classify_regime(autocorr)
    assert classified["regime"] == out["regime"]


def test_cli_trading_dates_and_save(hs300_ohlcv, tmp_path):
    from zstock.factor_management.script.run_style_detector import (
        _get_trading_dates,
        _save_results,
    )

    dates = _get_trading_dates("2024-01-02", "2024-01-31", hs300_ohlcv)
    assert dates
    assert all("2024-01" in d for d in dates)
    det = StyleDetector()
    results = []
    for td in dates[:8]:
        r = det.detect(hs300_ohlcv, trade_date=td)
        r["trade_date"] = td
        results.append(r)
    csv_path = _save_results(results, tmp_path)
    assert csv_path.is_file()


def test_empty_and_nan_are_neutral(hs300_ohlcv):
    det = StyleDetector()
    empty = det.detect(None)
    assert empty["regime"] == "neutral"
    nan = det.classify_regime(float("nan"))
    assert nan["regime"] == "neutral"
    mom = det.classify_regime(0.4)
    assert mom["regime"] == "momentum"
    rev = det.classify_regime(-0.4)
    assert rev["regime"] == "reversal"
    no_close = det.detect(hs300_ohlcv.drop(columns=["close"]) if "close" in hs300_ohlcv.columns else hs300_ohlcv)
    assert no_close["regime"] == "neutral"


@pytest.mark.asyncio
async def test_detect_from_mongo_uses_injected_hs300(hs300_ohlcv, monkeypatch):
    class _QS:
        async def get_ohlcv(self, code, start, end, period="daily"):
            assert code == "399300"
            return hs300_ohlcv, "fixture"

    monkeypatch.setattr(
        "zstock.factor_management.style_detector.get_data_query_service",
        lambda: _QS(),
    )
    out = await StyleDetector().detect_from_mongo("2024-01-02")
    assert out["regime"] in {"momentum", "reversal", "neutral"}


@pytest.mark.asyncio
async def test_detect_from_mongo_failure_is_neutral(monkeypatch):
    class _QS:
        async def get_ohlcv(self, *args, **kwargs):
            raise RuntimeError("down")

    monkeypatch.setattr(
        "zstock.factor_management.style_detector.get_data_query_service",
        lambda: _QS(),
    )
    out = await StyleDetector().detect_from_mongo("2024-01-02")
    assert out["regime"] == "neutral"
