"""策略与回测结果的 Mongo 存取（Web 控制台）。"""
from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId

from zstock.common.config.strategy_config import load_strategy_params

logger = logging.getLogger(__name__)

COL_STRATEGIES = "zstock_strategies"
COL_BACKTESTS = "zstock_backtests"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _oid(value: str) -> ObjectId:
    return ObjectId(str(value))


def _serialize(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not doc:
        return None
    out = dict(doc)
    if "_id" in out:
        out["id"] = str(out.pop("_id"))
    for key in ("created_at", "updated_at", "finished_at"):
        val = out.get(key)
        if isinstance(val, datetime):
            out[key] = val.isoformat()
    return out


async def ensure_strategy_indexes(db) -> None:
    await db[COL_STRATEGIES].create_index([("updated_at", -1)])
    await db[COL_STRATEGIES].create_index([("name", 1)])
    await db[COL_BACKTESTS].create_index([("strategy_id", 1), ("created_at", -1)])


def default_template_params() -> Dict[str, Any]:
    """磁盘 strategy_params.json 深拷贝，仅作新建模板。"""
    return copy.deepcopy(load_strategy_params() or {})


async def create_strategy(
    db,
    *,
    name: str,
    description: str = "",
    active_factors: Optional[Dict[str, Any]] = None,
    backtest: Optional[Dict[str, Any]] = None,
    params_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    params = params_override if params_override is not None else default_template_params()
    params = copy.deepcopy(params)
    params["strategy_name"] = name
    if description:
        params["description"] = description
    if active_factors is not None:
        # 保留 profiles（若模板有），前端可只传三层
        merged = copy.deepcopy(params.get("active_factors") or {})
        for layer in ("sector", "dragon", "force"):
            if layer in active_factors:
                merged[layer] = active_factors[layer]
        if "profiles" in active_factors:
            merged["profiles"] = active_factors["profiles"]
        params["active_factors"] = merged
    if backtest:
        bt = copy.deepcopy(params.get("backtest") or {})
        bt.update(backtest)
        params["backtest"] = bt

    now = _now()
    doc = {
        "name": name,
        "description": description or params.get("description", ""),
        "params": params,
        "created_at": now,
        "updated_at": now,
    }
    result = await db[COL_STRATEGIES].insert_one(doc)
    doc["_id"] = result.inserted_id
    return _serialize(doc)  # type: ignore[return-value]


async def list_strategies(db, *, limit: int = 100) -> List[Dict[str, Any]]:
    cursor = db[COL_STRATEGIES].find({}).sort("updated_at", -1).limit(limit)
    rows = []
    async for doc in cursor:
        item = _serialize(doc)
        if item:
            # 列表不塞完整 params，只摘要
            params = item.pop("params", {}) or {}
            item["strategy_name"] = params.get("strategy_name") or item.get("name")
            item["version"] = params.get("version")
            af = params.get("active_factors") or {}
            item["factor_summary"] = {
                layer: {
                    regime: len(entries or [])
                    for regime, entries in (af.get(layer) or {}).items()
                    if isinstance(entries, list)
                }
                for layer in ("sector", "dragon", "force")
                if isinstance(af.get(layer), dict)
            }
            rows.append(item)
    return rows


async def get_strategy(db, strategy_id: str) -> Optional[Dict[str, Any]]:
    doc = await db[COL_STRATEGIES].find_one({"_id": _oid(strategy_id)})
    return _serialize(doc)


async def update_strategy(
    db,
    strategy_id: str,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    active_factors: Optional[Dict[str, Any]] = None,
    backtest: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    doc = await db[COL_STRATEGIES].find_one({"_id": _oid(strategy_id)})
    if not doc:
        return None
    body = copy.deepcopy(doc.get("params") or {})
    if params is not None:
        body = copy.deepcopy(params)
    if name is not None:
        doc["name"] = name
        body["strategy_name"] = name
    if description is not None:
        doc["description"] = description
        body["description"] = description
    if active_factors is not None:
        merged = copy.deepcopy(body.get("active_factors") or {})
        for layer in ("sector", "dragon", "force", "profiles"):
            if layer in active_factors:
                merged[layer] = active_factors[layer]
        body["active_factors"] = merged
    if backtest is not None:
        bt = copy.deepcopy(body.get("backtest") or {})
        bt.update(backtest)
        body["backtest"] = bt
    doc["params"] = body
    doc["updated_at"] = _now()
    await db[COL_STRATEGIES].replace_one({"_id": doc["_id"]}, doc)
    return _serialize(doc)


async def delete_strategy(db, strategy_id: str) -> bool:
    result = await db[COL_STRATEGIES].delete_one({"_id": _oid(strategy_id)})
    return int(result.deleted_count or 0) > 0


async def create_backtest_record(
    db,
    *,
    strategy_id: str,
    status: str = "running",
    start_date: str = "",
    end_date: str = "",
    job_id: str = "",
) -> Dict[str, Any]:
    now = _now()
    doc = {
        "strategy_id": strategy_id,
        "job_id": job_id,
        "status": status,
        "start_date": start_date,
        "end_date": end_date,
        "metrics": {},
        "equity": [],
        "error": None,
        "created_at": now,
        "finished_at": None,
    }
    result = await db[COL_BACKTESTS].insert_one(doc)
    doc["_id"] = result.inserted_id
    return _serialize(doc)  # type: ignore[return-value]


async def finish_backtest_record(
    db,
    backtest_id: str,
    *,
    status: str,
    metrics: Optional[Dict[str, Any]] = None,
    equity: Optional[List[Dict[str, Any]]] = None,
    error: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    update = {
        "status": status,
        "finished_at": _now(),
        "metrics": metrics or {},
        "equity": equity or [],
        "error": error,
    }
    await db[COL_BACKTESTS].update_one({"_id": _oid(backtest_id)}, {"$set": update})
    doc = await db[COL_BACKTESTS].find_one({"_id": _oid(backtest_id)})
    return _serialize(doc)


async def get_backtest(db, backtest_id: str) -> Optional[Dict[str, Any]]:
    doc = await db[COL_BACKTESTS].find_one({"_id": _oid(backtest_id)})
    return _serialize(doc)


async def list_backtests(
    db,
    *,
    strategy_id: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    query: Dict[str, Any] = {}
    if strategy_id:
        query["strategy_id"] = strategy_id
    cursor = db[COL_BACKTESTS].find(query).sort("created_at", -1).limit(limit)
    rows = []
    async for doc in cursor:
        item = _serialize(doc)
        if item:
            # 列表省略长 equity
            item.pop("equity", None)
            rows.append(item)
    return rows
