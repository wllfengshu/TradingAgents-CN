"""因子层夹具：SSOT + Mongo 导出的 2024-01-02 预计算原始值 + 沪深300 日线。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PARAMS_PATH = PROJECT_ROOT / "zstock" / "common" / "config" / "strategy_params.json"
OVERNIGHT_DIR = (
    PROJECT_ROOT
    / "zstock"
    / "strategy_management"
    / "script"
    / "output"
    / "overnight_v16_20260824"
)
FIXTURES = Path(__file__).resolve().parent / "fixtures"
STRATEGY_OHLCV = (
    PROJECT_ROOT / "zstock" / "strategy_management" / "test" / "fixtures" / "ohlcv_bundle.json"
)


@pytest.fixture(scope="session")
def strategy_params() -> Dict[str, Any]:
    with open(PARAMS_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def overnight_dir() -> Path:
    if not OVERNIGHT_DIR.is_dir():
        pytest.skip(f"缺少隔夜产物: {OVERNIGHT_DIR}")
    return OVERNIGHT_DIR


@pytest.fixture(scope="session")
def factor_eval_2024_csv(overnight_dir: Path) -> Path:
    path = overnight_dir / "factor_eval" / "2024" / "summary.csv"
    if not path.is_file():
        pytest.skip(f"缺少因子测评: {path}")
    return path


@pytest.fixture(scope="session")
def hs300_ohlcv() -> pd.DataFrame:
    path = FIXTURES / "hs300_ohlcv.json"
    if not path.is_file():
        pytest.skip("缺少 399300 日线夹具")
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    df = pd.DataFrame(payload.get("rows") or [])
    if len(df) < 31:
        pytest.skip(f"沪深300 根数不足 31: {len(df)}")
    return df


@pytest.fixture(scope="session")
def factor_raw() -> Dict[str, Any]:
    path = FIXTURES / "factor_raw_20240102.json"
    if not path.is_file():
        pytest.skip("缺少 2024-01-02 因子原始值夹具")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def signal_names_raw() -> Dict[str, Any]:
    path = FIXTURES / "signal_names_20240102.json"
    if not path.is_file():
        pytest.skip("缺少信号标的因子夹具")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def stock_ohlcv_by_code() -> Dict[str, pd.DataFrame]:
    if not STRATEGY_OHLCV.is_file():
        pytest.skip("缺少策略层 OHLCV 夹具")
    with open(STRATEGY_OHLCV, encoding="utf-8") as f:
        payload = json.load(f)
    return {code: pd.DataFrame(rows) for code, rows in (payload.get("by_code") or {}).items()}


def make_offline_pipeline(config: Dict[str, Any], qs=None):
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline
    from zstock.factor_management.prefilters import PreFilters

    pipe = CrossSectionStrategyPipeline.__new__(CrossSectionStrategyPipeline)
    pipe.config = config
    pipe.prefilters = PreFilters()
    pipe._query_service = qs
    pipe._reset_precomputed()
    pipe._all_stocks_cache = None
    pipe._sectors_cache = None
    pipe._fund_provider = None
    pipe._fund_provider_tried = False
    pipe._style_cache = {}
    return pipe


def make_ohlcv(
    n: int,
    close: Union[float, Sequence[float]] = 10.0,
    *,
    volume: Union[float, Sequence[float]] = 1_000_000.0,
    amount: Optional[Union[float, Sequence[float]]] = None,
    turnover: Optional[Union[float, Sequence[float]]] = None,
    start: str = "2023-10-09",
) -> pd.DataFrame:
    """构造升序日线，供因子公式 UT 用。"""
    dates = pd.bdate_range(start, periods=n).strftime("%Y-%m-%d").tolist()
    c = np.full(n, float(close), dtype=float) if np.isscalar(close) else np.asarray(close, dtype=float)
    if len(c) != n:
        raise ValueError("close 长度必须等于 n")
    vol = np.full(n, float(volume), dtype=float) if np.isscalar(volume) else np.asarray(volume, dtype=float)
    amt = (c * vol) if amount is None else (
        np.full(n, float(amount), dtype=float) if np.isscalar(amount) else np.asarray(amount, dtype=float)
    )
    high = np.maximum(c * 1.01, c)
    low = np.minimum(c * 0.99, c)
    out = pd.DataFrame(
        {
            "trade_date": dates,
            "open": c,
            "high": high,
            "low": low,
            "close": c,
            "volume": vol,
            "amount": amt,
        }
    )
    if turnover is not None:
        out["turnover"] = (
            np.full(n, float(turnover), dtype=float)
            if np.isscalar(turnover)
            else np.asarray(turnover, dtype=float)
        )
    return out


def docs_to_field_map(docs: List[Dict[str, Any]], key: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for d in docs:
        code = d.get(key)
        if not code:
            continue
        for field, val in d.items():
            if field.startswith("f") or field.startswith("mf"):
                out.setdefault(field, {})[code] = val
    return out
