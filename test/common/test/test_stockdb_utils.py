"""stockdb_utils：时间戳规范化、int 键别名、本地资金流。"""

from __future__ import annotations

import pytest

from zstock.common.utils.stockdb_utils import (
    StockDbClient,
    StockDbError,
    _group_bars,
    _local_flow_row,
    _normalize_bar_timestamp,
    update_data,
)


def test_normalize_bar_timestamp_shapes():
    assert _normalize_bar_timestamp("20240102") == "2024-01-02"
    assert _normalize_bar_timestamp(20260916093500) == "2026-09-16 09:35:00"
    assert _normalize_bar_timestamp("202609160935") == "2026-09-16 09:35:00"
    assert _normalize_bar_timestamp(None) == ""


def test_group_bars_int_key_alias_and_error():
    grouped = _group_bars({"1": [{"date": "20240102", "close": 1.0}]}, ["000001"])
    assert grouped["000001"]
    with pytest.raises(StockDbError):
        _group_bars({"error": "down"}, ["600000"])
    with pytest.raises(StockDbError):
        _group_bars([{"close": 1}], ["600000", "000001"])


def test_local_flow_row_yuan_to_wan():
    row = _local_flow_row(
        "000001",
        {
            "date": 20240102,
            "main_net": 50000.0,
            "jumbo_net": 20000.0,
            "big_net": 10000.0,
            "mid_net": 5000.0,
            "small_net": 1000.0,
        },
    )
    assert row["trade_date"] == "2024-01-02"
    assert row["main_net"] == pytest.approx(5.0)
    assert row["xl_net"] == pytest.approx(2.0)
    assert row["l_net"] == pytest.approx(1.0)
    assert row["m_net"] == pytest.approx(0.5)
    assert row["s_net"] == pytest.approx(0.1)


def test_stockdb_require_and_flow_error():
    client = StockDbClient(sdk=object())
    client._ready = False
    with pytest.raises(StockDbError, match="未连接"):
        client.get_bars(["600000"], "2024-01-01", "2024-01-02")

    class BoomQuery:
        def do(self):
            return {"error": "flow down"}

    class BoomRd:
        def get(self, *args, **kwargs):
            return BoomQuery()

    class BoomSdk:
        rd = BoomRd()

        def get_trade_days(self, start_date=None, end_date=None):
            return ["2024-01-02"]

    client = StockDbClient(sdk=BoomSdk())
    client.connect()
    with pytest.raises(StockDbError, match="flow down"):
        client.get_money_flow(["600000"], "2024-01-01", "2024-01-02")


def test_group_bars_single_symbol_list_and_minute_frame():
    from zstock.common.utils.stockdb_utils import _bars_frame

    grouped = _group_bars(
        [{"date": 20240102093500, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "vol": 10}],
        ["603201"],
    )
    assert grouped["603201"]
    frame = _bars_frame("603201", grouped["603201"])
    assert "2024-01-02 09:35:00" in str(frame["trade_date"].iloc[0])
    assert frame["volume"].iloc[0] == 10
    empty = _bars_frame("603201", [])
    assert list(empty.columns)
    assert _group_bars({}, ["603201"])["603201"] == []


def test_money_flow_empty_chunk():
    class EmptyQuery:
        def do(self):
            return None

    class Rd:
        def get(self, *args, **kwargs):
            return EmptyQuery()

    class Sdk:
        rd = Rd()

        def get_trade_days(self, start_date=None, end_date=None):
            return ["2024-01-02"]

    client = StockDbClient(sdk=Sdk())
    client.connect()
    flow = client.get_money_flow(["603201"], "2024-01-01", "2024-01-02")
    assert "603201" in flow
    assert flow["603201"].empty or list(flow["603201"].columns)


class _FakeUpdater:
    def __init__(self, args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.pid = 4321
        self.returncode = 0

    def communicate(self, timeout=None):
        return b"v0.3.5\n", b""


def test_update_data_builds_official_command(tmp_path, monkeypatch):
    exe = tmp_path / "数据更新.exe"
    exe.write_bytes(b"")
    captured = {}

    def _popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeUpdater(args, **kwargs)

    monkeypatch.setattr("zstock.common.utils.stockdb_utils.subprocess.Popen", _popen)
    result = update_data(
        str(tmp_path),
        run_at="15:50:00",
        source_url="http://ah.123128.xyz",
        threads=4,
        retries=2,
        secret="hidden",
        verbose_ip=True,
        no_keep_old=True,
    )
    assert result["returncode"] == 0
    assert result["running"] is False
    assert result["pid"] == 4321
    assert result["stdout"].startswith("v0.3.5")
    assert captured["args"][0].endswith("数据更新.exe")
    assert captured["args"][1:] == [
        "-run", "15:50:00",
        "--url", "http://ah.123128.xyz",
        "--threads", "4",
        "--retries", "2",
        "--secret", "hidden",
        "--verbose-ip",
        "--no-keep-old",
    ]
    assert captured["kwargs"]["cwd"] == str(tmp_path)

    client = StockDbClient(pybao_dir=str(tmp_path / "pybao"), sdk=object())
    again = client.update_data(wait=False)
    assert again["running"] is True
    assert again["cwd"] == str(tmp_path)


def test_update_data_rejects_bad_clock_and_missing_exe(tmp_path):
    with pytest.raises(StockDbError, match="找不到数据更新程序"):
        update_data(str(tmp_path))
    exe = tmp_path / "数据更新.exe"
    exe.write_bytes(b"")
    with pytest.raises(StockDbError, match="-run"):
        update_data(str(tmp_path), run_at="下午三点")
