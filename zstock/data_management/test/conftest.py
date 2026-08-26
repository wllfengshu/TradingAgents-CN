"""数据层夹具：Mongo 导出的 399300 / 个股样本，以及内存 FakeMongo。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
FACTOR_HS300 = PROJECT_ROOT / "zstock" / "factor_management" / "test" / "fixtures" / "hs300_ohlcv.json"
STRATEGY_OHLCV = PROJECT_ROOT / "zstock" / "strategy_management" / "test" / "fixtures" / "ohlcv_bundle.json"


def _match(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, expected in (query or {}).items():
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$in" in expected:
                if actual not in expected["$in"]:
                    return False
            if "$gte" in expected and not (actual is not None and actual >= expected["$gte"]):
                return False
            if "$lte" in expected and not (actual is not None and actual <= expected["$lte"]):
                return False
            if "$regex" in expected:
                if actual is None or re.search(expected["$regex"], str(actual)) is None:
                    return False
            if "$eq" in expected and actual != expected["$eq"]:
                return False
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$type" in expected and expected["$type"] == "array" and not isinstance(actual, list):
                return False
        elif actual != expected:
            return False
    return True


class FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self.docs = list(docs)

    async def to_list(self, n):
        if n is None or n < 0:
            return list(self.docs)
        return list(self.docs[: int(n)])

    def sort(self, key_or_list, direction=1):
        if isinstance(key_or_list, str):
            pairs = [(key_or_list, direction)]
        else:
            pairs = list(key_or_list)
        for field, direc in reversed(pairs):
            self.docs.sort(key=lambda x: x.get(field) or "", reverse=direc < 0)
        return self

    def limit(self, n):
        self.docs = self.docs[: int(n)]
        return self

    def __aiter__(self):
        self._it = iter(self.docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None


class _AwaitValue:
    def __init__(self, value):
        self._value = value

    def __await__(self):
        async def _inner():
            return self._value
        return _inner().__await__()


class FakeCollection:
    def __init__(self, docs: Optional[List[Dict[str, Any]]] = None):
        self.docs = list(docs or [])

    def find(self, query=None, **kwargs):
        matched = [d for d in self.docs if _match(d, query or {})]
        sort = kwargs.get("sort")
        if sort:
            for field, direction in reversed(list(sort)):
                matched.sort(key=lambda x: x.get(field) or "", reverse=direction < 0)
        limit = kwargs.get("limit")
        if limit:
            matched = matched[: int(limit)]
        return FakeCursor(matched)

    async def find_one(self, query=None, **kwargs):
        docs = await self.find(query, **kwargs).to_list(1)
        return docs[0] if docs else None

    async def insert_one(self, document):
        self.docs.append(dict(document))
        return SimpleNamespace(inserted_id=f"id{len(self.docs)}")

    async def insert_many(self, documents, **kwargs):
        ids = []
        for d in documents:
            self.docs.append(dict(d))
            ids.append(f"id{len(self.docs)}")
        return SimpleNamespace(inserted_ids=ids)

    async def update_one(self, query, update, upsert=False, **kwargs):
        for d in self.docs:
            if _match(d, query or {}):
                patch = update.get("$set", update) if isinstance(update, dict) else update
                d.update(patch)
                return SimpleNamespace(modified_count=1, upserted_id=None)
        if upsert:
            patch = update.get("$set", update) if isinstance(update, dict) else update
            new_doc: Dict[str, Any] = {}
            for key, val in (query or {}).items():
                if not isinstance(val, dict):
                    new_doc[key] = val
            if isinstance(patch, dict):
                new_doc.update(patch)
            self.docs.append(new_doc)
            return SimpleNamespace(modified_count=0, upserted_id="up1")
        return SimpleNamespace(modified_count=0, upserted_id=None)

    async def update_many(self, query, update):
        n = 0
        patch = update.get("$set", update) if isinstance(update, dict) else update
        for d in self.docs:
            if _match(d, query or {}):
                d.update(patch)
                n += 1
        return SimpleNamespace(modified_count=n)

    async def replace_one(self, query, document, upsert=False):
        for i, d in enumerate(self.docs):
            if _match(d, query or {}):
                self.docs[i] = dict(document)
                return SimpleNamespace(modified_count=1, upserted_id=None)
        if upsert:
            self.docs.append(dict(document))
            return SimpleNamespace(modified_count=0, upserted_id="up1")
        return SimpleNamespace(modified_count=0, upserted_id=None)

    async def delete_one(self, query):
        for i, d in enumerate(self.docs):
            if _match(d, query or {}):
                self.docs.pop(i)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def delete_many(self, query):
        keep, n = [], 0
        for d in self.docs:
            if _match(d, query or {}):
                n += 1
            else:
                keep.append(d)
        self.docs = keep
        return SimpleNamespace(deleted_count=n)

    async def count_documents(self, query=None, **kwargs):
        return sum(1 for d in self.docs if _match(d, query or {}))

    async def drop(self):
        self.docs = []

    async def create_indexes(self, models):
        return [m.__class__.__name__ for m in models]

    async def drop_index(self, name):
        self.dropped_indexes = getattr(self, "dropped_indexes", [])
        self.dropped_indexes.append(name)
        return name

    def list_indexes(self):
        return [{"name": n} for n in getattr(self, "_index_names", ["_id_"])]

    def create_index(self, keys, **kwargs):
        name = kwargs.get("name") or "idx"
        names = getattr(self, "_index_names", ["_id_"])
        if name not in names:
            names.append(name)
        self._index_names = names
        return _AwaitValue(name)

    async def distinct(self, field, query=None):
        return list(
            {
                d.get(field)
                for d in self.docs
                if _match(d, query or {}) and d.get(field) is not None
            }
        )

    def aggregate(self, pipeline, **kwargs):
        docs = list(self.docs)
        for stage in pipeline or []:
            if "$match" in stage:
                docs = [d for d in docs if _match(d, stage["$match"])]
            if "$project" in stage:
                projected = []
                for d in docs:
                    out: Dict[str, Any] = {}
                    for key, expr in stage["$project"].items():
                        if key == "_id":
                            continue
                        if isinstance(expr, dict) and "$size" in expr:
                            field = str(expr["$size"]).lstrip("$")
                            out[key] = len(d.get(field) or [])
                        else:
                            out[key] = d.get(key if expr == 1 else key)
                    projected.append(out)
                docs = projected
            if "$group" in stage:
                spec = stage["$group"]
                id_spec = spec.get("_id")
                buckets: Dict[Any, List[Dict[str, Any]]] = {}
                if isinstance(id_spec, str) and id_spec.startswith("$"):
                    field = id_spec.lstrip("$")
                    for d in docs:
                        buckets.setdefault(d.get(field), []).append(d)
                else:
                    buckets[id_spec] = list(docs)
                grouped = []
                for gkey, gdocs in buckets.items():
                    acc: Dict[str, Any] = {"_id": gkey}
                    for key, expr in spec.items():
                        if key == "_id":
                            continue
                        if isinstance(expr, dict) and "$min" in expr:
                            field = str(expr["$min"]).lstrip("$")
                            vals = [d.get(field) for d in gdocs if d.get(field) is not None]
                            acc[key] = min(vals) if vals else None
                        if isinstance(expr, dict) and "$max" in expr:
                            field = str(expr["$max"]).lstrip("$")
                            vals = [d.get(field) for d in gdocs if d.get(field) is not None]
                            acc[key] = max(vals) if vals else None
                        if isinstance(expr, dict) and "$sum" in expr:
                            sval = expr["$sum"]
                            if sval == 1:
                                acc[key] = len(gdocs)
                            else:
                                field = str(sval).lstrip("$")
                                acc[key] = sum(float(d.get(field) or 0) for d in gdocs)
                    grouped.append(acc)
                docs = grouped
            if "$sort" in stage:
                for field, direction in reversed(list(stage["$sort"].items())):
                    docs.sort(
                        key=lambda x, f=field: x.get(f) if x.get(f) is not None else "",
                        reverse=direction < 0,
                    )
        return FakeCursor(docs)

    async def bulk_write(self, ops, ordered=False):
        for op in ops:
            filt = getattr(op, "_filter", None) or {}
            doc = getattr(op, "_doc", None) or {}
            if isinstance(doc, dict) and "$set" in doc:
                merged = {**filt, **(doc.get("$set") or {})}
                await self.replace_one(filt, merged, upsert=True)
            else:
                await self.replace_one(filt, doc, upsert=True)
        return SimpleNamespace(modified_count=len(ops), upserted_count=len(ops))


class FakeMongoDB:
    def __init__(self):
        self._cols: Dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        if name not in self._cols:
            self._cols[name] = FakeCollection()
        return self._cols[name]

    async def list_collection_names(self):
        return list(self._cols)

    async def drop(self):
        self._cols.clear()

    def __getattr__(self, name: str) -> FakeCollection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


class FakeDatabaseService:
    def __init__(self, store: Optional[Dict[str, List[Dict[str, Any]]]] = None):
        self.store = store or {}
        self._mongo: Optional[FakeMongoDB] = None

    def _docs(self, collection: str) -> List[Dict[str, Any]]:
        if self._mongo is not None:
            return self._mongo[collection].docs
        return self.store.setdefault(collection, [])

    async def query(self, collection, query=None, **kwargs):
        docs = [d for d in self._docs(collection) if _match(d, query or {})]
        sort = kwargs.get("sort")
        if sort:
            for field, direction in reversed(list(sort)):
                docs.sort(key=lambda x: x.get(field) or "", reverse=direction < 0)
        return docs

    async def query_one(self, collection, query=None, **kwargs):
        docs = await self.query(collection, query, **kwargs)
        return docs[0] if docs else None

    async def replace_one(self, collection, query, document, upsert=False):
        col = self.db[collection]
        result = await col.replace_one(query, document, upsert=upsert)
        self.store[collection] = col.docs
        return 1 if result.modified_count or result.upserted_id else 0

    @property
    def db(self):
        if self._mongo is None:
            self._mongo = FakeMongoDB()
            for name, docs in self.store.items():
                self._mongo._cols[name] = FakeCollection(docs)
        return self._mongo


@pytest.fixture(scope="session")
def hs300_rows() -> List[Dict[str, Any]]:
    path = FIXTURES / "hs300_ohlcv.json"
    if not path.is_file():
        path = FACTOR_HS300
    if not path.is_file():
        pytest.skip("缺少 399300 日线夹具")
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    rows = []
    for r in payload.get("rows") or []:
        doc = dict(r)
        doc["code"] = "399300"
        doc["period"] = "D"
        rows.append(doc)
    return rows


@pytest.fixture(scope="session")
def stock_ohlcv_docs() -> List[Dict[str, Any]]:
    if not STRATEGY_OHLCV.is_file():
        pytest.skip("缺少个股 OHLCV 夹具")
    with open(STRATEGY_OHLCV, encoding="utf-8") as f:
        payload = json.load(f)
    docs = []
    for code, rows in (payload.get("by_code") or {}).items():
        for r in rows:
            doc = dict(r)
            doc["code"] = code
            doc["period"] = "D"
            docs.append(doc)
    return docs


@pytest.fixture(scope="session")
def stock_info_sample() -> Dict[str, Any]:
    path = FIXTURES / "stock_info_sample.json"
    if not path.is_file():
        pytest.skip("缺少 stock_info 样本")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def sector_list_sample() -> Dict[str, Any]:
    path = FIXTURES / "sector_list_sample.json"
    if not path.is_file():
        pytest.skip("缺少板块列表样本")
    with open(path, encoding="utf-8") as f:
        return json.load(f)
