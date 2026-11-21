"""common_utils：用真实夹具代码与 OHLCV 测规范化 / asof / 门禁。"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from zstock.common.utils.common_utils import (
    WAN_TO_YUAN,
    apply_blacklist_filter,
    determine_market,
    ensure_ohlcv_sorted,
    flow_docs_asof,
    get_index_code_from_ohlcv_dict,
    get_index_ohlcv_df,
    is_index_code,
    is_main_board,
    is_st,
    limit_up_threshold,
    minmax_normalize,
    normalize_code,
    normalize_date,
    ohlcv_asof,
    passes_gate_value,
    select_top_k,
    to_float_or_none,
    to_yyyymmdd,
)


def test_normalize_code_and_date_shapes():
    assert normalize_code("603201.SH") == "603201"
    assert normalize_code("sh600036") == "600036"
    assert normalize_code("SZ000060") == "000060"
    assert normalize_code("") == ""
    assert normalize_date("20240102") == "2024-01-02"
    assert normalize_date("2024/01/02") == "2024-01-02"
    assert normalize_date("2024-01-02") == "2024-01-02"
    assert to_yyyymmdd("2024-01-02") == "20240102"
    assert to_yyyymmdd("") == ""


def test_board_st_limit_and_market_on_real_codes(stock_info_sample):
    # stock_info_sample 当前导出偏北交所；主板规则用已知真实代码断言
    docs = (
        stock_info_sample.get("sample")
        if isinstance(stock_info_sample, dict)
        else stock_info_sample
    )
    bj_codes = {d["code"] for d in docs}
    assert bj_codes
    assert all(not is_main_board(c) for c in bj_codes)
    assert "920808" in bj_codes
    assert is_main_board("603201")
    assert is_main_board("000060")
    assert not is_main_board("300750")
    assert not is_main_board("688981")
    assert not is_main_board("830001")
    assert not is_main_board("")
    assert is_st("*ST华映")
    assert is_st("ST假股")
    assert not is_st("常润股份")
    assert limit_up_threshold("603201") == pytest.approx(0.10)
    assert limit_up_threshold("600000", name="ST测试") == pytest.approx(0.05)
    assert limit_up_threshold("300750", trade_date="2020-08-01") == pytest.approx(0.10)
    assert limit_up_threshold("300750", trade_date="2020-08-24") == pytest.approx(0.20)
    assert limit_up_threshold("688981") == pytest.approx(0.20)
    assert limit_up_threshold("830001") == pytest.approx(0.30)
    assert limit_up_threshold("920808") == pytest.approx(0.30)
    assert determine_market("603201") == "sh"
    assert determine_market("000060") == "sz"
    assert WAN_TO_YUAN == 10000.0


def test_is_index_code_ambiguous_vs_unambiguous():
    assert is_index_code("399300") is True
    assert is_index_code("399001") is True
    assert is_index_code("000001") is False
    assert is_index_code("SH000001") is True
    assert is_index_code("000001.SH") is True
    assert is_index_code("000001.SZ") is False
    assert is_index_code("603201") is False
    assert is_index_code(None) is False
    assert is_index_code("") is False


def test_ohlcv_asof_on_hs300_and_stock(hs300_ohlcv, stock_ohlcv_by_code):
    assert "603201" in stock_ohlcv_by_code
    stock = stock_ohlcv_by_code["603201"]
    asof = ohlcv_asof(stock, "2024-01-02", require_exact=True)
    assert asof is not None
    assert normalize_date(str(asof["trade_date"].iloc[-1])) == "2024-01-02"

    missing = ohlcv_asof(stock, "2099-01-01", require_exact=True)
    assert missing is None

    before = ohlcv_asof(stock, "2010-01-01", require_exact=True)
    assert before is None

    assert ohlcv_asof(None, "2024-01-02") is None
    assert ohlcv_asof(pd.DataFrame(), "2024-01-02") is None

    unsorted = hs300_ohlcv.iloc[::-1].reset_index(drop=True)
    sorted_df = ensure_ohlcv_sorted(unsorted)
    assert sorted_df["trade_date"].is_monotonic_increasing

    no_date = ensure_ohlcv_sorted(pd.DataFrame({"close": [1.0]}))
    assert list(no_date.columns) == ["close"]


def test_flow_docs_asof_real_dates():
    docs = [
        {"code": "000060", "trade_date": "2024-01-02", "main_net": 1.0},
        {"code": "000060", "trade_date": "2024-01-03", "main_net": 2.0},
        {"code": "000060", "trade_date": "2024-01-04", "main_net": 3.0},
    ]
    exact = flow_docs_asof(docs, "2024-01-03", require_exact=True)
    assert len(exact) == 2
    assert exact[-1]["trade_date"] == "2024-01-03"
    assert flow_docs_asof(docs, "2024-01-05", require_exact=True) == []
    loose = flow_docs_asof(docs, "2024-01-05", require_exact=False)
    assert len(loose) == 3
    assert flow_docs_asof([], "2024-01-02") == []
    assert flow_docs_asof(docs, None) == docs
    no_td = [{"code": "000060", "main_net": 1.0}]
    assert flow_docs_asof(no_td, "2024-01-02") == no_td


def test_minmax_select_blacklist_and_index_helpers(stock_ohlcv_by_code):
    scores = {"603201": 80.0, "000060": 60.0, "601107": 90.0}
    top = select_top_k(scores, 2)
    assert [c for c, _ in top] == ["601107", "603201"]
    assert select_top_k({}, 3) == []
    filtered = apply_blacklist_filter(scores, {"000060"})
    assert "000060" not in filtered
    assert apply_blacklist_filter(scores, set()) is scores

    vals = {"a": 1.0, "b": 3.0, "c": float("nan"), "d": None}
    norm = minmax_normalize(vals)
    assert norm["a"] == pytest.approx(0.0)
    assert norm["b"] == pytest.approx(100.0)
    assert "c" not in norm
    assert minmax_normalize({"x": 1.0, "y": 1.0}) == {"x": 50.0, "y": 50.0}
    assert minmax_normalize({}) == {}

    idx = {"399300": stock_ohlcv_by_code["603201"]}
    assert get_index_code_from_ohlcv_dict(idx) == "399300"
    assert get_index_code_from_ohlcv_dict({}) == ""
    assert get_index_ohlcv_df(idx) is not None
    assert get_index_ohlcv_df({}) is None


def test_to_float_and_passes_gate_matrix():
    assert to_float_or_none("1.5") == pytest.approx(1.5)
    assert to_float_or_none(None) is None
    assert to_float_or_none("x") is None
    assert to_float_or_none(float("nan")) is None
    assert passes_gate_value(1.0, {"op": "finite"}) is True
    assert passes_gate_value(None, {"op": "finite"}) is False
    assert passes_gate_value(1.0, {"op": "gt", "value": 0}) is True
    assert passes_gate_value(0.0, {"op": ">", "value": 0}) is False
    assert passes_gate_value(1.0, {"op": "gte", "value": 1}) is True
    assert passes_gate_value(1.0, {"op": "eq", "value": 1}) is True
    assert passes_gate_value(1.0, {"op": "unknown"}) is True
