# =========================== 通用工具 ===========================

from typing import Optional

def normalize_code(symbol: str) -> str:
    """规范化股票代码为 6 位纯数字（去除 SH/SZ/BJ 前后缀和 .SH/.SZ 等后缀）。"""
    normalized = (symbol or '').strip().upper()
    if '.' in normalized:
        normalized = normalized.split('.', 1)[0]
    for prefix in ('SH', 'SZ', 'BJ'):
        if normalized.startswith(prefix):
            return normalized[len(prefix):]
    return normalized

def normalize_date(date_str: str) -> str:
    """统一为 MongoDB / zstock 存储与查询用的日期格式：YYYY-MM-DD。

    接受 YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD。
    """
    s = (date_str or "").replace("-", "").replace("/", "").strip()[:8]
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return date_str

def to_yyyymmdd(date_str: str) -> str:
    """转为外部 API 常用的紧凑日期 YYYYMMDD（不用于 MongoDB 落库）。"""
    return (date_str or "").replace("-", "").replace("/", "").strip()[:8]

def is_main_board(code: str) -> bool:
    """根据代码前缀判断是否主板（排除科创板688/创业板300-301/北交所8xx）。"""
    code = str(code).strip()
    if not code or len(code) < 3:
        return False
    if code.startswith('688'):
        return False
    if code.startswith(('300', '301')):
        return False
    if len(code) == 6 and code.startswith('8'):
        return False
    return code.startswith(
        ("600", "601", "602", "603", "605", "000", "001", "002", "003")
    )

def is_st(name: str) -> bool:
    """根据股票名称判断是否 ST（含 *ST / ST）。"""
    return 'ST' in (name or '').upper()

def limit_up_threshold(code: str) -> float:
    """按板块返回涨停判定阈值（略低于法定幅度，兼容四舍五入）。

    - 主板约 10% → 0.095
    - 创业板 / 科创板约 20% → 0.195
    - 北交所约 30% → 0.295
    """
    c = normalize_code(code)
    if c.startswith("688") or c.startswith(("300", "301")):
        return 0.195
    if len(c) == 6 and c.startswith(("8", "4")):
        return 0.295
    return 0.095

def ensure_ohlcv_sorted(df):
    """保证 OHLCV DataFrame 按 trade_date 升序；无该列则原样返回。"""
    if df is None or getattr(df, "empty", True):
        return df
    if "trade_date" not in df.columns:
        return df
    try:
        if df["trade_date"].is_monotonic_increasing:
            return df
    except Exception:
        pass
    return df.sort_values("trade_date").reset_index(drop=True)

def ohlcv_asof(df, trade_date: Optional[str], *, require_exact: bool = True):
    """
    将 OHLCV 截到 trade_date（含）并保证升序。

    Args:
        require_exact: True 时要求末行日期恰好等于 trade_date（停牌/缺日则返回 None），
                       避免用更早 bar 冒充当日截面。
    """
    if df is None or getattr(df, "empty", True):
        return None
    if not trade_date or "trade_date" not in df.columns:
        return ensure_ohlcv_sorted(df)

    td = normalize_date(trade_date)
    out = ensure_ohlcv_sorted(df)
    dates = out["trade_date"].astype(str)
    # 兼容 YYYYMMDD
    dates_norm = dates.map(
        lambda x: normalize_date(x) if x and x[0:1].isdigit() else x
    )
    mask = dates_norm <= td
    if not bool(mask.any()):
        return None
    out = out.loc[mask].reset_index(drop=True)
    if out.empty:
        return None
    last = normalize_date(str(out["trade_date"].iloc[-1]))
    if require_exact and last != td:
        return None
    return out


def flow_docs_asof(
    day_docs: list,
    trade_date: Optional[str],
    *,
    require_exact: bool = True,
) -> list:
    """资金流文档截到 trade_date；require_exact 时末条必须等于当日。

    若文档本身无 trade_date 字段，视为调用方已按日对齐，原样返回。
    """
    if not day_docs:
        return []
    if not trade_date:
        return list(day_docs)
    if not any(d.get("trade_date") for d in day_docs):
        return list(day_docs)
    td = normalize_date(trade_date)
    kept = []
    for d in day_docs:
        dtd = normalize_date(str(d.get("trade_date", "") or ""))
        if not dtd or dtd > td:
            continue
        kept.append(d)
    if not kept:
        return []
    last = normalize_date(str(kept[-1].get("trade_date", "") or ""))
    if require_exact and last != td:
        return []
    return kept


# =========================== 市场判断 ===========================

def determine_market(code: str) -> str:
    """根据代码前缀判断 sh/sz（资金流接口需要）。"""
    c = normalize_code(code)
    if c.startswith('6'):
        return 'sh'
    return 'sz'


_INDEX_CODES = frozenset({
    '000001', '000016', '000300', '000852', '000905',  # 上证系列
    '399001', '399006', '399300', '399905',            # 深证系列
    '000688',                                           # 科创50
})


def is_index_code(code: str) -> bool:
    """判断是否为已知 A 股指数代码（白名单方式，避免误判个股）。"""
    return normalize_code(code) in _INDEX_CODES