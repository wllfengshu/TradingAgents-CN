"""common 层夹具：SSOT JSON + Mongo 导出的真实 OHLCV / 截面。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PARAMS_PATH = PROJECT_ROOT / "zstock" / "common" / "config" / "strategy_params.json"
FACTOR_FIXTURES = PROJECT_ROOT / "test" / "factor_management" / "test" / "fixtures"
STRATEGY_FIXTURES = PROJECT_ROOT / "test" / "strategy_management" / "test" / "fixtures"
DM_FIXTURES = PROJECT_ROOT / "test" / "data_management" / "test" / "fixtures"


@pytest.fixture(scope="session")
def strategy_params() -> Dict[str, Any]:
    with open(PARAMS_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def hs300_ohlcv() -> pd.DataFrame:
    path = FACTOR_FIXTURES / "hs300_ohlcv.json"
    if not path.is_file():
        path = DM_FIXTURES / "hs300_ohlcv.json"
    if not path.is_file():
        pytest.skip("缺少 399300 日线夹具")
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    df = pd.DataFrame(payload.get("rows") or [])
    if df.empty:
        pytest.skip("沪深300 夹具为空")
    return df


@pytest.fixture(scope="session")
def stock_ohlcv_by_code() -> Dict[str, pd.DataFrame]:
    path = STRATEGY_FIXTURES / "ohlcv_bundle.json"
    if not path.is_file():
        pytest.skip("缺少个股 OHLCV 夹具")
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    return {code: pd.DataFrame(rows) for code, rows in (payload.get("by_code") or {}).items()}


@pytest.fixture(scope="session")
def signal_names_raw() -> Dict[str, Any]:
    path = FACTOR_FIXTURES / "signal_names_20240102.json"
    if not path.is_file():
        pytest.skip("缺少信号标的因子夹具")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def stock_info_sample() -> List[Dict[str, Any]]:
    path = DM_FIXTURES / "stock_info_sample.json"
    if not path.is_file():
        pytest.skip("缺少 stock_info 样本")
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    return list(payload.get("sample") or [])
