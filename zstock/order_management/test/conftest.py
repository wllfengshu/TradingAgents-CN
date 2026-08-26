"""订单层夹具：目标持仓与价格来自策略层导出的真实截面 / OHLCV。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SIGNAL_FIXTURE = PROJECT_ROOT / "zstock" / "strategy_management" / "test" / "fixtures" / "score_signals_bundle.json"
OHLCV_FIXTURE = PROJECT_ROOT / "zstock" / "strategy_management" / "test" / "fixtures" / "ohlcv_bundle.json"
PARAMS_PATH = PROJECT_ROOT / "zstock" / "common" / "config" / "strategy_params.json"


@pytest.fixture(scope="session")
def strategy_params() -> Dict[str, Any]:
    with open(PARAMS_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def jan_signals() -> pd.DataFrame:
    if not SIGNAL_FIXTURE.is_file():
        pytest.skip("缺少策略层真实截面夹具")
    with open(SIGNAL_FIXTURE, encoding="utf-8") as f:
        bundle = json.load(f)
    recs = (bundle.get("days") or {}).get("2024-01-02", {}).get("records") or []
    df = pd.DataFrame(recs)
    if df.empty:
        pytest.skip("2024-01-02 截面为空")
    return df


@pytest.fixture(scope="session")
def ohlcv_by_code() -> Dict[str, pd.DataFrame]:
    if not OHLCV_FIXTURE.is_file():
        pytest.skip("缺少策略层 OHLCV 夹具")
    with open(OHLCV_FIXTURE, encoding="utf-8") as f:
        payload = json.load(f)
    return {code: pd.DataFrame(rows) for code, rows in (payload.get("by_code") or {}).items()}


@pytest.fixture(scope="session")
def real_price_map(jan_signals, ohlcv_by_code) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for code in jan_signals["code"].astype(str):
        df = ohlcv_by_code.get(code)
        if df is None or df.empty:
            continue
        day = df[df["trade_date"] == "2024-01-02"]
        row = day.iloc[0] if not day.empty else df.iloc[0]
        px = float(row["close"])
        if px > 0:
            out[code] = px
    if not out:
        pytest.skip("无法从真实 OHLCV 得到 2024-01-02 价格")
    return out


class FakeDB:
    """注入订单落库，避免 UT 写真实 Mongo。"""

    def __init__(self):
        self.store: Dict[str, Dict[str, Any]] = {}
        self.inserted: List[Dict[str, Any]] = []

    async def insert_many(self, collection, documents):
        self.inserted.extend(documents)
        for d in documents:
            if d.get("order_id"):
                self.store[d["order_id"]] = dict(d)
        return [str(i) for i in range(len(documents))]

    async def insert_one(self, collection, document):
        self.inserted.append(document)
        if document.get("order_id"):
            self.store[document["order_id"]] = dict(document)
        return "fake-oid"

    async def query_one(self, collection, query=None, **kwargs):
        query = query or {}
        oid = query.get("order_id")
        if oid:
            return self.store.get(oid)
        for doc in self.store.values():
            if all(doc.get(k) == v for k, v in query.items()):
                return doc
        return None

    async def query(self, collection, query=None, **kwargs):
        query = query or {}
        docs = list(self.store.values())
        if not query:
            return docs
        return [d for d in docs if all(d.get(k) == v for k, v in query.items())]

    async def update_one(self, collection, query, update):
        oid = (query or {}).get("order_id")
        if not oid or oid not in self.store:
            return 0
        patch = update.get("$set") if isinstance(update, dict) and "$set" in update else update
        self.store[oid].update(patch or {})
        return 1


class FakeQMT:
    """纸面 QMT：账户资金用策略初始资金，行情用真实收盘价，持仓可注入。"""

    def __init__(self, price_map: Dict[str, float], positions: Optional[List] = None, capital: float = 10_000_000):
        self._connected = True
        self.price_map = price_map
        self._positions = positions or []
        self.capital = capital
        self.submitted: List[Dict[str, Any]] = []

    def connect(self) -> bool:
        self._connected = True
        return True

    def get_account_info(self):
        return SimpleNamespace(cash=self.capital, total_value=self.capital, frozen_cash=0.0)

    def get_positions(self):
        return self._positions

    def get_realtime_quote(self, codes: List[str]) -> Dict[str, Dict]:
        from zstock.common.utils.xtquant_data_utils import to_xt_code
        from zstock.common.utils.common_utils import normalize_code

        out = {}
        for c in codes:
            pure = normalize_code(c)
            px = self.price_map.get(pure) or self.price_map.get(c)
            if px:
                out[c] = {"lastPrice": float(px)}
                out[to_xt_code(pure)] = {"lastPrice": float(px)}
        return out

    def buy(self, code, amount, price=None, remark="", volume=None):
        self.submitted.append({"side": "buy", "code": code, "volume": volume, "amount": amount})
        return 10001

    def sell(self, code, volume, price=None, remark=""):
        self.submitted.append({"side": "sell", "code": code, "volume": volume})
        return 10002

    def cancel_order(self, xt_order_id):
        return True


def make_position(code: str, volume: int, price: float):
    return SimpleNamespace(
        code=code,
        volume=int(volume),
        cost_price=float(price),
        current_price=float(price),
    )


@pytest.fixture
def fake_db() -> FakeDB:
    return FakeDB()


@pytest.fixture(scope="session")
def target_holdings(jan_signals, strategy_params) -> pd.DataFrame:
    from zstock.strategy_management.portfolio_optimizer import PortfolioOptimizer

    cap = float(strategy_params["portfolio"]["max_weight_per_stock"])
    top_k = int(strategy_params["final_score"]["top_k"])
    out = PortfolioOptimizer().optimize_portfolio(
        jan_signals,
        min_holdings=1,
        max_holdings=top_k,
        max_weight_per_stock=cap,
    )
    if out.get("status") != "success":
        pytest.skip(f"真实截面无法优化持仓: {out}")
    hdf = out["holdings_df"]
    if hdf is None or hdf.empty:
        pytest.skip("优化结果为空")
    return hdf


@pytest.fixture(scope="session")
def initial_capital(strategy_params) -> float:
    return float(strategy_params["backtest"]["initial_capital"])
