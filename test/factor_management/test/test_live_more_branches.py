"""用 live_calc / factor_raw 再压一轮公式层分支。"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from zstock.factor_management.factors_dragon import DragonFactors
from zstock.factor_management.factors_sector import SectorFactors

LIVE = Path(__file__).resolve().parent / "fixtures" / "live_calc_20240102.json"
RAW = Path(__file__).resolve().parent / "fixtures" / "factor_raw_20240102.json"


def test_sector_aggregate_and_raw_from_docs():
    if not LIVE.is_file() or not RAW.is_file():
        pytest.skip("缺少夹具")
    with open(LIVE, encoding="utf-8") as f:
        live = json.load(f)
    with open(RAW, encoding="utf-8") as f:
        raw = json.load(f)
    members = live.get("members") or {}
    ohlcv = {c: pd.DataFrame(rows) for c, rows in (live.get("ohlcv") or {}).items()}
    if not members or not ohlcv:
        pytest.skip("live 空")
    sector_code, codes = next(iter(members.items()))
    codes = [c for c in codes if c in ohlcv][:15]
    if len(codes) < 3:
        pytest.skip("成分不足")
    sector_ohlcv, sector_flow = SectorFactors.aggregate_sector_ohlcv(
        {sector_code: codes},
        {c: ohlcv[c] for c in codes},
        trade_date="2024-01-02",
    )
    assert isinstance(sector_ohlcv, dict)
    assert isinstance(sector_flow, dict)
    docs = list(raw.get("sectors") or [])[:5]
    rebuilt = SectorFactors.raw_from_docs(docs)
    assert isinstance(rebuilt, dict)


def test_dragon_raw_from_doc_and_multi():
    if not RAW.is_file() or not LIVE.is_file():
        pytest.skip("缺少夹具")
    with open(RAW, encoding="utf-8") as f:
        raw = json.load(f)
    with open(LIVE, encoding="utf-8") as f:
        live = json.load(f)
    dragons = list(raw.get("dragons") or [])
    assert dragons
    one = DragonFactors.raw_from_doc(dragons[0])
    assert "f32_amount" in one
    ohlcv = {c: pd.DataFrame(rows) for c, rows in (live.get("ohlcv") or {}).items()}
    members = live.get("members") or {}
    if not members:
        return
    sector_code, codes = next(iter(members.items()))
    codes = [c for c in codes if c in ohlcv][:8]
    if len(codes) < 2:
        return
    out = DragonFactors.calculate_all_dragon_factors_raw(
        [(sector_code, codes)],
        {c: ohlcv[c] for c in codes},
        trade_date="2024-01-02",
    )
    assert isinstance(out, dict)
