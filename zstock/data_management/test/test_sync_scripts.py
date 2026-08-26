"""同步 / 检查脚本的纯函数与参数解析，不跑全市场 xtquant。"""

import sys

from zstock.data_management.script.check_ohlcv_coverage import INDEX_CODES as CHECK_INDEXES
from zstock.data_management.script.sync_ohlcv import (
    INDEX_CODES as SYNC_INDEXES,
    _enrich_turnover_rate,
    _parse_args,
)
import pandas as pd


def test_index_codes_include_hs300():
    sync_codes = {c for c, _ in SYNC_INDEXES}
    check_codes = {c for c, _ in CHECK_INDEXES}
    assert "399300" in sync_codes
    assert "399300" in check_codes


def test_parse_args_defaults(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["sync_ohlcv.py"])
    args = _parse_args()
    assert args.start == "2025-01-01"
    assert args.end


def test_enrich_turnover_empty():
    assert _enrich_turnover_rate(pd.DataFrame(), ["603201"]).empty


def test_enrich_turnover_with_float_volume_column(stock_ohlcv_docs, monkeypatch):
    df = pd.DataFrame([d for d in stock_ohlcv_docs if d["code"] == "603201"][:5])
    assert not df.empty
    # 流通股本走日线自带列：用成交量列复制为 floatVolume 会得到 turnover=10000，
    # 那是编造分母。这里只验证无 floatVolume 且快照为空时保持 0。
    monkeypatch.setattr(
        "zstock.common.utils.xtquant_data_utils.fetch_float_shares_map",
        lambda codes: {},
    )
    out = _enrich_turnover_rate(df, ["603201"])
    assert "turnover_rate" in out.columns
    assert float(out["turnover_rate"].fillna(0).max()) == 0.0
