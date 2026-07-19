import os
import sys
import types
import asyncio

import pandas as pd
import pytest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tradingagents.dataflows.selector import force_indicators
from tradingagents.dataflows.selector import leader_indicators
from tradingagents.dataflows.selector import market_indicators
from tradingagents.dataflows.selector import risk_indicators
from tradingagents.dataflows.selector import sector_indicators
from app.core.database import init_database, close_database


@pytest.fixture(scope="module", autouse=True)
def init_and_close_database_for_tests():
    """为本测试模块初始化数据库连接（含 Redis），结束后关闭连接。"""
    asyncio.run(init_database())
    yield
    asyncio.run(close_database())


def test_force_indicators_normal_data(monkeypatch):
    fake_ak = types.SimpleNamespace(
        stock_fund_flow_industry=lambda symbol: pd.DataFrame(
            {
                "行业": ["AI算力", "汽车"],
                "净额": ["2.5亿", "1.2亿"],
            }
        ),
        stock_fund_flow_individual=lambda symbol: pd.DataFrame(
            {
                "股票代码": ["600519", "300750"],
                "股票简称": ["贵州茅台", "宁德时代"],
                "净额": ["8000万", "9000万"],
            }
        ),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)

    out = force_indicators.compute_force_indicators("2026-06-09", ["AI"])

    assert "行业资金流向（净流入前20）" in out
    assert "AI算力 ★：2.50 亿元" in out
    assert "600519 贵州茅台：0.80 亿元" in out
    assert "300750" not in out


def test_leader_indicators_normal_data(monkeypatch):
    fake_ak = types.SimpleNamespace(
        stock_zt_pool_em=lambda date: pd.DataFrame(
            {
                "代码": ["000001", "000002"],
                "名称": ["平安银行", "万科A"],
                "所属行业": ["银行", "地产"],
                "连板数": [3, 1],
                "成交额": [1_000_000, 2_000_000],
            }
        ),
        stock_zt_pool_strong_em=lambda date: pd.DataFrame(
            {
                "代码": ["000001", "600519"],
                "名称": ["平安银行", "贵州茅台"],
            }
        ),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)

    out = leader_indicators.compute_leader_indicators(
        "2026-06-09", [{"code": "000001"}, {"code": "600519"}]
    )

    assert "连板股列表（共 1 只）" in out
    assert "000001 平安银行（银行）：3 连板" in out
    assert "优质标的在强势股池中的情况" in out
    assert "✅ 600519 贵州茅台：已进入强势股池" in out


def test_market_indicators_normal_data(monkeypatch):
    def _index_df(base):
        return pd.DataFrame(
            {
                "date": pd.date_range("2026-06-02", periods=6, freq="D"),
                "close": [base, base + 1, base + 2, base + 3, base + 4, base + 5],
                "volume": [1000, 1200, 1100, 1300, 1400, 1500],
            }
        )

    fake_ak = types.SimpleNamespace(
        stock_zh_index_daily=lambda symbol: _index_df(
            3000 if symbol == "sh000001" else (10000 if symbol == "sz399001" else 2000)
        ),
        stock_hsgt_hist_em=lambda symbol: pd.DataFrame(
            {
                "日期": ["2026-06-09"],
                "当日成交净买额": [1_230_000_000],
            }
        ),
        stock_zh_a_spot=lambda: pd.DataFrame({"涨跌幅": [1.2, -0.6, 0.3, -1.1, 2.0]}),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)

    out = market_indicators.compute_market_indicators("2026-06-09")

    assert "上证指数：收盘" in out
    assert "深证成指：收盘" in out
    assert "创业板指：收盘" in out
    assert "北向资金：当日净买额 12.30 亿元，方向净流入" in out
    assert "涨跌统计：上涨 3 只，下跌 2 只，涨跌比 1.50" in out


def test_risk_indicators_normal_data(monkeypatch):
    out = risk_indicators.compute_risk_indicators(
        "2026-06-15", [{"code": "000001"}]
    )

    print(out)


def test_sector_indicators_normal_data(monkeypatch):
    fake_ak = types.SimpleNamespace(
        stock_board_industry_summary_ths=lambda: pd.DataFrame(
            {
                "板块名称": ["半导体", "算力", "汽车"],
                "涨跌幅": [3.2, 2.1, 1.0],
            }
        ),
        stock_zt_pool_em=lambda date: pd.DataFrame(
            {
                "代码": ["000001", "000002", "000003"],
                "连板数": [2, 1, 3],
                "所属行业": ["半导体", "半导体", "算力"],
                "封板资金": [10.0, 2.0, 9.0],
                "成交额": [8.0, 4.0, 6.0],
            }
        ),
        stock_zt_pool_strong_em=lambda date: pd.DataFrame(
            {
                "代码": ["000001", "000003", "000004"],
                "所属行业": ["半导体", "算力", "汽车"],
            }
        ),
        stock_zt_pool_dtgc_em=lambda date: pd.DataFrame({"代码": ["000010"]}),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)

    out = sector_indicators.compute_sector_indicators("2026-06-09")

    assert "1. 半导体：+3.20%" in out
    assert "涨停股总数：3 只" in out
    assert "强势股池总数：3 只" in out
    assert "平均封板比：1.08" in out
    assert "炸板率：25.0%" in out

