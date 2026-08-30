"""strategy_management 测试公共夹具。

数据来源（按优先级）：
1. 正式 SSOT：zstock/common/config/strategy_params.json
2. 已落盘的隔夜回测产物 overnight_v16_20260824
3. 从 Mongo 导出的真实 score_signals / OHLCV（test/fixtures/）
4. 当场连 Mongo 拉一天预计算信号（不可用则跳过依赖真实截面的用例）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
STRATEGY_PARAMS_PATH = PROJECT_ROOT / "zstock" / "common" / "config" / "strategy_params.json"
OVERNIGHT_DIR = (
    PROJECT_ROOT
    / "zstock"
    / "strategy_management"
    / "script"
    / "output"
    / "overnight_v16_20260824"
)
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SIGNALS_FIXTURE = FIXTURES_DIR / "score_signals_bundle.json"
OHLCV_FIXTURE = FIXTURES_DIR / "ohlcv_bundle.json"

# 与 signal_consistency_check 文档一致的历史交易日；若红灯则回退到隔夜覆盖的其他日期。
CANDIDATE_SIGNAL_DATES = (
    "2024-06-03",
    "2024-06-04",
    "2024-01-02",
    "2024-01-03",
    "2024-03-01",
)


def _jsonable_attrs(df: pd.DataFrame) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, val in dict(getattr(df, "attrs", {}) or {}).items():
        if isinstance(val, (str, int, float, bool)) or val is None:
            out[key] = val
        else:
            out[key] = str(val)
    return out


def signals_to_payload(df: pd.DataFrame, trade_date: str) -> Dict[str, Any]:
    return {
        "trade_date": trade_date,
        "attrs": _jsonable_attrs(df),
        "records": df.to_dict(orient="records") if df is not None else [],
        "empty": bool(df is None or df.empty),
    }


def signals_from_payload(payload: Dict[str, Any]) -> pd.DataFrame:
    records = payload.get("records") or []
    df = pd.DataFrame(records)
    for key, val in (payload.get("attrs") or {}).items():
        df.attrs[key] = val
    return df


@pytest.fixture(autouse=True)
def _clear_strategy_caches():
    from zstock.strategy_management.pipeline import StrategyPipeline
    from zstock.strategy_management.risk_manager import _load_risk_limits_from_config
    from zstock.common.config import strategy_config

    StrategyPipeline._config_cache = None
    _load_risk_limits_from_config._cache = None
    strategy_config._clear_cache()
    yield
    StrategyPipeline._config_cache = None
    _load_risk_limits_from_config._cache = None
    strategy_config._clear_cache()


@pytest.fixture(scope="session")
def strategy_params() -> Dict[str, Any]:
    with open(STRATEGY_PARAMS_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def overnight_dir() -> Path:
    if not OVERNIGHT_DIR.is_dir():
        pytest.skip(f"缺少隔夜产物目录: {OVERNIGHT_DIR}")
    return OVERNIGHT_DIR


@pytest.fixture(scope="session")
def overnight_results(overnight_dir: Path) -> Dict[str, Any]:
    path = overnight_dir / "overnight_backtest_results.json"
    if not path.is_file():
        pytest.skip(f"缺少隔夜结果: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def overnight_checkpoint(overnight_dir: Path) -> Dict[str, Any]:
    path = overnight_dir / "checkpoint.json"
    if not path.is_file():
        pytest.skip(f"缺少 checkpoint: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def is_grid_2024_params(overnight_dir: Path) -> Dict[str, Any]:
    path = overnight_dir / "is_grid_2024" / "best_strategy_params.json"
    if not path.is_file():
        pytest.skip(f"缺少 2024 IS 最优参数: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def factor_eval_2024_csv(overnight_dir: Path) -> Path:
    path = overnight_dir / "factor_eval" / "2024" / "summary.csv"
    if not path.is_file():
        pytest.skip(f"缺少因子测评: {path}")
    return path


def _load_signals_bundle_from_disk() -> Optional[Dict[str, Any]]:
    if SIGNALS_FIXTURE.is_file():
        with open(SIGNALS_FIXTURE, encoding="utf-8") as f:
            return json.load(f)
    return None


def _try_fetch_signals_from_mongo() -> Optional[Dict[str, Any]]:
    """当场拉一天非空预计算信号；失败返回 None，不编造。"""
    import asyncio

    async def _inner() -> Optional[Dict[str, Any]]:
        from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
        from zstock.factor_management.pipeline import CrossSectionStrategyPipeline

        await init_zstock_database()
        try:
            pipe = CrossSectionStrategyPipeline()
            days: Dict[str, Any] = {}
            nonempty_date = None
            for td in CANDIDATE_SIGNAL_DATES:
                try:
                    df = await pipe.score_signals(td)
                except Exception:
                    continue
                days[td] = signals_to_payload(df, td)
                if df is not None and not df.empty and nonempty_date is None:
                    nonempty_date = td
            if not days:
                return None
            bundle = {"days": days, "primary_date": nonempty_date or next(iter(days))}
            FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
            SIGNALS_FIXTURE.write_text(
                json.dumps(bundle, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            return bundle
        finally:
            await close_zstock_database()

    try:
        return asyncio.run(_inner())
    except Exception:
        return None


@pytest.fixture(scope="session")
def signals_bundle() -> Dict[str, Any]:
    bundle = _load_signals_bundle_from_disk()
    if bundle is None:
        bundle = _try_fetch_signals_from_mongo()
    if bundle is None:
        pytest.skip("无真实 score_signals：Mongo 不可用且 test/fixtures 未导出")
    return bundle


def _pick_richest_date(signals_bundle: Dict[str, Any]) -> str:
    days = signals_bundle.get("days") or {}
    nonempty = [d for d, p in days.items() if (p.get("records") or [])]
    if not nonempty:
        pytest.skip("score_signals fixture 全部为空")
    return max(nonempty, key=lambda d: len(days[d].get("records") or []))


@pytest.fixture(scope="session")
def real_trade_date(signals_bundle: Dict[str, Any]) -> str:
    return _pick_richest_date(signals_bundle)


@pytest.fixture(scope="session")
def real_signals(signals_bundle: Dict[str, Any], real_trade_date: str) -> pd.DataFrame:
    df = signals_from_payload(signals_bundle["days"][real_trade_date])
    if df.empty:
        pytest.skip(f"{real_trade_date} 预计算信号为空，跳过持仓相关用例")
    return df


@pytest.fixture(scope="session")
def reversal_yellow_signals(signals_bundle: Dict[str, Any]) -> pd.DataFrame:
    for payload in (signals_bundle.get("days") or {}).values():
        attrs = payload.get("attrs") or {}
        if str(attrs.get("regime")) == "reversal" and str(attrs.get("market_grade")) == "yellow":
            df = signals_from_payload(payload)
            if not df.empty:
                return df
    pytest.skip("fixture 中没有 reversal+yellow 的非空截面")


@pytest.fixture(scope="session")
def green_signals(signals_bundle: Dict[str, Any]) -> pd.DataFrame:
    for payload in (signals_bundle.get("days") or {}).values():
        attrs = payload.get("attrs") or {}
        if str(attrs.get("market_grade")) == "green":
            df = signals_from_payload(payload)
            if not df.empty:
                return df
    pytest.skip("fixture 中没有 green 的非空截面")


def _load_ohlcv_bundle_from_disk() -> Optional[Dict[str, Any]]:
    if OHLCV_FIXTURE.is_file():
        with open(OHLCV_FIXTURE, encoding="utf-8") as f:
            return json.load(f)
    return None


def _try_fetch_ohlcv_from_mongo(codes: list[str], start: str, end: str) -> Optional[Dict[str, Any]]:
    import asyncio

    async def _inner() -> Optional[Dict[str, Any]]:
        from zstock.common.utils.common_utils import normalize_date
        from zstock.common.utils.db_utils import close_zstock_database, init_zstock_database
        from zstock.data_management.query_service import get_data_query_service

        await init_zstock_database()
        try:
            qs = get_data_query_service()
            batch = await qs.get_ohlcv_batch(codes, start, end)
            if not batch:
                return None
            payload: Dict[str, Any] = {"start": start, "end": end, "by_code": {}}
            for code, df in batch.items():
                if df is None or df.empty:
                    continue
                work = df.copy()
                if "trade_date" in work.columns:
                    work["trade_date"] = work["trade_date"].apply(normalize_date)
                keep = [c for c in ("trade_date", "open", "high", "low", "close", "volume") if c in work.columns]
                payload["by_code"][code] = work[keep].to_dict(orient="records")
            if not payload["by_code"]:
                return None
            FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
            OHLCV_FIXTURE.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            return payload
        finally:
            await close_zstock_database()

    try:
        return asyncio.run(_inner())
    except Exception:
        return None


class DummySignalGenerator:
    """不连 Mongo 的信号入口，供 StrategyPipeline / Backtester 离线构造。"""

    def __init__(self, df: Optional[pd.DataFrame] = None):
        self._df = df
        self.signals_history: Dict[str, pd.DataFrame] = {}

    async def generate_signals(self, **kwargs):
        if self._df is not None:
            return self._df
        from zstock.factor_management.pipeline import CrossSectionStrategyPipeline

        td = kwargs.get("trade_date") or "2024-01-02"
        return CrossSectionStrategyPipeline._empty_signals_df(str(td), "red", 0.0)


class ReplayFactorPipeline:
    """按导出的 score_signals 回放，不连 Mongo。"""

    def __init__(self, bundle: Dict[str, Any]):
        self.bundle = bundle

    async def preload_precomputed_factors(self, *args, **kwargs):
        return None

    async def score_signals(self, trade_date: str) -> pd.DataFrame:
        payload = (self.bundle.get("days") or {}).get(trade_date)
        if payload is None:
            raise ValueError(f"无预计算 M1 数据: {trade_date}")
        return signals_from_payload(payload)


def make_offline_pipeline(signals: Optional[pd.DataFrame] = None):
    from zstock.strategy_management.pipeline import StrategyPipeline

    return StrategyPipeline(signal_generator=DummySignalGenerator(signals))  # type: ignore[arg-type]


@pytest.fixture
def offline_pipeline():
    return make_offline_pipeline()


@pytest.fixture(scope="session")
def ohlcv_by_code(real_signals: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    disk = _load_ohlcv_bundle_from_disk()
    if disk is None:
        codes = list(real_signals["code"].astype(str).unique())
        disk = _try_fetch_ohlcv_from_mongo(codes, "2024-01-02", "2024-01-16")
    if disk is None:
        pytest.skip("无真实 OHLCV：Mongo 不可用且 test/fixtures 未导出")
    out: Dict[str, pd.DataFrame] = {}
    for code, rows in (disk.get("by_code") or {}).items():
        out[code] = pd.DataFrame(rows)
    if not out:
        pytest.skip("OHLCV fixture 为空")
    return out
