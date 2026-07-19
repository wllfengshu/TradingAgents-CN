import asyncio

# =========================== 通用工具 ===========================

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
    """YYYYMMDD 或 YYYY-MM-DD → YYYY-MM-DD。"""
    s = (date_str or '').replace('-', '').replace('/', '').strip()[:8]
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return date_str

def to_yyyymmdd(date_str: str) -> str:
    """YYYY-MM-DD 或 YYYYMMDD → YYYYMMDD。"""
    return (date_str or '').replace('-', '').replace('/', '').strip()[:8]

async def to_thread(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

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
    return code.startswith(('600', '601', '603', '605', '000', '001', '002', '003'))

def is_st(name: str) -> bool:
    """根据股票名称判断是否 ST（含 *ST / ST）。"""
    return 'ST' in (name or '').upper()


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