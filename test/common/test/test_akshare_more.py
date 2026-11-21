"""akshare 更多离线分支：板块成分、空快照、接口失败。"""

from __future__ import annotations

import pandas as pd
import pytest

from zstock.common.utils import akshare_utils as aku


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(aku, "_ak", None)
    aku._spot_cache["data"] = None
    aku._spot_cache["ts"] = 0.0


def test_fetch_ohlcv_failures(monkeypatch):
    class Boom:
        def stock_zh_index_daily(self, symbol):
            raise RuntimeError("index down")

        def stock_zh_a_daily(self, **kwargs):
            raise RuntimeError("stock down")

    monkeypatch.setattr(aku, "_get_ak", lambda: Boom())
    assert aku.fetch_ohlcv("399300", "2024-01-02", "2024-01-03").empty
    assert aku.fetch_ohlcv("603201", "2024-01-02", "2024-01-03").empty


def test_fetch_all_stocks_empty(monkeypatch):
    class Empty:
        def stock_info_a_code_name(self):
            return pd.DataFrame()

    monkeypatch.setattr(aku, "_get_ak", lambda: Empty())
    assert aku.fetch_all_stocks() == []


def test_sector_stocks_em_and_ths(monkeypatch):
    class FakeAk:
        def stock_board_concept_cons_em(self, symbol):
            return pd.DataFrame({"代码": ["603201", "000060"]})

        def stock_board_concept_info_ths(self, symbol):
            return pd.DataFrame({"代码": ["601107"]})

        def stock_board_industry_cons_em(self, symbol):
            return pd.DataFrame({"代码": ["603201"]})

        def stock_board_industry_info_ths(self, symbol):
            return pd.DataFrame()

    monkeypatch.setattr(aku, "_get_ak", lambda: FakeAk())

    def _ok(func, *a, retries=2, delay=0, **k):
        return func(*a, **k)

    monkeypatch.setattr(aku, "_em_call", _ok)
    cons = aku.fetch_sector_stocks(
        "BK1184", sector_name="贵金属", sector_type="concept"
    )
    assert "603201" in cons and "000060" in cons
    ths = aku.fetch_sector_stocks("贵金属", sector_type="concept")
    assert "601107" in ths


def test_spot_cache_ttl(monkeypatch):
    calls = {"n": 0}

    class FakeAk:
        def stock_zh_a_spot_em(self):
            calls["n"] += 1
            return pd.DataFrame({"代码": ["603201"], "名称": ["常润股份"]})

    ak = FakeAk()
    monkeypatch.setattr(aku, "_get_ak", lambda: ak)
    monkeypatch.setattr(aku._time, "time", lambda: 1000.0)
    s1 = aku._get_spot_snapshot(ak)
    s2 = aku._get_spot_snapshot(ak)
    assert calls["n"] == 1
    assert not s1.empty and not s2.empty


def test_em_call_retries(monkeypatch):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("busy")
        return "ok"

    monkeypatch.setattr(aku._time, "sleep", lambda *_: None)
    assert aku._em_call(flaky, retries=3, delay=0) == "ok"
    assert calls["n"] == 2


def test_get_ak_missing(monkeypatch):
    monkeypatch.setattr(aku, "_ak", None)

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "akshare":
            raise ImportError("no ak")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match="akshare 未安装"):
        aku._get_ak()
