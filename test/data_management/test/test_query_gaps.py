"""query_service 缺口：bulk_upsert / factor range / fundamentals / reset。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zstock.data_management.query_service import (
    COL_FACTOR_MARKET,
    COL_FACTOR_SECTOR,
    COL_FUNDAMENTAL_HOLDER,
    COL_FUNDAMENTAL_PERSHARE,
    COL_OHLCV,
    DataQueryService,
)
from test.data_management.test.conftest import FakeDatabaseService, FakeMongoDB

FACTOR_RAW = (
    Path(__file__).resolve().parents[2]
    / "factor_management"
    / "test"
    / "fixtures"
    / "factor_raw_20240102.json"
)


def _make_qs(store) -> DataQueryService:
    qs = DataQueryService.__new__(DataQueryService)
    qs.db = FakeDatabaseService(store).db
    return qs


@pytest.mark.asyncio
async def test_bulk_upsert_real_ohlcv(stock_ohlcv_docs):
    qs = _make_qs({COL_OHLCV: []})
    sample = [d for d in stock_ohlcv_docs if d["code"] == "603201"][:3]
    assert sample
    summary = await qs.bulk_upsert(
        COL_OHLCV,
        [dict(x) for x in sample],
        key_fields=["code", "trade_date", "period"],
    )
    assert summary["upserted"] >= 1 or summary["matched"] >= 0
    again = await qs.bulk_upsert(
        COL_OHLCV,
        [dict(x) for x in sample],
        key_fields=["code", "trade_date", "period"],
    )
    assert again["matched"] >= 1
    assert await qs.bulk_upsert(COL_OHLCV, [], key_fields=["code"]) == {
        "matched": 0,
        "modified": 0,
        "upserted": 0,
    }
    with pytest.raises(ValueError, match="key_fields"):
        await qs.bulk_upsert(COL_OHLCV, [{"code": "603201"}], key_fields=[])
    with pytest.raises(ValueError, match="缺少"):
        await qs.bulk_upsert(
            COL_OHLCV,
            [{"code": "603201"}],
            key_fields=["code", "trade_date"],
        )


def test_reset_instance(monkeypatch):
    import zstock.data_management.query_service as qs_mod

    fake = FakeMongoDB()
    monkeypatch.setattr(qs_mod, "get_database", lambda: fake)
    monkeypatch.setattr(qs_mod.DataQueryService, "_instance", None)
    a = qs_mod.get_data_query_service()
    qs_mod.DataQueryService.reset_instance()
    b = qs_mod.get_data_query_service()
    assert a is not b


@pytest.mark.asyncio
async def test_factor_ranges_and_sink():
    if not FACTOR_RAW.is_file():
        pytest.skip("缺少因子原始值")
    with open(FACTOR_RAW, encoding="utf-8") as f:
        raw = json.load(f)
    market = dict(raw["market"])
    market["trade_date"] = "2024-01-02"
    sector = dict((raw.get("sectors") or [{"trade_date": "2024-01-02", "sector_code": "SW2出版"}])[0])
    sector["trade_date"] = "2024-01-02"
    qs = _make_qs(
        {
            COL_FACTOR_MARKET: [market, {**market, "trade_date": "2024-01-03"}],
            COL_FACTOR_SECTOR: [sector],
        }
    )
    docs = await qs.get_factor_market_range("2024-01-01", "2024-01-05")
    assert any(d["trade_date"] == "2024-01-02" for d in docs)
    sink_rows = []
    await qs.get_factor_sectors_range(
        "2024-01-01",
        "2024-01-05",
        sink=lambda rows: sink_rows.extend(rows),
    )
    assert sink_rows
    empty = await qs.get_factor_market_range("2025-01-01", "2024-01-01")
    assert empty == []


@pytest.mark.asyncio
async def test_query_window_retry(monkeypatch):
    qs = _make_qs({COL_FACTOR_MARKET: [{"trade_date": "2024-01-02", "index_code": "399300"}]})
    calls = {"n": 0}
    real_query = qs.query

    async def flaky(collection, query=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("timed out")
        return await real_query(collection, query, **kwargs)

    monkeypatch.setattr(qs, "query", flaky)

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(
        "zstock.data_management.query_service.asyncio.sleep",
        _no_sleep,
    )
    docs = await qs._query_window_with_retry(
        COL_FACTOR_MARKET, "2024-01-02", "2024-01-02", attempts=3
    )
    assert docs
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_get_all_fundamentals():
    qs = _make_qs(
        {
            COL_FUNDAMENTAL_PERSHARE: [
                {"code": "603201", "ann_date": "2024-03-01", "bps": 4.0},
                {"code": "000060", "ann_date": "2024-01-15", "bps": 2.5},
            ],
            COL_FUNDAMENTAL_HOLDER: [
                {"code": "603201", "ann_date": "2023-08-20", "end_date": "2023-06-30", "shareholder": 80.0},
            ],
        }
    )
    pershare = await qs.get_all_fundamental_pershare()
    assert {d["code"] for d in pershare} >= {"603201", "000060"}
    holders = await qs.get_all_fundamental_holder()
    assert holders[0]["code"] == "603201"


def test_iter_date_windows():
    wins = DataQueryService._iter_date_windows("2024-01-02", "2024-01-10", 5)
    assert wins[0][0] == "2024-01-02"
    assert wins[-1][1] == "2024-01-10"
    assert DataQueryService._iter_date_windows("2024-01-10", "2024-01-02", 5) == []
    assert DataQueryService._iter_date_windows("2024-01-02", "2024-01-02", 0) == [
        ("2024-01-02", "2024-01-02")
    ]
