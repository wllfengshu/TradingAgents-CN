"""基本面 PIT：日期解析 + 用信号夹具中的 PB/股东变化做 asof 查询。"""

from types import SimpleNamespace

import pandas as pd
import pytest

from zstock.factor_management.fundamental_factors import (
    FundamentalDataProvider,
    _date_str_to_int,
    _date_to_int,
    _from_xt_code,
    _parse_date_int,
)


def test_date_helpers():
    assert _date_to_int("2024-01-02") == 20240102
    assert _date_str_to_int("2024-01-02") == 20240102
    assert _date_str_to_int("20240102") == 20240102
    assert _date_str_to_int(20240102) == 20240102
    assert _date_str_to_int(None) is None
    assert _date_str_to_int("2024") is None
    assert _date_str_to_int("abcdefgh") is None
    assert _from_xt_code("603201.SH") == "603201"
    assert _parse_date_int("2024-01-02") == 20240102
    assert _parse_date_int("20240102000000000") == 20240102
    assert _parse_date_int(None) is None
    assert _parse_date_int("x") is None


def test_empty_cache_returns_none(stock_ohlcv_by_code):
    close = float(stock_ohlcv_by_code["603201"].iloc[0]["close"])
    provider = FundamentalDataProvider()
    assert provider.get_bps("603201", "2024-01-02") is None
    assert provider.get_holder_change("603201", "2024-01-02") is None
    assert provider.compute_pb("603201", close, "2024-01-02") is None
    assert provider.is_loaded is False
    assert provider.codes_with_pb() == 0
    assert provider.codes_with_holder() == 0


def test_pit_asof_from_signal_pb(stock_ohlcv_by_code, signal_names_raw):
    dragon = next(d for d in signal_names_raw["dragons"] if d["code"] == "603201")
    df = stock_ohlcv_by_code["603201"]
    close = float(df[df["trade_date"] == "2024-01-02"].iloc[0]["close"])
    pb = float(dragon["f39_pb"])
    bps = close / pb
    hc = float(dragon["f40_holder_change"])
    provider = FundamentalDataProvider()
    provider._bps_cache["603201"] = [(20231031, bps)]
    provider._holder_cache["603201"] = [(20231031, hc)]
    provider._loaded = True
    assert provider.get_bps("603201", "2024-01-02") == pytest.approx(bps)
    assert provider.get_bps("603201", "2023-01-01") is None
    assert provider.get_holder_change("603201", "2024-01-02") == pytest.approx(hc)
    assert provider.get_holder_change("603201", "2023-01-01") is None
    assert provider.compute_pb("603201", close, "2024-01-02") == pytest.approx(pb)
    assert provider.compute_pb("603201", 0.0, "2024-01-02") is None
    assert provider.codes_with_pb() == 1
    assert provider.codes_with_holder() == 1
    assert provider.is_loaded is True


def test_load_from_mongodb_with_fake_client(monkeypatch, stock_ohlcv_by_code, signal_names_raw):
    dragon = next(d for d in signal_names_raw["dragons"] if d["code"] == "603201")
    df = stock_ohlcv_by_code["603201"]
    close = float(df[df["trade_date"] == "2024-01-02"].iloc[0]["close"])
    bps = close / float(dragon["f39_pb"])

    class Cursor(list):
        def sort(self, *args, **kwargs):
            return self

    class Col:
        def __init__(self, docs):
            self.docs = docs

        def count_documents(self, q):
            return len(self.docs)

        def find(self, *args, **kwargs):
            return Cursor(self.docs)

    ps = [
        {"code": "603201", "ann_date": "2023-10-31", "bps": bps},
        {"code": "603201", "ann_date": "bad", "bps": 1},
        {"code": "603201", "ann_date": "2023-08-31", "bps": 0},
        {"code": "000060", "ann_date": "2023-10-31", "bps": None},
    ]
    holder = [
        {"code": "603201", "ann_date": "2023-08-31", "end_date": "2023-06-30", "shareholder": 10000},
        {"code": "603201", "ann_date": "2023-10-31", "end_date": "2023-09-30", "shareholder": 11000},
        {"code": "000060", "ann_date": "2023-10-31", "end_date": "2023-09-30", "shareholder": 0},
    ]

    class DB:
        def __getitem__(self, name):
            if "holder" in name:
                return Col(holder)
            return Col(ps)

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __getitem__(self, name):
            return DB()

        def close(self):
            return None

    import pymongo

    monkeypatch.setattr(pymongo, "MongoClient", Client)
    provider = FundamentalDataProvider()
    provider.load_from_mongodb()
    assert provider.get_bps("603201", "2024-01-02") == pytest.approx(bps)
    assert provider.get_holder_change("603201", "2024-01-02") is not None


def test_load_from_xtdata_with_fake_frames(monkeypatch, stock_ohlcv_by_code, signal_names_raw):
    dragon = next(d for d in signal_names_raw["dragons"] if d["code"] == "603201")
    df = stock_ohlcv_by_code["603201"]
    close = float(df[df["trade_date"] == "2024-01-02"].iloc[0]["close"])
    bps = close / float(dragon["f39_pb"])
    psh = pd.DataFrame(
        [
            {"m_anntime": "20231031", "s_fa_bps": bps},
            {"m_anntime": "", "s_fa_bps": 1.0},
        ]
    )
    hld = pd.DataFrame(
        [
            {"declareDate": "20230831", "endDate": "20230630", "shareholder": 10000},
            {"declareDate": "20231031", "endDate": "20230930", "shareholder": 11000},
        ]
    )

    class XT:
        def download_financial_data2(self, *args, **kwargs):
            return None

        def get_financial_data(self, codes, table_list, **kwargs):
            table = table_list[0]
            if table == "Pershareindex":
                return {"603201.SH": {"Pershareindex": psh}}
            return {"603201.SH": {"Holdernum": hld}}

    import sys

    monkeypatch.setitem(sys.modules, "xtquant", SimpleNamespace(xtdata=XT()))
    monkeypatch.setitem(sys.modules, "xtquant.xtdata", XT())
    provider = FundamentalDataProvider()
    provider.load_from_xtdata(["603201.SH"], start_time="20230101", end_time="20240102")
    assert provider.get_bps("603201", "2024-01-02") == pytest.approx(bps)
