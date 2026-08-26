"""同步 / 检查 / L2 / 导出脚本：解析与落库走 FakeMongo，数据来自真实夹具。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from zstock.data_management.query_service import COL_OHLCV, COL_STOCK_INFO
from zstock.data_management.script.L2数据.import_l2_capital_flow import (
    _is_valid_code,
    _parse_code,
    _parse_float,
    parse_filename,
    read_l2_file,
    _ensure_indexes,
)
from zstock.data_management.script.删除无用数据.cleanup_invalid_sectors import (
    _is_invalid_sector,
)
from zstock.data_management.script.exports.export_data import DataExporter
from zstock.data_management.script.sync_fundamental import (
    _from_xt_code,
    _parse_date_str,
    _safe_float,
)
from zstock.data_management.script.sync_ohlcv import _persist_ohlcv
from zstock.data_management.script.sync_stock_info import _persist_stock_flags
from zstock.data_management.script.sync_capital_flow import _persist_capital_flow_bulk
from zstock.data_management.test.conftest import FakeDatabaseService, FakeMongoDB


LIVE = (
    Path(__file__).resolve().parents[2]
    / "factor_management"
    / "test"
    / "fixtures"
    / "live_calc_20240102.json"
)


def test_l2_parsers_and_real_row_file(tmp_path):
    assert parse_filename("全部Ａ股_20240102.xls") == "20240102"
    assert parse_filename("全部A股_20240102_12.xls") == "20240102"
    assert parse_filename("other.xls") is None
    assert _parse_code("='000060'") == "000060"
    assert _is_valid_code("000060")
    assert not _is_valid_code("60")
    assert _parse_float("") == 0.0
    assert _parse_float("bad") == 0.0
    if not LIVE.is_file():
        pytest.skip("缺少资金流夹具")
    with open(LIVE, encoding="utf-8") as f:
        flow = json.load(f)["flow"]["000060"]
    row = next(d for d in flow if d["trade_date"] == "2024-01-02")
    path = tmp_path / "全部Ａ股_20240102.xls"
    header = "代码\t名称\t收盘\t主力净额\t成交额"
    line = f"='000060'\t{row.get('name', '中金岭南')}\t{row['close']}\t{row['main_net']}\t{row['turnover']}"
    path.write_text(f"数据 日期:2024-01-02 说明\n{header}\n{line}\n", encoding="gbk")
    td, rows = read_l2_file(str(path))
    assert td == "2024-01-02"
    assert rows[0]["code"] == "000060"
    assert rows[0]["close"] == pytest.approx(float(row["close"]))
    assert rows[0]["main_net"] == pytest.approx(float(row["main_net"]))


def test_l2_ensure_indexes_on_fake():
    db = FakeMongoDB()
    _ensure_indexes(db)
    _ensure_indexes(db)


def test_cleanup_invalid_sector_rules(sector_list_sample):
    keep = sector_list_sample["sample"][0]
    assert _is_invalid_sector(keep) is None
    assert _is_invalid_sector({"sector_name": "上期所铜", "sector_type": "sw"})
    assert _is_invalid_sector({"sector_name": "SW2贵金属", "sector_type": "exchange"})
    assert _is_invalid_sector({"sector_name": "300SW2贵金属", "sector_type": "sw"})
    assert _is_invalid_sector({"sector_name": "SW2贵金属加权", "sector_type": "sw"})
    assert _is_invalid_sector(
        {"sector_name": "期货板块", "sector_type": "sw", "stocks": ["cu2401", "au2406"]}
    )


def test_sync_fundamental_helpers():
    assert _parse_date_str("20240102") == "2024-01-02"
    assert _parse_date_str("2024-01-02") == "2024-01-02"
    assert _parse_date_str("20240102000000000") == "2024-01-02"
    assert _parse_date_str("bad") is None
    assert _parse_date_str(None) is None
    assert _from_xt_code("000060.SZ") == "000060"
    assert _safe_float("1.5") == 1.5
    assert _safe_float("nan") is None
    assert _safe_float(None) is None


@pytest.mark.asyncio
async def test_persist_ohlcv_and_stock_flags(stock_ohlcv_docs, stock_info_sample, monkeypatch):
    fake = FakeMongoDB()

    class Mgr:
        mongo_db = fake

        async def init_mongodb(self):
            return None

    monkeypatch.setattr("app.core.database.db_manager", Mgr())
    sample = pd.DataFrame([d for d in stock_ohlcv_docs if d["code"] == "603201"][:3])
    await _persist_ohlcv(sample, period="D")
    assert await fake[COL_OHLCV].count_documents({"code": "603201"}) >= 1
    await _persist_ohlcv(pd.DataFrame(), period="D")
    flags = [
        {"code": d["code"], "name": d.get("name", "")}
        for d in stock_info_sample["sample"][:5]
    ]
    await _persist_stock_flags(flags)
    assert await fake[COL_STOCK_INFO].count_documents({}) >= 1
    await _persist_stock_flags([])


@pytest.mark.asyncio
async def test_persist_capital_flow_from_live(monkeypatch):
    if not LIVE.is_file():
        pytest.skip("缺少资金流夹具")
    with open(LIVE, encoding="utf-8") as f:
        docs = json.load(f)["flow"]["000060"]
    df = pd.DataFrame(docs)
    fake = FakeMongoDB()

    class Mgr:
        mongo_db = fake

        async def init_mongodb(self):
            return None

    monkeypatch.setattr("app.core.database.db_manager", Mgr())
    n = await _persist_capital_flow_bulk(df)
    assert n == len(df)
    assert await _persist_capital_flow_bulk(pd.DataFrame()) == 0


@pytest.mark.asyncio
async def test_check_index_on_fake(hs300_rows, monkeypatch):
    fake = FakeMongoDB()
    fake[COL_OHLCV].docs = list(hs300_rows)

    class Mgr:
        mongo_db = fake
        mongo_client = fake

        async def init_mongodb(self):
            return None

        async def close_connections(self):
            return None

    class QS:
        async def get_ohlcv_batch(self, codes, start, end, **kwargs):
            return {}

    monkeypatch.setattr("app.core.database.db_manager", Mgr())
    monkeypatch.setattr(
        "zstock.data_management.query_service.get_data_query_service",
        lambda: QS(),
    )
    from zstock.data_management.script.check_index_data import check_index_data

    await check_index_data()


@pytest.mark.asyncio
async def test_check_coverage_on_fake(hs300_rows, monkeypatch):
    fake = FakeMongoDB()
    fake[COL_OHLCV].docs = list(hs300_rows)

    class Mgr:
        mongo_db = fake

        async def init_mongodb(self):
            return None

        async def close_connections(self):
            return None

    monkeypatch.setattr("app.core.database.db_manager", Mgr())
    from zstock.data_management.script.check_ohlcv_coverage import inspect

    await inspect("2024-01-02")
    await inspect(None)


@pytest.mark.asyncio
async def test_check_sector_on_fake(sector_list_sample, monkeypatch):
    from zstock.data_management.query_service import COL_SECTOR
    from zstock.data_management.script.check_sector import check

    fake = FakeMongoDB()
    docs = []
    for d in sector_list_sample["sample"]:
        item = dict(d)
        item.setdefault("source", "xtquant")
        item.setdefault("stocks", ["603201"])
        docs.append(item)
    fake[COL_SECTOR].docs = docs

    class Mgr:
        mongo_db = fake

        async def init_mongodb(self):
            return None

        async def close_connections(self):
            return None

    monkeypatch.setattr("app.core.database.db_manager", Mgr())
    monkeypatch.setattr("app.core.database.get_database", lambda: fake)
    await check()


@pytest.mark.asyncio
async def test_cleanup_run_dry(sector_list_sample, monkeypatch):
    from zstock.data_management.query_service import COL_SECTOR
    from zstock.data_management.script.删除无用数据.cleanup_invalid_sectors import run

    fake = FakeMongoDB()
    docs = []
    for d in sector_list_sample["sample"]:
        item = dict(d)
        item.setdefault("source", "xtquant")
        item.setdefault("stocks", ["603201"])
        docs.append(item)
    docs.append(
        {
            "sector_code": "FUT_CU",
            "sector_name": "上期所铜",
            "sector_type": "sw",
            "source": "xtquant",
            "stocks": ["cu2401"],
        }
    )
    fake[COL_SECTOR].docs = docs

    class Mgr:
        mongo_db = fake

        async def init_mongodb(self):
            return None

    monkeypatch.setattr("app.core.database.db_manager", Mgr())
    await run(apply=False)


@pytest.mark.asyncio
async def test_delete_today_period_empty(monkeypatch):
    from zstock.data_management.script.删除无用数据 import delete_today_period as mod

    fake = FakeMongoDB()

    class Mgr:
        mongo_db = fake

        async def init_mongodb(self):
            return None

        async def close_connections(self):
            return None

    monkeypatch.setattr("app.core.database.db_manager", Mgr())
    monkeypatch.setattr(mod.sys, "argv", ["delete_today_period.py", "--dry-run"])
    await mod.main()


@pytest.mark.asyncio
async def test_exporter_with_injected_query(stock_info_sample, tmp_path):
    docs = list(stock_info_sample["sample"][:3])
    qs = type("QS", (), {})()

    async def get_stock_info(symbol):
        hit = next((d for d in docs if d["code"] == symbol), None)
        if not hit:
            raise ValueError(symbol)
        return hit, "mongodb"

    qs.get_stock_info = get_stock_info
    exp = DataExporter.__new__(DataExporter)
    exp.output_dir = tmp_path
    exp.query_service = qs
    codes = [d["code"] for d in docs]
    path = await exp.export_stock_info_to_csv(codes)
    assert Path(path).is_file()
    files = await exp.export_multiple_symbols_to_separate_files(codes + ["999999"])
    assert len(files) == len(codes)
    with pytest.raises(ValueError):
        await exp.export_stock_info_to_csv(["999999"])
