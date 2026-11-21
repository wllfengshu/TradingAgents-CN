"""akshare_utils 离线：用真实 399300/个股夹具行喂 mock ak，不打外网。"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from zstock.common.utils import akshare_utils as aku


@pytest.fixture(autouse=True)
def _reset_ak_cache(monkeypatch):
    monkeypatch.setattr(aku, "_ak", None)
    aku._spot_cache["data"] = None
    aku._spot_cache["ts"] = 0.0


def _hs300_as_ak_index(hs300_ohlcv: pd.DataFrame) -> pd.DataFrame:
    df = hs300_ohlcv.copy()
    df = df.rename(columns={"trade_date": "date"})
    if "amount" not in df.columns:
        df["amount"] = df.get("volume", 0)
    cols = [c for c in ("date", "open", "high", "low", "close", "volume", "amount") if c in df.columns]
    return df[cols]


def test_fetch_ohlcv_index_and_stock(hs300_ohlcv, stock_ohlcv_by_code, monkeypatch):
    idx = _hs300_as_ak_index(hs300_ohlcv)
    stock = stock_ohlcv_by_code["603201"].copy()
    stock_ak = stock.rename(columns={"trade_date": "date"})
    if "amount" not in stock_ak.columns:
        stock_ak["amount"] = stock_ak.get("volume", 0)
    stock_ak = stock_ak[
        [c for c in ("date", "open", "high", "low", "close", "volume", "amount") if c in stock_ak.columns]
    ]

    class FakeAk:
        def stock_zh_index_daily(self, symbol):
            assert "399300" in symbol
            return idx

        def stock_zh_a_daily(self, symbol, start_date, end_date, adjust):
            assert "603201" in symbol or "sh603201" == symbol
            return stock_ak

    monkeypatch.setattr(aku, "_get_ak", lambda: FakeAk())
    out_idx = aku.fetch_ohlcv("399300", "2024-01-02", "2024-01-16")
    assert not out_idx.empty
    assert (out_idx["code"] == "399300").all()
    assert "2024-01-02" in set(out_idx["trade_date"].astype(str))

    out_stk = aku.fetch_ohlcv("603201", "2024-01-02", "2024-01-16")
    assert not out_stk.empty
    assert (out_stk["code"] == "603201").all()

    # 000001 无前缀按股票路径（不是指数）
    class FakeAk2(FakeAk):
        def stock_zh_a_daily(self, symbol, start_date, end_date, adjust):
            return stock_ak

        def stock_zh_index_daily(self, symbol):
            raise AssertionError("不应走指数接口")

    monkeypatch.setattr(aku, "_get_ak", lambda: FakeAk2())
    aku.fetch_ohlcv("000001", "2024-01-02", "2024-01-03")


def test_fetch_ohlcv_batch_and_empty(hs300_ohlcv, monkeypatch):
    assert aku.fetch_ohlcv_batch([], "2024-01-02", "2024-01-03").empty

    idx = _hs300_as_ak_index(hs300_ohlcv)

    class FakeAk:
        def stock_zh_a_spot_em(self):
            return pd.DataFrame({"代码": ["603201"], "名称": ["常润股份"]})

        def stock_zh_index_daily(self, symbol):
            return idx

        def stock_zh_a_daily(self, symbol, start_date, end_date, adjust):
            return idx.rename(columns={"date": "date"})

    monkeypatch.setattr(aku, "_get_ak", lambda: FakeAk())
    batch = aku.fetch_ohlcv_batch(["399300", "603201"], "2024-01-02", "2024-01-10")
    assert not batch.empty
    assert set(batch["code"]) >= {"399300"}


def test_fetch_all_stocks_and_sector_helpers(monkeypatch, sector_list_sample=None):
    class FakeAk:
        def stock_info_a_code_name(self):
            return pd.DataFrame(
                [
                    {"code": "603201", "name": "常润股份"},
                    {"code": "000060", "name": "中金岭南"},
                    {"code": "", "name": "bad"},
                ]
            )

        def stock_board_concept_name_em(self):
            return pd.DataFrame(
                [{"板块名称": "贵金属", "板块代码": "BK1184"}]
            )

        def stock_board_industry_name_em(self):
            return pd.DataFrame(
                [{"板块名称": "汽车零部件", "板块代码": "BK0481"}]
            )

    monkeypatch.setattr(aku, "_get_ak", lambda: FakeAk())
    stocks = aku.fetch_all_stocks()
    codes = {s["code"] for s in stocks}
    assert "603201" in codes and "000060" in codes

    def _ok(func, *a, retries=2, delay=0, **k):
        return func(*a, **k)

    monkeypatch.setattr(aku, "_em_call", _ok)
    sectors = aku.fetch_sector_list("concept")
    assert sectors and sectors[0]["sector_type"] == "concept"
    assert aku._is_em_code("BK1184") is True
    assert aku._is_em_code("SW2出版") is False


def test_trade_status_single_and_batch(monkeypatch):
    spot = pd.DataFrame(
        [
            {
                "代码": "603201",
                "名称": "常润股份",
                "最新价": 12.0,
                "昨收": 10.0,
                "涨停价": 11.0,
                "跌停价": 9.0,
                "成交量": 1000,
                "成交额": 1e6,
            },
            {
                "代码": "000060",
                "名称": "*ST岭南",
                "最新价": 5.0,
                "昨收": 5.0,
                "涨停价": 0,
                "跌停价": 0,
                "成交量": 0,
                "成交额": 0,
            },
        ]
    )

    class FakeAk:
        def stock_individual_info_em(self, symbol):
            return pd.DataFrame(
                [
                    {"item": "股票简称", "value": "常润股份"},
                    {"item": "上市时间", "value": "2020-01-01"},
                ]
            )

        def stock_zh_a_spot_em(self):
            return spot

    monkeypatch.setattr(aku, "_get_ak", lambda: FakeAk())
    st = aku.fetch_trade_status("603201", "2024-01-02")
    assert st["code"] == "603201"
    # 与 batch 一致：最新价 >= 涨停价
    assert st["is_limit_up"] is True

    batch = aku.fetch_trade_status_batch(["603201", "000060"], "2024-01-02")
    assert batch["603201"]["is_limit_up"] is True
    assert batch["000060"]["is_st"] is True
    assert batch["000060"]["is_suspended"] is True
