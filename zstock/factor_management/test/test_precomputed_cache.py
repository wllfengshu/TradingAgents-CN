"""预计算缓存：用导出文档走 preload 的内存索引路径。"""

import pytest

from zstock.factor_management.precomputed_cache import PrecomputedFactorCache


class _FakeDB:
    def __init__(self, factor_raw, names):
        self.factor_raw = factor_raw
        self.names = names

    async def query(self, collection, query=None, **kwargs):
        td = "2024-01-02"
        if "factor_market" in collection:
            return [self.factor_raw["market"]]
        if "factor_sector" in collection:
            seen = {}
            for d in list(self.factor_raw.get("sectors") or []) + list(self.names.get("sectors") or []):
                seen[d["sector_code"]] = d
            return list(seen.values())
        if "factor_dragon" in collection:
            return list(self.names.get("dragons") or []) + list(self.factor_raw.get("dragons") or [])
        if "factor_force" in collection:
            return list(self.names.get("forces") or []) + list(self.factor_raw.get("forces") or [])
        return []


class _FakeQS:
    def __init__(self, db):
        self.database_service = db


@pytest.mark.asyncio
async def test_preload_and_get(factor_raw, signal_names_raw):
    cache = PrecomputedFactorCache()
    assert cache.loaded is False
    qs = _FakeQS(_FakeDB(factor_raw, signal_names_raw))
    await cache.preload(qs, "2024-01-02", "2024-01-02")
    assert cache.loaded is True
    assert cache.date_range == ("2024-01-02", "2024-01-02")
    mkt = cache.get_factor_market("2024-01-02")
    assert mkt["index_code"] == "399300"
    secs = cache.get_factor_sectors("2024-01-02")
    assert secs
    codes = {d["code"] for d in cache.get_factor_dragons("2024-01-02")}
    assert "603201" in codes
    subset = cache.get_factor_dragons("2024-01-02", sector_codes=["SW2汽车零部件"])
    assert all(d["sector_code"] == "SW2汽车零部件" for d in subset)
    forces = cache.get_factor_forces("2024-01-02")
    assert any(d["code"] == "603201" for d in forces)
