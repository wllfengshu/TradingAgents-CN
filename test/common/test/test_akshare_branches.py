"""akshare 分支补齐：THS 降级、行业板块、涨停阈值兜底、成分提取失败。"""

from __future__ import annotations

import pandas as pd
import pytest

from zstock.common.utils import akshare_utils as aku


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(aku, "_ak", None)
    aku._spot_cache["data"] = None
    aku._spot_cache["ts"] = 0.0
    monkeypatch.setattr(aku._time, "sleep", lambda *_: None)


def _ok(func, *a, retries=2, delay=0, **k):
    return func(*a, **k)


def test_sector_list_industry_and_ths_fallback(monkeypatch):
    class FakeAk:
        def stock_board_concept_name_em(self):
            raise RuntimeError("em concept down")

        def stock_board_concept_name_ths(self):
            return pd.DataFrame([{"name": "人工智能"}])

        def stock_board_industry_name_em(self):
            raise RuntimeError("em industry down")

        def stock_board_industry_name_ths(self):
            return pd.DataFrame([{"name": "汽车零部件"}])

    monkeypatch.setattr(aku, "_get_ak", lambda: FakeAk())
    monkeypatch.setattr(aku, "_em_call", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("em")))
    concepts = aku.fetch_sector_list("concept")
    assert any(x["sector_name"] == "人工智能" for x in concepts)
    industries = aku.fetch_sector_list("industry")
    assert any(x["sector_name"] == "汽车零部件" for x in industries)
    both = aku.fetch_sector_list("all")
    assert len(both) >= 2


def test_sector_list_em_success_industry(monkeypatch):
    class FakeAk:
        def stock_board_concept_name_em(self):
            return pd.DataFrame([{"板块名称": "贵金属", "板块代码": "BK1184"}])

        def stock_board_industry_name_em(self):
            return pd.DataFrame([{"板块名称": "汽车零部件", "板块代码": "BK0481"}])

    monkeypatch.setattr(aku, "_get_ak", lambda: FakeAk())
    monkeypatch.setattr(aku, "_em_call", _ok)
    out = aku.fetch_sector_list("all")
    types = {x["sector_type"] for x in out}
    assert "concept" in types and "industry" in types


def test_sector_stocks_industry_and_fallbacks(monkeypatch):
    class FakeAk:
        def stock_board_industry_cons_em(self, symbol):
            return pd.DataFrame({"代码": ["603201"]})

        def stock_board_concept_cons_em(self, symbol):
            return pd.DataFrame()

        def stock_board_concept_info_ths(self, symbol):
            raise RuntimeError("ths down")

        def stock_board_industry_info_ths(self, symbol):
            return pd.DataFrame({"code": ["000060"]})

    monkeypatch.setattr(aku, "_get_ak", lambda: FakeAk())
    monkeypatch.setattr(aku, "_em_call", _ok)
    assert "603201" in aku.fetch_sector_stocks(
        "BK0481", sector_name="汽车零部件", sector_type="industry"
    )
    assert "000060" in aku.fetch_sector_stocks("汽车零部件", sector_type="industry")
    # 无列可提取
    class EmptyCols:
        def stock_board_concept_cons_em(self, symbol):
            return pd.DataFrame({"名称": ["x"]})

        def stock_board_industry_cons_em(self, symbol):
            return pd.DataFrame({"名称": ["x"]})

        def stock_board_concept_info_ths(self, symbol):
            return pd.DataFrame({"名称": ["x"]})

        def stock_board_industry_info_ths(self, symbol):
            return pd.DataFrame({"名称": ["x"]})

    monkeypatch.setattr(aku, "_get_ak", lambda: EmptyCols())
    assert aku.fetch_sector_stocks("BK1184", sector_name="贵金属", sector_type="concept") == []
    assert aku.fetch_sector_stocks("不存在板块") == []


def test_trade_status_threshold_fallback(monkeypatch):
    spot = pd.DataFrame(
        [
            {
                "代码": "603201",
                "名称": "常润股份",
                "最新价": 11.0,
                "昨收": 10.0,
                "涨停价": 0,
                "跌停价": 0,
                "成交量": 100,
                "成交额": 1e5,
            }
        ]
    )

    class FakeAk:
        def stock_individual_info_em(self, symbol):
            return pd.DataFrame(
                [
                    {"item": "股票简称", "value": "常润股份"},
                    {"item": "上市时间", "value": "bad"},
                ]
            )

        def stock_zh_a_spot_em(self):
            return spot

    monkeypatch.setattr(aku, "_get_ak", lambda: FakeAk())
    st = aku.fetch_trade_status("603201", "2024-01-02")
    assert st["up_stop_price"] > 0
    # 10% 涨幅触主板阈值 9.5%
    assert st["is_limit_up"] is True

    batch = aku.fetch_trade_status_batch(["603201"], "2024-01-02")
    assert batch["603201"]["is_limit_up"] is True


def test_fetch_ohlcv_sh_prefix_index(hs300_ohlcv, monkeypatch):
    df = hs300_ohlcv.copy().rename(columns={"trade_date": "date"})
    if "amount" not in df.columns:
        df["amount"] = 0

    class FakeAk:
        def stock_zh_index_daily(self, symbol):
            assert symbol.startswith("sh")
            return df[["date", "open", "high", "low", "close", "volume"]].assign(
                amount=0
            )

    monkeypatch.setattr(aku, "_get_ak", lambda: FakeAk())
    out = aku.fetch_ohlcv("SH000300", "2024-01-02", "2024-01-10")
    assert not out.empty
    assert (out["code"] == "000300").all()


def test_build_name_cache_and_em_call_exhausted(monkeypatch):
    class FakeAk:
        def stock_zh_a_spot_em(self):
            raise RuntimeError("spot fail")

    monkeypatch.setattr(aku, "_get_ak", lambda: FakeAk())
    assert aku._build_name_cache(FakeAk()) == {}

    def always_fail():
        raise RuntimeError("always")

    with pytest.raises(RuntimeError):
        aku._em_call(always_fail, retries=2, delay=0)


def test_spot_double_check_locked(monkeypatch):
    calls = {"n": 0}

    class FakeAk:
        def stock_zh_a_spot_em(self):
            calls["n"] += 1
            return pd.DataFrame({"代码": ["000060"], "名称": ["中金岭南"]})

    ak = FakeAk()
    # 预填过期缓存
    aku._spot_cache["data"] = pd.DataFrame({"代码": ["x"]})
    aku._spot_cache["ts"] = 0.0
    monkeypatch.setattr(aku._time, "time", lambda: 10_000.0)
    out = aku._get_spot_snapshot(ak)
    assert "000060" in set(out["代码"].astype(str))
    assert calls["n"] == 1
