"""灯色合成：股票、行业 ETF、低波和当前持仓。"""

from zstock.strategy_management.sleeve_combine import combine_by_light


def test_green_buys_stocks_then_industry_then_low_vol():
    weights = combine_by_light(
        "green",
        current={},
        stocks={"600000": 0.20},
        industry={"512880": 0.40, "512480": 0.20, "511260": 0.40},
        low_vol={"511090": 0.35, "511260": 0.35, "518880": 0.30},
    )
    assert abs(weights["600000"] - 0.20) < 1e-8
    assert abs(weights["512880"] - 0.40) < 1e-8
    assert abs(weights["512480"] - 0.20) < 1e-8
    assert abs(weights["511090"] - 0.07) < 1e-8
    assert abs(weights["518880"] - 0.06) < 1e-8
    assert abs(sum(weights.values()) - 1.0) < 1e-8


def test_yellow_keeps_wanted_stocks_and_buys_industry():
    weights = combine_by_light(
        "yellow",
        current={"600000": 0.20, "600001": 0.10, "512880": 0.70},
        stocks={"600000": 0.30, "600002": 0.20},
        industry={"512010": 0.50, "511260": 0.50},
        low_vol={"511090": 1.0},
    )
    assert abs(weights["600000"] - 0.20) < 1e-8
    assert "600001" not in weights
    assert "600002" not in weights
    assert abs(weights["512010"] - 0.40) < 1e-8
    assert abs(weights["511260"] - 0.40) < 1e-8
    assert "511090" not in weights
    assert abs(sum(weights.values()) - 1.0) < 1e-8


def test_red_is_only_low_vol():
    weights = combine_by_light(
        "red",
        current={"600000": 0.20, "512880": 0.30, "512480": 0.10},
        stocks={"600000": 0.20},
        industry={"512880": 0.40, "512010": 0.30, "511260": 0.30},
        low_vol={"511090": 0.35, "511260": 0.35, "511520": 0.30},
    )
    assert "600000" not in weights
    assert "512880" not in weights
    assert "512480" not in weights
    assert "512010" not in weights
    assert abs(weights["511090"] - 0.35) < 1e-8
    assert abs(weights["511260"] - 0.35) < 1e-8
    assert abs(weights["511520"] - 0.30) < 1e-8
    assert abs(sum(weights.values()) - 1.0) < 1e-8
