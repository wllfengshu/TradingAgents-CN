"""
akshare 数据获取工具

与 xtquant_data_utils 接口完全对齐，用于 QMT 不可用时（如周末维护）替代。
所有公开函数的签名、返回列名均与 xtquant_data_utils 保持一致。

约定：
- 股票代码统一使用 6 位纯数字，对外返回也是这个格式。
- 日期参数接受 'YYYY-MM-DD' 或 'YYYYMMDD'，内部统一转换。

除非xtquant不可用，否则不建议使用该类！！！
"""
from __future__ import annotations
import time as _time
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from .common_utils import (
    determine_market,
    is_index_code,
    normalize_code,
    normalize_date,
    to_yyyymmdd,
)

logger = logging.getLogger(__name__)

_ak = None
# 全市场快照缓存（60秒 TTL），避免 fetch_trade_status 每只都拉全市场
_spot_cache: Dict[str, Any] = {'data': None, 'ts': 0.0}
_SPOT_CACHE_TTL = 60  # 秒

def _get_ak():
    global _ak
    if _ak is not None:
        return _ak
    try:
        import akshare as ak
        _ak = ak
        return ak
    except ImportError as e:
        raise RuntimeError(
            "akshare 未安装，请执行: pip install akshare"
        ) from e


# ============================== 代码格式工具 ==============================

# normalize_code, to_yyyymmdd, determine_market, is_index_code 均从 common_utils 导入


# ============================== 行情接口 ==============================

def _get_spot_snapshot(ak) -> pd.DataFrame:
    """获取全市场实时快照（带 60 秒缓存），避免重复拉取。"""
    now = _time.time()
    cached = _spot_cache.get('data')
    if cached is not None and (now - _spot_cache['ts']) < _SPOT_CACHE_TTL:
        return cached
    try:
        spot = ak.stock_zh_a_spot_em()
        if spot is not None and not spot.empty:
            _spot_cache['data'] = spot
            _spot_cache['ts'] = now
            return spot
    except Exception as e:
        logger.debug(f"_get_spot_snapshot 失败: {e}")
    return _spot_cache.get('data') or pd.DataFrame()


def _build_name_cache(ak) -> Dict[str, str]:
    """一次性拉取全市场代码→名称映射（复用缓存快照），供批量场景复用。"""
    try:
        spot = _get_spot_snapshot(ak)
        if spot is not None and not spot.empty:
            return dict(zip(spot['代码'].astype(str), spot['名称'].astype(str)))
    except Exception as e:
        logger.error(f"_build_name_cache 失败: {e}")
    return {}


def fetch_ohlcv(
    symbol: str,
    start_date: str,
    end_date: str,
    adjust: str = 'qfq',
    name: str = '',
) -> pd.DataFrame:
    """
    获取单只股票日线 OHLCV 数据（使用新浪财经接口，稳定性优于东方财富）。

    Returns:
        DataFrame，列：code, trade_date(YYYY-MM-DD), open, high, low, close,
        volume, amount, preClose。无数据返回空 DataFrame。
    """
    ak = _get_ak()
    code = normalize_code(symbol)
    st = to_yyyymmdd(start_date)
    et = to_yyyymmdd(end_date)

    # ── 指数走专用接口 ──
    if is_index_code(code):
        market = 'sh' if code.startswith('000') else 'sz'
        full_code = f"{market}{code}"
        try:
            df = ak.stock_zh_index_daily(symbol=full_code)
        except Exception as e:
            logger.warning(f"fetch_ohlcv(指数 {code}) 失败: {e}")
            return pd.DataFrame()
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.copy()
        df = df.rename(columns={'date': 'trade_date'})
        df['trade_date'] = pd.to_datetime(df['trade_date'], errors='coerce').dt.strftime('%Y-%m-%d')
        # 按日期范围过滤
        start_norm = f"{st[:4]}-{st[4:6]}-{st[6:]}"
        end_norm   = f"{et[:4]}-{et[4:6]}-{et[6:]}"
        df = df[(df['trade_date'] >= start_norm) & (df['trade_date'] <= end_norm)]
        df['code'] = code
        df['name'] = name
        if 'preClose' not in df.columns:
            df = df.sort_values('trade_date').reset_index(drop=True)
            df['preClose'] = df['close'].shift(1)
        cols = ['code', 'name', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'preClose']
        df = df[[c for c in cols if c in df.columns]].reset_index(drop=True)
        return df

    # ── 个股走新浪接口 ──
    if code.startswith('6') or code.startswith('9'):
        market = 'sh'
    else:
        market = 'sz'
    full_code = f"{market}{code}"

    try:
        df = ak.stock_zh_a_daily(
            symbol=full_code,
            start_date=st,
            end_date=et,
            adjust=adjust,
        )
    except Exception as e:
        logger.warning(f"fetch_ohlcv({code}) 失败: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    # 新浪接口返回英文列名
    df = df.rename(columns={'date': 'trade_date'})

    # trade_date 统一为字符串 YYYY-MM-DD
    df['trade_date'] = pd.to_datetime(df['trade_date'], errors='coerce').dt.strftime('%Y-%m-%d')
    df['code'] = code
    df['name'] = name

    # preClose：用 close.shift(1) 近似
    if 'preClose' not in df.columns:
        df = df.sort_values('trade_date').reset_index(drop=True)
        df['preClose'] = df['close'].shift(1)

    cols = ['code', 'name', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'preClose']
    df = df[[c for c in cols if c in df.columns]].reset_index(drop=True)
    return df


def fetch_ohlcv_batch(
    symbols: List[str],
    start_date: str,
    end_date: str,
    adjust: str = 'qfq',
) -> pd.DataFrame:
    """
    批量获取多只股票的日线 OHLCV 数据。

    Returns:
        DataFrame，列：code, name, trade_date(YYYY-MM-DD), open, high, low, close,
        volume, amount, preClose。所有股票纵向拼接，无数据返回空 DataFrame。
    """
    if not symbols:
        return pd.DataFrame()
    ak = _get_ak()
    name_cache = _build_name_cache(ak)
    frames = []
    for s in symbols:
        code = normalize_code(s)
        df = fetch_ohlcv(s, start_date, end_date, adjust, name=name_cache.get(code, ''))
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ============================== 股票列表 ==============================

def fetch_all_stocks(trade_date: Optional[str] = None) -> List[Dict[str, str]]:
    """获取全 A 股列表（沪深所有 A 股，含创业板/科创板/北交所，含 ST）。
    返回 [{'code': '000001', 'name': '平安银行'}, ...]
    """
    ak = _get_ak()
    try:
        df = ak.stock_info_a_code_name()
    except Exception as e:
        logger.error(f"fetch_all_stocks 获取股票列表失败: {e}")
        return []
    if df is None or df.empty:
        return []
    out: List[Dict[str, str]] = []
    for _, row in df.iterrows():
        code = normalize_code(str(row.get('code', '')))
        name = str(row.get('name', ''))
        if not code:
            continue
        out.append({'code': code, 'name': name})
    return out


# ============================== 板块接口 ==============================

def _em_call(func, *args, retries: int = 2, delay: float = 8.0, **kwargs):
    """调用东方财富(EM)接口，失败后退避重试。"""
    last_exc = None
    for i in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_exc = e
            if i < retries - 1:
                _time.sleep(delay * (i + 1))
    raise last_exc


def fetch_sector_list(sector_type: str = 'all') -> List[Dict[str, str]]:
    """
    获取板块列表。优先使用东方财富(EM)接口，失败则降级到同花顺(THS)接口。

    Args:
        sector_type: 'all' / 'concept' / 'industry'

    Returns:
        List[{sector_code, sector_name, sector_type}]
    """
    ak = _get_ak()
    result: List[Dict[str, str]] = []

    def _add_em(df, stype: str):
        if df is None or df.empty:
            return
        for _, row in df.iterrows():
            code = str(row.get('板块代码', '')).strip()
            name = str(row.get('板块名称', '')).strip()
            if code and name:
                result.append({'sector_code': code, 'sector_name': name, 'sector_type': stype})

    def _add_ths_concept(df):
        if df is None or df.empty:
            return
        for _, row in df.iterrows():
            # THS 返回列名为 'name' / 'code'
            name = str(row.get('name', row.get('概念名称', ''))).strip()
            if name:
                result.append({'sector_code': name, 'sector_name': name, 'sector_type': 'concept'})

    def _add_ths_industry(df):
        if df is None or df.empty:
            return
        for _, row in df.iterrows():
            name = str(row.get('name', row.get('行业名称', row.get('板块名称', '')))).strip()
            if name:
                result.append({'sector_code': name, 'sector_name': name, 'sector_type': 'industry'})

    # ── 概念板块 ──
    if sector_type in ('all', 'concept'):
        em_ok = False
        try:
            df = _em_call(ak.stock_board_concept_name_em)
            _add_em(df, 'concept')
            em_ok = bool(result)
            if em_ok:
                logger.info(f"概念板块(EM): {len(result)} 个")
        except Exception as e:
            logger.error(f"概念板块(EM)失败，降级到THS: {e}")

        if not em_ok:
            try:
                df = ak.stock_board_concept_name_ths()
                before = len(result)
                _add_ths_concept(df)
                logger.info(f"概念板块(THS): {len(result) - before} 个")
            except Exception as e:
                logger.error(f"概念板块(THS)也失败: {e}")

    # ── 行业板块 ──
    if sector_type in ('all', 'industry'):
        before = len(result)
        em_ok = False
        try:
            df = _em_call(ak.stock_board_industry_name_em)
            _add_em(df, 'industry')
            em_ok = len(result) > before
            if em_ok:
                logger.info(f"行业板块(EM): {len(result) - before} 个")
        except Exception as e:
            logger.error(f"行业板块(EM)失败，降级到THS: {e}")

        if not em_ok:
            try:
                df = ak.stock_board_industry_name_ths()
                before2 = len(result)
                _add_ths_industry(df)
                logger.info(f"行业板块(THS): {len(result) - before2} 个")
            except Exception as e:
                logger.warning(f"行业板块(THS)也失败: {e}")

    return result


def _is_em_code(sector_code: str) -> bool:
    """判断是否为东方财富板块代码（BK 开头的纯ASCII字符串）。"""
    return bool(sector_code) and sector_code.upper().startswith('BK') and sector_code.isascii()


def fetch_sector_stocks(
    sector_code: str,
    trade_date: Optional[str] = None,
    sector_name: Optional[str] = None,
    sector_type: Optional[str] = None,
) -> List[str]:
    """
    获取板块成分股代码列表（6 位代码）。
    - sector_code 为 BK 开头时使用 EM 接口；
    - sector_code 为中文名称时使用 THS 接口。

    Args:
        sector_code: 板块代码（如 BK0977）或板块名称
        trade_date: 交易日期（akshare 不支持历史成分，忽略）
        sector_name: 板块名称（如 "人工智能"），当 sector_code 为 BK 代码时必须提供
        sector_type: 'concept' / 'industry'，提供后直接走对应接口，省一半请求
    """
    ak = _get_ak()

    def _extract_codes(df) -> List[str]:
        if df is None or df.empty:
            return []
        for col in ('代码', 'code'):
            if col in df.columns:
                return [normalize_code(str(c)) for c in df[col].dropna()]
        return []

    # 根据 sector_type 选择 API 顺序
    def _em_funcs():
        if sector_type == 'concept':
            return [ak.stock_board_concept_cons_em]
        if sector_type == 'industry':
            return [ak.stock_board_industry_cons_em]
        return [ak.stock_board_concept_cons_em, ak.stock_board_industry_cons_em]

    def _ths_funcs():
        if sector_type == 'concept':
            return [ak.stock_board_concept_info_ths]
        if sector_type == 'industry':
            return [ak.stock_board_industry_info_ths]
        return [ak.stock_board_concept_info_ths, ak.stock_board_industry_info_ths]

    # EM 路径（BK 代码）
    if _is_em_code(sector_code):
        query_name = sector_name or sector_code
        if not sector_name:
            logger.warning(f"fetch_sector_stocks({sector_code}): BK代码需要配合sector_name使用")
        for em_func in _em_funcs():
            try:
                df = _em_call(em_func, symbol=query_name, retries=2, delay=5.0)
                codes = _extract_codes(df)
                if codes:
                    return codes
            except Exception:
                pass
        logger.warning(f"fetch_sector_stocks({sector_code}, name={sector_name}) EM接口失败")
        return []

    # THS 路径（中文名称）
    for ths_func in _ths_funcs():
        try:
            df = ths_func(symbol=sector_code)
            codes = _extract_codes(df)
            if codes:
                return codes
        except Exception:
            pass

    # THS 也失败，尝试 EM
    for em_func in _em_funcs():
        try:
            df = _em_call(em_func, symbol=sector_code, retries=1, delay=3.0)
            codes = _extract_codes(df)
            if codes:
                return codes
        except Exception:
            pass

    logger.warning(f"fetch_sector_stocks({sector_code}) 未获取到成分股")
    return []


# ============================== 资金流接口 ==============================

def fetch_capital_flow(symbol: str, trade_date: str) -> Optional[Dict[str, Any]]:
    """
    获取股票当日资金流向数据。
    一次 API 调用拿全部历史，Python 侧按日期过滤，避免重复请求。
    """
    history = fetch_capital_flow_history(symbol)
    if not history:
        return None
    td = to_yyyymmdd(trade_date)
    # 从最新往回找，优先匹配指定日期
    for entry in reversed(history):
        if entry.get('trade_date', '').replace('-', '') == td:
            return entry
    # 未匹配到指定日期，返回 None
    return None


def fetch_capital_flow_history(symbol: str, start_date: str = '', end_date: str = '') -> List[Dict[str, Any]]:
    """
    获取股票历史资金流。

    Args:
        start_date / end_date: 日期范围，空字符串表示不限。

    Returns:
        List[Dict]，每个 Dict 字段与 fetch_capital_flow 一致。
    """
    ak = _get_ak()
    code = normalize_code(symbol)
    market = determine_market(code)
    sd = to_yyyymmdd(start_date) if start_date else ''
    ed = to_yyyymmdd(end_date) if end_date else ''

    try:
        df = ak.stock_individual_fund_flow(stock=code, market=market)
    except Exception as e:
        logger.warning(f"fetch_capital_flow_history({code}) 失败: {e}")
        return []

    if df is None or df.empty:
        return []

    df = df.copy()
    date_col = next((c for c in df.columns if '日期' in c or 'date' in c.lower()), None)
    if not date_col:
        return []

    df[date_col] = df[date_col].astype(str).str.replace('-', '').str.replace('/', '')
    if sd:
        df = df[df[date_col] >= sd]
    if ed:
        df = df[df[date_col] <= ed]
    if df.empty:
        return []

    def _row_to_dict(row):
        def _f(col_candidates):
            for c in col_candidates:
                if c in row.index:
                    v = row[c]
                    try:
                        return float(v or 0)
                    except (TypeError, ValueError):
                        return 0.0
            return 0.0

        return {
            'code':             code,
            'trade_date':       normalize_date(str(row[date_col])),
            'last_price':       0.0,
            'turnover_amount':  _f(['成交额']),
            'turnover_volume':  0.0,
            'main_inflow':      _f(['主力净流入-净额', '主力净额']),
            'medium_inflow':    _f(['中单净流入-净额', '中单净额']),
            'small_inflow':     _f(['小单净流入-净额', '小单净额']),
            'bid_amount':       0.0,
            'ask_amount':       0.0,
        }

    return [_row_to_dict(row) for _, row in df.iterrows()]


# ============================== 交易状态接口 ==============================

def fetch_trade_status(symbol: str, trade_date: str) -> Optional[Dict[str, Any]]:
    """
    获取股票交易状态（is_st, is_suspended, is_limit_up, is_limit_down 等）。

    注意：akshare 无法精确判断历史日期的涨停/停牌，此处基于当日实时快照估算。
    """
    ak = _get_ak()
    code = normalize_code(symbol)
    td = to_yyyymmdd(trade_date)

    # 基本信息（名称、上市日期）
    name = ''
    open_date = ''
    try:
        info_df = ak.stock_individual_info_em(symbol=code)
        if info_df is not None and not info_df.empty:
            def _get_item(item_name):
                row = info_df[info_df['item'] == item_name]
                return str(row['value'].iloc[0]) if not row.empty else ''
            name      = _get_item('股票简称')
            open_date = _get_item('上市时间').replace('-', '')
    except Exception as e:
        logger.debug(f"fetch_trade_status 获取基本信息失败 ({code}): {e}")

    is_st = 'ST' in name.upper()

    # 上市天数
    days_listed = 0
    if len(open_date) == 8 and open_date.isdigit():
        try:
            open_dt = datetime.strptime(open_date, '%Y%m%d')
            ref_dt  = datetime.strptime(td, '%Y%m%d')
            days_listed = max((ref_dt - open_dt).days, 0)
        except Exception:
            pass

    # 实时快照：涨停/跌停/停牌
    last_price = pre_close = 0.0
    up_stop = down_stop = 0.0
    is_limit_up = is_limit_down = is_suspended = False

    try:
        spot = _get_spot_snapshot(ak)
        if spot is not None and not spot.empty:
            row = spot[spot['代码'] == code]
            if not row.empty:
                r = row.iloc[0]
                last_price = float(r.get('最新价', 0) or 0)
                pre_close  = float(r.get('昨收', 0) or 0)
                high_limit = float(r.get('涨停价', 0) or 0)
                low_limit  = float(r.get('跌停价', 0) or 0)
                # 部分版本 akshare 列名不同，兜底用涨幅判断
                if high_limit == 0 and pre_close > 0:
                    # 主板 ±10%，创业板/科创板 ±20%，北交所 ±30%
                    if code.startswith('30') or code.startswith('68'):
                        high_limit = round(pre_close * 1.20, 2)
                        low_limit  = round(pre_close * 0.80, 2)
                    elif code.startswith('8') or code.startswith('4'):
                        high_limit = round(pre_close * 1.30, 2)
                        low_limit  = round(pre_close * 0.70, 2)
                    else:
                        high_limit = round(pre_close * 1.10, 2)
                        low_limit  = round(pre_close * 0.90, 2)
                up_stop   = high_limit
                down_stop = low_limit
                if last_price > 0 and up_stop > 0:
                    is_limit_up   = abs(last_price - up_stop)   < 0.005
                    is_limit_down = abs(last_price - down_stop) < 0.005
                # 成交量为 0 且非新股视为停牌
                vol = float(r.get('成交量', 1) or 1)
                if vol == 0 and days_listed > 5:
                    is_suspended = True
    except Exception as e:
        logger.debug(f"fetch_trade_status 获取实时快照失败 ({code}): {e}")

    return {
        'code':            code,
        'trade_date':      normalize_date(trade_date),
        'name':            name,
        'open_date':       open_date,
        'is_st':           is_st,
        'is_suspended':    is_suspended,
        'is_limit_up':     is_limit_up,
        'is_limit_down':   is_limit_down,
        'is_trading':      not is_suspended,
        'days_listed':     days_listed,
        'up_stop_price':   up_stop,
        'down_stop_price': down_stop,
        'pre_close':       pre_close,
    }


# ============================== 批量接口（一次拉全市场）==============================


def fetch_trade_status_batch(
    codes: List[str], trade_date: str,
) -> Dict[str, Dict[str, Any]]:
    """
    批量获取交易状态：复用缓存的全市场快照，按 codes 过滤返回。

    Returns: {code: {trade_date, name, is_st, is_suspended, is_limit_up, ...}}
    """
    ak = _get_ak()
    td = normalize_date(trade_date)
    code_set = set(normalize_code(c) for c in codes)
    result: Dict[str, Dict[str, Any]] = {}

    try:
        spot = _get_spot_snapshot(ak)
    except Exception as e:
        logger.warning(f"fetch_trade_status_batch 获取全市场快照失败: {e}")
        return result

    if spot is None or spot.empty:
        return result

    for _, r in spot.iterrows():
        code = str(r.get('代码', '')).strip()
        if code not in code_set:
            continue
        name_full = str(r.get('名称', '')).strip()
        last_price = float(r.get('最新价', 0) or 0)
        pre_close  = float(r.get('昨收', 0) or 0)
        high_limit = float(r.get('涨停价', 0) or 0)
        low_limit  = float(r.get('跌停价', 0) or 0)
        vol = float(r.get('成交量', 1) or 1)

        is_st = 'ST' in name_full.upper()
        amount = float(r.get('成交额', 1) or 1)
        is_suspended = (vol == 0 and amount == 0)
        is_limit_up = False
        is_limit_down = False
        if high_limit > 0 and last_price > 0:
            is_limit_up   = last_price >= high_limit
            is_limit_down = low_limit > 0 and last_price <= low_limit
        elif pre_close > 0 and last_price > 0:
            pct = (last_price - pre_close) / pre_close
            if code.startswith('30') or code.startswith('68'):
                is_limit_up   = pct >= 0.195
                is_limit_down = pct <= -0.195
            else:
                is_limit_up   = pct >= 0.095
                is_limit_down = pct <= -0.095

        result[code] = {
            'code': code,
            'trade_date': td,
            'name': name_full,
            'is_st': is_st,
            'is_suspended': is_suspended,
            'is_limit_up': is_limit_up,
            'is_limit_down': is_limit_down,
            'is_trading': not is_suspended,
            'days_listed': 0,
        }

    logger.info(f"fetch_trade_status_batch: 全市场 {len(spot)} 只, 匹配 {len(result)}/{len(code_set)} 只")
    return result


def fetch_capital_flow_batch(
    codes: List[str], trade_date: str,
) -> Dict[str, Dict[str, Any]]:
    """
    批量获取当日资金流：使用东方财富个股资金流排名接口，
    一次拉取全市场数据再按 codes 过滤。

    Returns: {code: {code, trade_date, main_inflow, medium_inflow, small_inflow, turnover_amount, ...}}
    """
    ak = _get_ak()
    td = normalize_date(trade_date)
    code_set = set(normalize_code(c) for c in codes)
    result: Dict[str, Dict[str, Any]] = {}

    # 尝试使用全市场资金流排名接口
    try:
        # stock_individual_fund_flow_rank 返回全市场个股当日资金流排名
        df = ak.stock_individual_fund_flow_rank(indicator="今日")
    except Exception as e:
        logger.warning(f"fetch_capital_flow_batch 获取全市场资金流失败: {e}")
        return result

    if df is None or df.empty:
        return result

    # 找到代码列和日期列
    code_col = next((c for c in df.columns if '代码' in c or 'code' in c.lower()), None)
    if not code_col:
        # 尝试用索引列
        code_col = df.columns[0] if len(df.columns) > 0 else None

    if not code_col:
        logger.warning("fetch_capital_flow_batch: 找不到代码列")
        return result

    for _, row in df.iterrows():
        code = str(row.get(code_col, '')).strip()
        if code not in code_set:
            continue

        def _f(col_candidates):
            for c in col_candidates:
                if c in row.index:
                    v = row[c]
                    try:
                        return float(v or 0)
                    except (TypeError, ValueError):
                        return 0.0
            return 0.0

        result[code] = {
            'code': code,
            'trade_date': td,
            'last_price': 0.0,
            'turnover_amount': _f(['成交额']),
            'turnover_volume': 0.0,
            'main_inflow': _f(['主力净流入-净额', '主力净额']),
            'medium_inflow': _f(['中单净流入-净额', '中单净额']),
            'small_inflow': _f(['小单净流入-净额', '小单净额']),
            'bid_amount': 0.0,
            'ask_amount': 0.0,
        }

    logger.info(f"fetch_capital_flow_batch: 全市场 {len(df)} 只, 匹配 {len(result)}/{len(code_set)} 只")
    return result
