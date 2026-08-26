"""预过滤：黑名单来自正式 JSON，标的来自真实截面。"""

from zstock.factor_management.prefilters import PreFilters


def test_stock_blacklist_hits_real_codes():
    pf = PreFilters()
    stocks = ["603201", "000060", "000981", "000981.SZ", "600001", "600001.SH"]
    out = pf.apply_blacklist_filters(stocks)
    kept = set(out["stocks"])
    assert "603201" in kept
    assert "000060" in kept
    assert "000981" not in kept
    assert "000981.SZ" not in kept
    assert "600001" not in kept
    assert "600001.SH" not in kept


def test_sector_glob_blacklist():
    pf = PreFilters()
    sectors = [
        {"sector_code": "SW2汽车零部件", "sector_name": "SW2汽车零部件"},
        {"sector_code": "SW2房地产", "sector_name": "SW2房地产开发"},
        {"sector_code": "SW2光伏", "sector_name": "SW2光伏设备"},
        {"sector_code": "SW2出版", "sector_name": "SW2出版"},
    ]
    out = pf.apply_blacklist_filters(["603201"], sectors=sectors)
    names = {s["sector_name"] for s in out["sectors"]}
    assert "SW2汽车零部件" in names
    assert "SW2出版" in names
    assert "SW2房地产开发" not in names
    assert "SW2光伏设备" not in names


def test_mainboard_filter_uses_real_stock_info(signal_names_raw):
    pf = PreFilters()
    infos = signal_names_raw["stock_infos"]
    codes = [c for c, info in infos.items() if isinstance(info, dict)]
    kept = pf.apply_technical_filters(codes, {}, infos, apply_main_board=True, apply_bollinger=False)
    for code in kept:
        info = infos[code]
        assert info.get("is_mainboard") is True
        assert not info.get("is_st")


def test_bollinger_filter_on_real_ohlcv(stock_ohlcv_by_code):
    pf = PreFilters()
    codes = [c for c in ("603201", "000060", "601107") if c in stock_ohlcv_by_code]
    data = {c: stock_ohlcv_by_code[c] for c in codes}
    kept = pf.apply_technical_filters(codes, data, apply_main_board=False, apply_bollinger=True)
    assert isinstance(kept, list)
    assert set(kept) <= set(codes)
    pf = PreFilters()
    n = len(pf._normalized_stock_blacklist)
    pf._reload_blacklists()
    assert len(pf._normalized_stock_blacklist) == n
