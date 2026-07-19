import os
import sys
import types

import pandas as pd
import pytest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tradingagents.dataflows.selector import sector_indicators


@pytest.fixture(autouse=True)
def clear_api_cache_state():
    sector_indicators.api_cache._local_cache.clear()
    sector_indicators.api_cache._stats["hits"] = 0
    sector_indicators.api_cache._stats["misses"] = 0
    yield
    sector_indicators.api_cache._local_cache.clear()


def test_sector_rank_uses_real_sector_name(monkeypatch):
    df = pd.DataFrame(
        {
            "板块名称": ["半导体", "算力", "汽车"],
            "涨跌幅": [3.21, 2.12, 1.01],
        }
    )

    fake_ak = types.SimpleNamespace(
        stock_board_industry_summary_ths=lambda: df,
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)

    out = sector_indicators._sector_rank_simple("20240524")

    assert "1. 半导体：+3.21%" in out
    assert "2. 算力：+2.12%" in out
    assert "1：+" not in out


def test_broken_rate_fallbacks_to_latest_when_old_date_window_error(monkeypatch):
    calls = []

    def fake_dtgc(date):
        calls.append(date)
        if date == "20240524":
            raise Exception("跌停股池只能获取最近 30 个交易日的数据")
        return pd.DataFrame({"代码": ["000001", "000002"]})

    fake_ak = types.SimpleNamespace(stock_zt_pool_dtgc_em=fake_dtgc)
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)
    monkeypatch.setattr(sector_indicators, "_latest_date_fmt", lambda: "20260607")

    out = sector_indicators._broken_rate_simple("20240524", zt_count=8)

    assert calls == ["20240524", "20260607"]
    assert "回退" not in out  # 报告中仅保留注释提示
    assert "注：目标日期 20240524 超出AKShare可查窗口" in out
    assert "炸板家数：2 只" in out

