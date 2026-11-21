"""PreFilters：生产黑名单 JSON + 真实个股截面 / OHLCV。"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from zstock.common.prefilters import PreFilters

CONFIG_DIR = Path(__file__).resolve().parents[3] / "zstock" / "common" / "config"


def test_loads_production_blacklist_json():
    pf = PreFilters(config_dir=CONFIG_DIR)
    sector_path = CONFIG_DIR / "blacklist_sector.json"
    with open(sector_path, encoding="utf-8") as f:
        expected = set(json.load(f).get("sectors") or [])
    assert pf.sector_blacklist == expected
    pf.reload_blacklists()
    assert pf.sector_blacklist == expected


def test_stock_blacklist_until_expiry(tmp_path):
    (tmp_path / "blacklist_sector.json").write_text(
        json.dumps({"sectors": ["*房地产*"]}), encoding="utf-8"
    )
    (tmp_path / "blacklist_stock.json").write_text(
        json.dumps(
            {
                "stocks": [
                    {"code": "000981.SZ", "until": "2099-01-01"},
                    {"code": "600001.SH", "until": "2000-01-01"},
                    "601107",
                    {"reason": "缺 code"},
                ]
            }
        ),
        encoding="utf-8",
    )
    pf = PreFilters(config_dir=tmp_path)
    stocks = ["603201", "000981", "600001", "601107"]
    out = pf.apply_blacklist_filters(stocks)
    kept = set(out["stocks"])
    assert "603201" in kept
    assert "000981" not in kept
    assert "600001" in kept  # until 已过期
    assert "601107" not in kept


def test_sector_glob_from_production_patterns():
    pf = PreFilters(config_dir=CONFIG_DIR)
    sectors = [
        {"sector_code": "SW2汽车零部件", "sector_name": "SW2汽车零部件"},
        {"sector_code": "SW2房地产", "sector_name": "SW2房地产开发"},
        {"sector_code": "SW2出版", "sector_name": "SW2出版"},
    ]
    out = pf.apply_blacklist_filters(["603201"], sectors=sectors)
    names = {s["sector_name"] for s in out["sectors"]}
    assert "SW2汽车零部件" in names
    assert "SW2出版" in names
    # 生产 blacklist 含 *房地产*
    if any("房地产" in p for p in pf.sector_blacklist):
        assert "SW2房地产开发" not in names


def test_mainboard_and_bollinger_on_real_data(signal_names_raw, stock_ohlcv_by_code):
    pf = PreFilters(config_dir=CONFIG_DIR)
    infos = signal_names_raw["stock_infos"]
    codes = [c for c, info in infos.items() if isinstance(info, dict)]
    kept = pf.apply_technical_filters(codes, {}, infos, apply_main_board=True, apply_bollinger=False)
    for code in kept:
        info = infos[code]
        assert info.get("is_mainboard") is True
        assert not info.get("is_st")

    code = "603201"
    assert code in stock_ohlcv_by_code
    df = stock_ohlcv_by_code[code]
    boll = pf.apply_bollinger_filter({code: df}, slope_threshold=-1.0)
    assert isinstance(boll, dict)

    bands = PreFilters.calculate_bollinger_bands(df[["close"]].astype(float))
    assert not bands.empty
    last = bands.dropna().iloc[-1]
    assert last["upper"] >= last["mid"] >= last["lower"]

    short = pf.apply_bollinger_filter({code: df.head(10)}, slope_threshold=0.0)
    # 不足窗口应被滤掉
    assert code not in short


def test_filter_universe_combines(signal_names_raw, stock_ohlcv_by_code):
    pf = PreFilters(config_dir=CONFIG_DIR)
    infos = signal_names_raw["stock_infos"]
    codes = [c for c, info in infos.items() if isinstance(info, dict)][:10]
    stock_data = {c: stock_ohlcv_by_code[c] for c in codes if c in stock_ohlcv_by_code}
    out = pf.filter_universe(
        codes,
        stock_infos=infos,
        stock_data=stock_data,
        apply_main_board=True,
        apply_bollinger=False,
    )
    assert "stocks" in out
    assert isinstance(out["stocks"], list)

    # 默认 config_dir + 布林开关
    pf2 = PreFilters()
    boll = pf2.apply_technical_filters(
        ["603201"],
        {"603201": stock_ohlcv_by_code["603201"]},
        stock_infos=infos,
        apply_main_board=False,
        apply_bollinger=True,
        bollinger_slope_threshold=-10.0,
    )
    assert isinstance(boll, list)
    assert PreFilters.calculate_bollinger_bands(pd.DataFrame({"close": [1, 2, 3]})).empty
    assert PreFilters.calculate_bollinger_slope(pd.DataFrame({"mid": [1.0]})).iloc[0] == 0
    assert PreFilters.check_bollinger_condition(pd.DataFrame({"close": [1.0] * 10})) is False
