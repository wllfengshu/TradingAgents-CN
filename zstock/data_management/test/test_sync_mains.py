"""同步 / 导入 / 导出脚本的 main：FakeMongo + 真实夹具，不连 QMT。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from zstock.data_management.query_service import (
    COL_CAPITAL_FLOW,
    COL_OHLCV,
    COL_SECTOR,
    COL_STOCK_INFO,
)
from zstock.data_management.test.conftest import FakeDatabaseService, FakeMongoDB

LIVE = (
    Path(__file__).resolve().parents[2]
    / "factor_management"
    / "test"
    / "fixtures"
    / "live_calc_20240102.json"
)
SIGNAL = (
    Path(__file__).resolve().parents[2]
    / "factor_management"
    / "test"
    / "fixtures"
    / "signal_names_20240102.json"
)


def _mgr(fake: FakeMongoDB):
    class Mgr:
        mongo_db = fake

        async def init_mongodb(self):
            self.mongo_db = fake

        async def close_connections(self):
            return None

    return Mgr()


def _patch_db(monkeypatch, fake: FakeMongoDB, fake_ds: FakeDatabaseService | None = None):
    mgr = _mgr(fake)
    monkeypatch.setattr("app.core.database.db_manager", mgr)
    monkeypatch.setattr("app.core.database.get_database", lambda: fake)
    ds = fake_ds or FakeDatabaseService({})
    monkeypatch.setattr(
        "zstock.data_management.query_service.get_database_service", lambda: ds
    )
    monkeypatch.setattr("zstock.data_management.query_service._data_query_service", None)
    return mgr, ds


@pytest.mark.asyncio
async def test_sync_ohlcv_main(stock_ohlcv_docs, hs300_rows, stock_info_sample, monkeypatch):
    fake = FakeMongoDB()
    stocks = [
        d for d in stock_info_sample["sample"] if d["code"] in {"603201", "601107", "000060"}
    ]
    if not stocks:
        stocks = [{"code": "603201", "name": "常润股份", "is_mainboard": True, "is_st": False}]
    ds = FakeDatabaseService({COL_STOCK_INFO: stocks})
    _patch_db(monkeypatch, fake, ds)
    df_stocks = pd.DataFrame([d for d in stock_ohlcv_docs if d["code"] == "603201"][:8])
    df_idx = pd.DataFrame(hs300_rows[:8])

    monkeypatch.setattr(
        "zstock.common.utils.xtquant_data_utils.fetch_ohlcv_batch",
        lambda codes, start, end, **k: df_stocks,
    )

    def _fetch_idx(code, start, end, **k):
        if code == "399300":
            return df_idx
        if code == "399001":
            raise RuntimeError("timeout")
        return pd.DataFrame()

    monkeypatch.setattr(
        "zstock.common.utils.xtquant_data_utils.fetch_ohlcv", _fetch_idx
    )
    monkeypatch.setattr(
        "zstock.common.utils.xtquant_data_utils.fetch_float_shares_map",
        lambda codes: {},
    )
    monkeypatch.setattr("sys.argv", ["sync_ohlcv.py", "--start", "2024-01-02", "--end", "2024-01-16"])
    from zstock.data_management.script.sync_ohlcv import main

    await main()
    assert await fake[COL_OHLCV].count_documents({"code": "603201"}) >= 1


@pytest.mark.asyncio
async def test_sync_stock_info_and_sector_persist(stock_info_sample, sector_list_sample, monkeypatch):
    fake = FakeMongoDB()
    _patch_db(monkeypatch, fake)
    flags = [
        {"code": d["code"], "name": d.get("name", "")}
        for d in stock_info_sample["sample"][:8]
    ]
    monkeypatch.setattr(
        "zstock.common.utils.xtquant_data_utils.fetch_all_stocks",
        lambda: flags,
    )
    from zstock.data_management.script.sync_stock_info import (
        _persist_sector_meta,
        _persist_sector_stocks,
        main,
    )

    await main()
    assert await fake[COL_STOCK_INFO].count_documents({}) >= 1
    sectors = [
        {
            "sector_code": d["sector_code"],
            "sector_name": d.get("sector_name", ""),
            "sector_type": d.get("sector_type", "sw"),
        }
        for d in sector_list_sample["sample"][:3]
    ]
    await _persist_sector_meta(sectors)
    await _persist_sector_meta([])
    await _persist_sector_stocks(sectors[0]["sector_code"], ["603201", "000060"])
    await _persist_sector_stocks("X", [])
    assert await fake[COL_SECTOR].count_documents({}) >= 1


@pytest.mark.asyncio
async def test_sync_lhb_main(monkeypatch):
    fake = FakeMongoDB()
    _patch_db(monkeypatch, fake)
    monkeypatch.setattr(
        "zstock.common.utils.xtquant_data_utils.fetch_all_stocks",
        lambda: [{"code": "603201", "name": "常润股份"}],
    )
    monkeypatch.setattr(
        "zstock.common.utils.xtquant_data_utils.fetch_lhb",
        lambda codes, start, end: [
            {"code": "603201", "trade_date": "2024-01-02"},
            {"code": "", "trade_date": "2024-01-02"},
        ],
    )
    monkeypatch.setattr(
        "sys.argv",
        ["sync_lhb.py", "--start", "2024-01-02", "--end", "2024-01-02"],
    )
    from zstock.data_management.script.sync_lhb import main, sync_longhubang

    n = await main()
    assert n >= 1
    monkeypatch.setattr(
        "zstock.common.utils.xtquant_data_utils.fetch_lhb",
        lambda *a, **k: [],
    )
    assert await sync_longhubang("2024-01-02", "2024-01-02") == 0
    monkeypatch.setattr("sys.argv", ["sync_lhb.py", "--date", "2024-01-02"])
    await main()
    monkeypatch.setattr("sys.argv", ["sync_lhb.py"])
    await main()


@pytest.mark.asyncio
async def test_sync_capital_flow_main(monkeypatch):
    if not LIVE.is_file():
        pytest.skip("缺少资金流夹具")
    with open(LIVE, encoding="utf-8") as f:
        rows = json.load(f)["flow"]["000060"]
    df = pd.DataFrame(rows)
    df["period"] = "today"
    fake = FakeMongoDB()
    _patch_db(monkeypatch, fake)

    monkeypatch.setattr(
        "zstock.common.utils.a_stock_data_utils.fetch_money_flow_all",
        lambda period: df,
    )
    from zstock.data_management.script.sync_capital_flow import main, sync_one_period

    await main()
    assert await fake[COL_CAPITAL_FLOW].count_documents({}) >= 1

    calls = {"n": 0}

    def _flaky(period):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("wind")
        return df

    monkeypatch.setattr(
        "zstock.common.utils.a_stock_data_utils.fetch_money_flow_all",
        _flaky,
    )

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(
        "zstock.data_management.script.sync_capital_flow.asyncio.sleep",
        _no_sleep,
    )
    n = await sync_one_period(None, "today", max_retries=2)
    assert n == len(df)

    monkeypatch.setattr(
        "zstock.common.utils.a_stock_data_utils.fetch_money_flow_all",
        lambda period: pd.DataFrame(),
    )
    assert await sync_one_period(None, "today", max_retries=1) == 0


@pytest.mark.asyncio
async def test_sync_fundamental_main(stock_ohlcv_docs, monkeypatch):
    if not SIGNAL.is_file():
        pytest.skip("缺少信号夹具")
    with open(SIGNAL, encoding="utf-8") as f:
        payload = json.load(f)
    dragon = next(d for d in payload["dragons"] if d["code"] == "603201")
    row = next(
        d
        for d in stock_ohlcv_docs
        if d["code"] == "603201" and d["trade_date"] == "2024-01-02"
    )
    close = float(row["close"])
    bps = close / float(dragon["f39_pb"])
    fake = FakeMongoDB()
    _patch_db(monkeypatch, fake)

    psh = pd.DataFrame(
        [
            {"m_anntime": "20231031", "s_fa_bps": bps, "s_fa_eps_basic": None},
            {"m_anntime": "bad", "s_fa_bps": 1.0},
            {"m_anntime": "20230831"},
        ]
    )
    hld = pd.DataFrame(
        [
            {
                "declareDate": "20231031",
                "endDate": "20230930",
                "shareholder": 11000,
                "shareholderA": 10000,
            },
            {"declareDate": "x", "endDate": "20230930", "shareholder": 1},
            {"declareDate": "20230831", "endDate": "20230630", "shareholder": 0},
        ]
    )

    class XT:
        def download_financial_data2(self, *a, **k):
            return None

        def get_financial_data(self, codes, table_list, **k):
            table = table_list[0]
            if table == "Pershareindex":
                return {"603201.SH": {"Pershareindex": psh}, "000060.SZ": {"Pershareindex": None}}
            return {"603201.SH": {"Holdernum": hld}, "000060.SZ": {"Holdernum": pd.DataFrame()}}

    monkeypatch.setattr(
        "zstock.common.utils.xtquant_data_utils._get_xtdata", lambda: XT()
    )
    monkeypatch.setattr(
        "zstock.common.utils.xtquant_data_utils.fetch_all_stocks",
        lambda: [{"code": "603201", "name": "常润股份"}, {"code": "000060", "name": "中金岭南"}],
    )
    monkeypatch.setattr(
        "zstock.common.utils.xtquant_data_utils.to_xt_code",
        lambda c: "603201.SH" if c.startswith("6") else f"{c}.SZ",
    )
    monkeypatch.setattr(
        "sys.argv",
        ["sync_fundamental.py", "--start", "2023-01-01", "--end", "2024-01-02"],
    )
    from zstock.data_management.script.sync_fundamental import main

    await main()
    assert await fake["zstock_fundamental_pershare"].count_documents({}) >= 1
    assert await fake["zstock_fundamental_holder"].count_documents({}) >= 1


@pytest.mark.asyncio
async def test_l2_import_main_dry_and_write(tmp_path, monkeypatch):
    if not LIVE.is_file():
        pytest.skip("缺少资金流夹具")
    with open(LIVE, encoding="utf-8") as f:
        row = next(d for d in json.load(f)["flow"]["000060"] if d["trade_date"] == "2024-01-02")
    work = tmp_path / "work"
    work.mkdir()
    header = "代码\t名称\t收盘\t主力净额\t成交额"
    line = f"='000060'\t中金岭南\t{row['close']}\t{row['main_net']}\t{row['turnover']}"
    (work / "全部Ａ股_20240102.xls").write_text(
        f"数据 日期:2024-01-02 说明\n{header}\n{line}\n", encoding="gbk"
    )
    (work / "全部Ａ股_20240102_2.xls").write_text("dup", encoding="gbk")
    (work / "other.xls").write_text("x", encoding="gbk")
    (work / "全部Ａ股_20240103.xls").write_text(
        "数据 日期:2024-01-03 说明\n代码\n='xx'\n", encoding="gbk"
    )
    from zstock.data_management.script.L2数据 import import_l2_capital_flow as mod

    monkeypatch.setattr(mod, "DATA_DIR", work)
    monkeypatch.setattr("sys.argv", ["import_l2.py", "--dry-run", "--start", "20240101", "--end", "20240131"])
    mod.main()

    class SyncCol:
        def __init__(self):
            self.docs = []

        def list_indexes(self):
            return [{"name": n} for n in getattr(self, "_names", ["_id_"])]

        def create_index(self, *a, **k):
            self._names = getattr(self, "_names", ["_id_"])
            self._names.append(k.get("name") or "idx")
            return k.get("name")

        def distinct(self, *a, **k):
            return []

        def bulk_write(self, ops, ordered=False):
            self.docs.extend(ops)
            return SimpleNamespace(upserted_count=len(ops), modified_count=0)

    col = SyncCol()

    class SyncDB:
        def __getitem__(self, name):
            return col

    class SyncClient:
        address = ("localhost", 27017)

        def __getitem__(self, name):
            return SyncDB()

    monkeypatch.setattr(mod, "_connect_mongo", lambda: (SyncClient(), SyncDB()))
    monkeypatch.setattr("sys.argv", ["import_l2.py", "--force"])
    mod.main()
    assert col.docs

    monkeypatch.setattr(mod, "DATA_DIR", tmp_path / "missing")
    monkeypatch.setattr("sys.argv", ["import_l2.py"])
    with pytest.raises(SystemExit):
        mod.main()

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(mod, "DATA_DIR", empty)
    monkeypatch.setattr("sys.argv", ["import_l2.py", "--dry-run"])
    mod.main()


@pytest.mark.asyncio
async def test_export_and_delete_and_cleanup_apply(
    stock_info_sample, sector_list_sample, monkeypatch, tmp_path
):
    fake = FakeMongoDB()
    docs = list(stock_info_sample["sample"][:3])
    fake[COL_STOCK_INFO].docs = docs
    fake[COL_CAPITAL_FLOW].docs = [
        {"code": "000060", "trade_date": "2024-01-02", "period": "today", "main_net": 1.0}
    ]
    from zstock.data_management.query_service import COL_SECTOR

    sec_docs = []
    for d in sector_list_sample["sample"][:2]:
        item = dict(d)
        item.setdefault("source", "xtquant")
        item.setdefault("stocks", ["603201"])
        sec_docs.append(item)
    sec_docs.append(
        {
            "sector_code": "FUT_CU",
            "sector_name": "上期所铜",
            "sector_type": "sw",
            "source": "xtquant",
            "stocks": ["cu2401"],
        }
    )
    fake[COL_SECTOR].docs = sec_docs
    _patch_db(monkeypatch, fake)

    class QS:
        async def get_stock_info(self, symbol):
            hit = next((d for d in docs if d["code"] == symbol), None)
            if not hit:
                raise ValueError(symbol)
            return hit, "mongodb"

    async def _init():
        return None

    async def _close():
        return None

    monkeypatch.setattr("zstock.common.utils.db_utils.init_zstock_database", _init)
    monkeypatch.setattr("zstock.common.utils.db_utils.close_zstock_database", _close)
    from zstock.data_management.script.exports import export_data as exp_mod

    monkeypatch.setattr(exp_mod, "get_data_query_service", lambda: QS())
    monkeypatch.setattr(exp_mod, "PROJECT_ROOT", tmp_path)
    await exp_mod.main()

    from zstock.data_management.script.删除无用数据 import delete_today_period as del_mod

    monkeypatch.setattr(del_mod.sys, "argv", ["delete_today_period.py", "--dry-run"])
    await del_mod.main()

    from zstock.data_management.script.删除无用数据.cleanup_invalid_sectors import run

    await run(apply=True)
    remaining = await fake[COL_SECTOR].count_documents({"source": "xtquant"})
    assert remaining < len(sec_docs)


def test_check_ohlcv_main_parses_argv(monkeypatch):
    from zstock.data_management.script import check_ohlcv_coverage as mod

    called = {}

    async def fake_inspect(td=None):
        called["td"] = td

    monkeypatch.setattr(mod, "inspect", fake_inspect)
    monkeypatch.setattr("sys.argv", ["check_ohlcv_coverage.py", "--trade-date", "2024-01-02"])
    mod.main()
    assert called["td"] == "2024-01-02"


def test_cleanup_cli_main(monkeypatch):
    from zstock.data_management.script.删除无用数据 import cleanup_invalid_sectors as mod

    called = {}

    async def fake_run(apply=False):
        called["apply"] = apply

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["cleanup.py", "--apply"])
    mod.main()
    assert called["apply"] is True
