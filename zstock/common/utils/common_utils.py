# =========================== 通用常量 ===========================

# 资金流单位换算：xtquant L2 净额字段(main_net/m_net/s_net)单位为"万元"，
# 成交额字段(turnover)单位为"元"。统一换算到元，保证净值比量纲一致。
WAN_TO_YUAN = 10000.0

# =========================== 通用工具 ===========================

import math
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

# 创业板注册制涨跌幅由 10% 改为 20% 的生效日（含当日）。
_CHINEXT_20_DATE = "2020-08-24"


def limit_ratio(code: str, trade_date: Optional[str] = None, name: str = "") -> float:
    """法定涨跌幅比例。

    主板 10%，主板 ST/*ST 5%，科创板 20%，北交所 30%。
    创业板在 2020-08-24 之前为 10%（ST 5%），当日及之后为 20%。
    """
    c = normalize_code(code)
    st = is_st(name)
    if c.startswith("688"):
        return 0.20
    if c.startswith(("300", "301")):
        if trade_date and normalize_date(str(trade_date)) < _CHINEXT_20_DATE:
            return 0.05 if st else 0.10
        return 0.20
    if len(c) == 6 and (c.startswith(("8", "4")) or c.startswith("92")):
        return 0.30
    if st:
        return 0.05
    return 0.10


def limit_up_threshold(code: str, trade_date: Optional[str] = None, name: str = "") -> float:
    """法定涨跌幅比例。成交判定用 is_limit_up / is_limit_down（四舍五入到分）。"""
    return limit_ratio(code, trade_date, name)


def limit_price(pre_close: float, ratio: float, *, up: bool) -> float:
    """涨跌停价：前收 × (1±比例)，四舍五入到分。"""
    try:
        pre = float(pre_close)
        r = float(ratio)
    except (TypeError, ValueError):
        return float("nan")
    if not (pre > 0) or not math.isfinite(pre) or not math.isfinite(r):
        return float("nan")
    raw = pre * (1.0 + r if up else 1.0 - r)
    return round(raw + 1e-8, 2)


def is_limit_up(
    code: str,
    pre_close: float,
    price: float,
    trade_date: Optional[str] = None,
    name: str = "",
) -> bool:
    """价格是否封在涨停价（含一价涨停）。"""
    try:
        px = float(price)
        pre = float(pre_close)
    except (TypeError, ValueError):
        return False
    if not (px > 0) or not (pre > 0):
        return False
    lim = limit_price(pre, limit_ratio(code, trade_date, name), up=True)
    return math.isfinite(lim) and px + 1e-6 >= lim


def is_limit_down(
    code: str,
    pre_close: float,
    price: float,
    trade_date: Optional[str] = None,
    name: str = "",
) -> bool:
    """价格是否封在跌停价。收盘封跌停视为卖不出。"""
    try:
        px = float(price)
        pre = float(pre_close)
    except (TypeError, ValueError):
        return False
    if not (px > 0) or not (pre > 0):
        return False
    lim = limit_price(pre, limit_ratio(code, trade_date, name), up=False)
    return math.isfinite(lim) and px <= lim + 1e-6


def is_equity_etf(code: str) -> bool:
    """场内 ETF（股票/跨境/商品等）。买卖有佣金，没有股票印花税。"""
    c = normalize_code(code)
    return len(c) == 6 and c.startswith(("15", "16", "51", "56", "58"))


def average_rank_percentile(values: dict) -> dict:
    """平均秩百分位，0=最弱，100=最强。并列取平均秩，按样本数而不是不同取值个数。"""
    finite = []
    for key, raw in (values or {}).items():
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(val):
            finite.append((key, val))
    n = len(finite)
    if n == 0:
        return {}
    if n == 1:
        return {finite[0][0]: 100.0}
    finite.sort(key=lambda item: item[1])
    out = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and finite[j + 1][1] == finite[i][1]:
            j += 1
        avg_rank = (i + 1 + j + 1) / 2.0
        pct = (avg_rank - 1.0) / (n - 1) * 100.0
        for k in range(i, j + 1):
            out[finite[k][0]] = pct
        i = j + 1
    return out


def buy_shares(code: str, raw_shares: int) -> int:
    """买入申报股数。主板/ETF 整手 100；科创板 200 股起，超过后可以 1 股递增。"""
    try:
        shares = int(raw_shares)
    except (TypeError, ValueError):
        return 0
    if shares <= 0:
        return 0
    c = normalize_code(code)
    if c.startswith("688"):
        return shares if shares >= 200 else 0
    return (shares // 100) * 100


def sell_shares(code: str, want: int, position: int, can_use: Optional[int] = None) -> int:
    """卖出申报股数，不超过持仓和可用（T+1）。

    主板不足 100 的余股只能整笔卖出。科创板可用不足 200 时必须一次卖光；
    卖完后若剩余不足 200 且不为 0，改为卖出全部可用。
    """
    try:
        want_n = int(want)
        pos_n = int(position)
    except (TypeError, ValueError):
        return 0
    tradable = pos_n
    if can_use is not None:
        try:
            tradable = min(pos_n, int(can_use))
        except (TypeError, ValueError):
            tradable = pos_n
    if want_n <= 0 or tradable <= 0:
        return 0
    want_n = min(want_n, tradable)
    c = normalize_code(code)
    if c.startswith("688"):
        if tradable < 200:
            return tradable if want_n >= tradable else 0
        if want_n < 200:
            return 0
        if 0 < tradable - want_n < 200:
            return tradable
        return want_n
    if want_n >= tradable:
        return tradable
    return (want_n // 100) * 100

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
    if not trade_date or "trade_date" not in getattr(df, "columns", []):
        # 调用方要求「恰好当日」但没有日期列时，不能把整段历史当成当日。
        if require_exact and trade_date:
            return None
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


def get_index_ohlcv_df(index_ohlcv_dict):
    """从 {index_code: DataFrame} 取指数行情：优先第一个 key，否则 399300。"""
    if not index_ohlcv_dict:
        return None
    code = get_index_code_from_ohlcv_dict(index_ohlcv_dict)
    df = index_ohlcv_dict.get(code) if code else None
    if df is None:
        df = index_ohlcv_dict.get("399300")
    return df


def to_float_or_none(val) -> Optional[float]:
    """安全转换为 float，NaN/None/非法值均返回 None。"""
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return f


def passes_gate_value(val, gate: dict) -> bool:
    """通用配置驱动门禁条件判定：支持 finite / gt / gte / eq 操作符。"""
    op = str(gate.get("op", "gt")).strip().lower()
    if op == "finite":
        return to_float_or_none(val) is not None
    if op in ("gt", ">"):
        f = to_float_or_none(val)
        ref = to_float_or_none(gate.get("value", 0))
        return f is not None and ref is not None and f > ref
    if op in ("gte", ">="):
        f = to_float_or_none(val)
        ref = to_float_or_none(gate.get("value", 0))
        return f is not None and ref is not None and f >= ref
    if op in ("eq", "=="):
        f = to_float_or_none(val)
        ref = to_float_or_none(gate.get("value"))
        return f is not None and ref is not None and f == ref
    return True
