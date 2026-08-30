# =========================== 通用常量 ===========================

# 资金流单位换算：xtquant L2 净额字段(main_net/m_net/s_net)单位为"万元"，
# 成交额字段(turnover)单位为"元"。统一换算到元，保证净值比量纲一致。
WAN_TO_YUAN = 10000.0

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


# 000xxx 段沪深指数与深市个股完全同码（例：000001 上证指数 vs 深市平安银行、
# 000300 沪深300 vs 深市宜华木业、000905 中证500 vs 深市金城股份 …），
# 因此按"6 位纯数字"是无法判定该 code 到底是指数还是股票的。
# 这里把指数专用段拆成两类：
#   - _UNAMBIGUOUS_INDEX_CODES：仅在指数命名空间存在的代码（399xxx/000688）
#   - _AMBIGUOUS_INDEX_CODES：与深市 000xxx 股票同码，必须带 SH 前缀才可判定为指数
_UNAMBIGUOUS_INDEX_CODES = frozenset({
    '399001', '399006', '399300', '399905',  # 深证系列指数
    '000688',                                 # 科创50（深市 000xxx 不含此码）
})
_AMBIGUOUS_INDEX_CODES = frozenset({
    '000001', '000016', '000300', '000852', '000905',  # 上证系列指数
})


def _extract_market_prefix(symbol: str) -> str:
    """提取显式市场前缀（SH/SZ/BJ）。无前缀返回空字符串。

    支持以下形态：
    - "SH000001" / "000001.SH" / "sh000001"
    - "000001"（无前缀）返回 ""
    """
    if not symbol:
        return ''
    s = str(symbol).strip().upper()
    for pref in ('SH', 'SZ', 'BJ'):
        if s.startswith(pref):
            return pref
    if '.' in s:
        suf = s.rsplit('.', 1)[1]
        if suf in ('SH', 'SZ', 'BJ'):
            return suf
    return ''


def is_index_code(code: str) -> bool:
    """判断是否为 A 股指数代码。

    规则（避免与深市 000xxx 段股票误判）：
    - 若 symbol 显式带 SH 前缀（如 "SH000001" / "000001.SH"）：属于任一指数集即 True
    - 若显式带 SZ/BJ 前缀：不属于指数集，一律 False
    - 若无市场前缀：只有 399xxx / 000688 这类"无歧义"指数段返回 True；
      000xxx 段（与深市股票同码）默认视为股票并返回 False。
      调用方若确实要按指数处理，请传入带 SH 前缀的完整 symbol。
    """
    if code is None:
        return False
    prefix = _extract_market_prefix(str(code))
    c = normalize_code(str(code))
    if not c:
        return False
    if prefix == 'SH':
        return c in _AMBIGUOUS_INDEX_CODES or c in _UNAMBIGUOUS_INDEX_CODES
    if prefix in ('SZ', 'BJ'):
        # 深/北市场不存在与 000xxx 指数冲突的指数命名空间；只有 399xxx 例外，
        # 但 399xxx 归深交所指数，不属于个股，此处一并按无前缀分支处理。
        return c in _UNAMBIGUOUS_INDEX_CODES
    # 无显式前缀：只承认无歧义段
    return c in _UNAMBIGUOUS_INDEX_CODES


# =========================== 通用排序和过滤 ===========================

def minmax_normalize(values_dict, default_val: float = 50.0) -> dict:
    """min-max 归一化转 0-100（跳过 nan 和 inf，确保数据有效）。

    所有因子模块统一使用此方法，避免多份重复实现。

    Args:
        values_dict: {key: float} 原始值字典
        default_val: 当所有值相同时返回的默认值

    Returns:
        {key: float} 归一化后的分值字典（0~100 范围）
    """
    if not values_dict:
        return {}
    import math
    clean = {
        k: float(v)
        for k, v in values_dict.items()
        if v is not None and isinstance(v, (int, float)) and math.isfinite(v)
    }
    if not clean:
        return {}
    min_val = min(clean.values())
    max_val = max(clean.values())
    if max_val == min_val:
        return {k: default_val for k in clean}
    return {k: 100.0 * (v - min_val) / (max_val - min_val) for k, v in clean.items()}


def select_top_k(scores_dict, k: int):
    """按分数降序排序，取前 K 个。

    Args:
        scores_dict: {code: score} 字典
        k: 数量限制

    Returns:
        [(code, score), ...] 列表，按分数降序排列
    """
    if not scores_dict:
        return []
    return sorted(scores_dict.items(), key=lambda x: x[1], reverse=True)[:k]


def apply_blacklist_filter(scores_dict, blacklist_set):
    """从分数字典中过滤掉黑名单。

    Args:
        scores_dict: {code: score} 字典
        blacklist_set: {code, ...} 黑名单集合

    Returns:
        {code: score} 过滤后的字典
    """
    if not blacklist_set:
        return scores_dict
    return {k: v for k, v in scores_dict.items() if k not in blacklist_set}


def get_index_code_from_ohlcv_dict(index_ohlcv_dict):
    """从 {index_code: DataFrame} 字典中安全获取指数代码。

    Args:
        index_ohlcv_dict: {index_code: DataFrame} 字典

    Returns:
        str: 指数代码，若字典为空返回 ""
    """
    if not index_ohlcv_dict:
        return ""
    return list(index_ohlcv_dict.keys())[0]