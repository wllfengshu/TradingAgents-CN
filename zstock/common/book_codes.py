"""账户层 / 行情同步共用的指数与债基 ETF 代码（与策略 market_book 契约一致）。"""

from __future__ import annotations

from zstock.common.utils.common_utils import normalize_code

INDUSTRY_ETFS: tuple[str, ...] = (
    "159928",  # 消费
    "512010",  # 医药
    "512880",  # 证券
    "512660",  # 军工
    "512400",  # 有色
    "512800",  # 银行
    "512480",  # 半导体
)
BOND = "511260"  # 十年国债 ETF
BOND_FALLBACK = "511010"  # 五年国债 ETF
BOND30 = "511090"  # 三十年国债 ETF
GOLD = "518880"  # 黄金 ETF
POLICY = "511520"  # 政金债 ETF
INDEX_CODE = "399300"
PARK_BONDS = frozenset({BOND, BOND_FALLBACK})
# 场内债基/黄金：回测按免佣金、免印花税（实盘常见零佣或极低摩擦）
FEE_FREE_CODES: frozenset[str] = frozenset(
    {BOND, BOND_FALLBACK, BOND30, GOLD, POLICY}
)
# 国债、政金债、黄金 ETF 当日可回转。股票和股票 ETF 是 T+1。
T0_CODES: frozenset[str] = FEE_FREE_CODES
BOOK_OHLCV_CODES: tuple[str, ...] = (
    INDEX_CODE,
    *INDUSTRY_ETFS,
    BOND,
    BOND_FALLBACK,
    BOND30,
    GOLD,
    POLICY,
)

def is_t0(code: str) -> bool:
    """国债 / 政金债 / 黄金可以当天买卖。"""
    return normalize_code(str(code)) in T0_CODES


__all__ = [
    "INDUSTRY_ETFS",
    "BOND",
    "BOND_FALLBACK",
    "BOND30",
    "GOLD",
    "POLICY",
    "INDEX_CODE",
    "PARK_BONDS",
    "FEE_FREE_CODES",
    "T0_CODES",
    "is_t0",
    "BOOK_OHLCV_CODES",
]
