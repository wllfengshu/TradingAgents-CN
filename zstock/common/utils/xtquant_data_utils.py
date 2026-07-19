"""
xtquant 数据获取工具

封装 xtquant.xtdata 的行情/板块/合约/财务等只读接口，用于
zstock.data_management.query_service 的"截面策略专用接口"在 MongoDB
缓存未命中时回源到 QMT。

约定：
- 所有股票代码统一使用 6 位纯数字（不含市场后缀），对外返回也是这个格式。
- 对 xtquant 调用时内部转换为 "code.SH/.SZ/.BJ" 格式。
- 连接管理直接复用 app.utils.xtquant_util 中已有的实现。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from .common_utils import normalize_code, to_yyyymmdd

logger = logging.getLogger(__name__)

_xtdata = None
_xtdata_lock = threading.Lock()

def _get_xtdata():
    """惰性导入 xtdata。复用 QMTUtil 的安装路径配置。"""
    global _xtdata
    if _xtdata is not None:
        return _xtdata
    with _xtdata_lock:
        if _xtdata is not None:
            return _xtdata

        from app.utils.xtquant_util import ensure_xtquant_importable, get_xtquant_client

        # 复用 xtquant_util 中已经写好的 sys.path 注入逻辑
        client = get_xtquant_client()
        try:
            ensure_xtquant_importable(client.install_path)
        except Exception as e:
            logger.error(f"_ensure_xtquant_importable: {e}")

        try:
            from xtquant import xtdata
        except ImportError as e:
            raise RuntimeError(
                "xtquant 未安装或不可用，请确认已启动 miniQMT 且当前 Python "
                "环境安装了 xtquant 包。"
            ) from e

        try:
            xtdata.enable_hello = False
        except Exception:
            pass

        _xtdata = xtdata
        return xtdata

# ============================== 代码格式工具 ==============================

def _ensure_sector_data(xtdata) -> None:
    """首次调用板块接口时下载一次板块分类信息（耗时较长，仅一次）。"""
    try:
        logger.info("⏬ 触发 download_sector_data()，首次下载板块分类信息...")
        # xtdata.download_sector_data()
        client = xtdata.get_client()
        client.down_all_sector_data()   # 替代 xtdata.download_sector_data()
        print("板块数据下载完成")
        logger.info("✅ 板块分类信息下载完成")
    except Exception as e:
        logger.error(f"download_sector_data 失败: {e}")


def to_xt_code(code: str) -> str:
    """6 位代码转换为 xtquant 格式（带交易所后缀）。"""
    c = normalize_code(code)
    if not c:
        return c
    head = c[0]
    if head == '6':
        return f"{c}.SH"
    if head in ('0', '3'):
        return f"{c}.SZ"
    if head in ('4', '8'):
        return f"{c}.BJ"
    if head == '9' and c[:3] == '920':
        return f"{c}.BJ"  # 北交所新代码段（920xxx）
    if head == '9':
        return f"{c}.SH"  # B股沪
    if head == '2':
        return f"{c}.SZ"  # B股深
    return f"{c}.SH"


# ============================== 行情接口 ==============================

def _build_ohlcv_df(xtdata, xt_code: str, symbol: str, raw_df: pd.DataFrame) -> pd.DataFrame:
    """将 get_market_data_ex 返回的单只股票原始 DataFrame 整理为标准格式。"""
    df = raw_df.copy()
    df.index = pd.to_datetime(df.index.astype(str), format='%Y%m%d', errors='coerce')
    df = df[df.index.notna()]
    df['trade_date'] = df.index.strftime('%Y-%m-%d')
    df['code'] = normalize_code(symbol)
    detail = xtdata.get_instrument_detail(xt_code, iscomplete=False)
    df['name'] = str(detail.get('InstrumentName') or '') if detail else ''
    cols = ['code', 'name', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount']
    for opt in ('preClose', 'suspendFlag'):
        if opt in df.columns:
            cols.append(opt)
    return df[[c for c in cols if c in df.columns]].reset_index(drop=True)


def fetch_ohlcv(symbol: str, start_date: str, end_date: str, dividend_type: str = 'front') -> pd.DataFrame:
    """
    获取单只股票的日线 OHLCV 数据。

    Returns:
        DataFrame，列：code, name, trade_date(YYYY-MM-DD), open, high, low, close,
        volume, amount, preClose, suspendFlag。无数据返回空 DataFrame。
    """
    xtdata = _get_xtdata()
    xt_code = to_xt_code(symbol)
    st = to_yyyymmdd(start_date)
    et = to_yyyymmdd(end_date)

    try:
        xtdata.download_history_data(xt_code, period='1d', start_time=st, end_time=et, incrementally=True)
    except Exception as e:
        logger.warning(f"download_history_data({xt_code}) 失败: {e}")

    data = xtdata.get_market_data_ex(
        [], [xt_code], period='1d',
        start_time=st, end_time=et, count=-1,
        dividend_type=dividend_type, fill_data=False,
    )

    raw = data.get(xt_code) if data else None
    if raw is None or len(raw) == 0:
        return pd.DataFrame()

    return _build_ohlcv_df(xtdata, xt_code, symbol, raw)


def fetch_ohlcv_batch(symbols: List[str],start_date: str,end_date: str,dividend_type: str = 'front',) -> pd.DataFrame:
    """
    批量获取多只股票的日线 OHLCV 数据。

    Returns:
        DataFrame，列：code, name, trade_date(YYYY-MM-DD), open, high, low, close,
        volume, amount, preClose, suspendFlag。所有股票纵向拼接，无数据返回空 DataFrame。
    """
    if not symbols:
        return pd.DataFrame()

    xtdata = _get_xtdata()
    xt_codes = [to_xt_code(s) for s in symbols]
    st = to_yyyymmdd(start_date)
    et = to_yyyymmdd(end_date)

    try:
        xtdata.download_history_data2(xt_codes, period='1d', start_time=st, end_time=et, incrementally=True)
    except Exception as e:
        logger.warning(f"download_history_data2 失败: {e}")

    data = xtdata.get_market_data_ex(
        [], xt_codes, period='1d',
        start_time=st, end_time=et, count=-1,
        dividend_type=dividend_type, fill_data=False,
    )

    if not data:
        return pd.DataFrame()

    frames = []
    for xt_code, symbol in zip(xt_codes, symbols):
        raw = data.get(xt_code)
        if raw is None or len(raw) == 0:
            continue
        frames.append(_build_ohlcv_df(xtdata, xt_code, symbol, raw))

    if not frames:
        return pd.DataFrame()

    return pd.concat(frames, ignore_index=True)


def fetch_float_shares_map(codes: List[str]) -> Dict[str, float]:
    """
    批量获取股票当前流通股本（单位：股）。

    数据来源：xtquant get_instrument_detail 的 FloatVolume 字段，为当前快照值
    （限售解禁等变动不会回溯），用作历史区间换手率的近似分母。

    Args:
        codes: 6 位股票代码列表

    Returns: {code: float_volume(股)}，取不到的不放入字典
    """
    if not codes:
        return {}
    xtdata = _get_xtdata()
    result: Dict[str, float] = {}
    for c in codes:
        pure = normalize_code(c)
        xt_code = to_xt_code(pure)
        try:
            detail = xtdata.get_instrument_detail(xt_code, iscomplete=False)
        except Exception as e:
            logger.debug(f"get_instrument_detail({xt_code}) 失败: {e}")
            detail = None
        if not detail:
            continue
        try:
            fv = float(detail.get('FloatVolume', 0.0) or 0.0)
        except (ValueError, TypeError):
            fv = 0.0
        if fv > 0:
            result[pure] = fv
    return result


# ============================== 板块接口 ==============================

def fetch_sector_list(sector_type: str = 'all', a_stock_only: bool = True) -> List[Dict[str, str]]:
    """
    获取板块列表。xtdata 的 get_sector_list 返回的是板块名称字符串数组。
    这里给每个板块附上一个简单的类型标签，方便上层按 concept/index/board 等过滤。

    Args:
        sector_type: 'all' / 'concept' / 'index' / 'board'。
            过滤条件按板块名称关键字粗略归类。
        a_stock_only: True 时仅保留与 A 股相关的板块，排除期货交易所、
            B股、期权、债券、基金 等非 A 股品种板块（默认 True）。

    Returns:
        List[{sector_code, sector_name, sector_type}]
    """
    xtdata = _get_xtdata()
    _ensure_sector_data(xtdata)
    sectors = xtdata.get_sector_list() or []

    import re as _re

    # ── 非 A 股板块黑名单关键词 ──
    _EXCLUDE_EXCHANGE_KW = ('上期所', '中金所', '大商所', '郑商所', '能源中心',
                            '广期所', '上金所', '上期能源')
    _EXCLUDE_NON_A_KW = ('B股', '期权', '债券', '基金', 'ETF', '转债',
                         '期货', '港股', '美股', '新三板', '回购')
    # 指数成分×行业交叉板块（如 300SW2贵金属, 1000THY2xxx）— 量化辅助，非券商标准
    _INDEX_CROSS_RE = _re.compile(r'^\d+(SW|THY)\d')

    def _classify(name: str) -> str:
        if '概念' in name:
            return 'concept'
        if '指数' in name:
            return 'index'
        if any(k in name for k in ('A股', '主板', '创业板', '科创板', '北交所')):
            return 'board'
        if any(k in name for k in _EXCLUDE_EXCHANGE_KW):
            return 'exchange'
        if any(k in name for k in ('B股', 'ETF', '债券', '基金', '期权', '转债')):
            return 'board_non_a'
        return 'other'

    def _is_a_stock_relevant(name: str, st: str) -> bool:
        """判断板块是否与 A 股相关"""
        if st == 'exchange':
            return False
        if st == 'board_non_a':
            return False
        if any(k in name for k in _EXCLUDE_NON_A_KW):
            return False
        if any(k in name for k in _EXCLUDE_EXCHANGE_KW):
            return False
        # 排除指数交叉板块 (300SW2xxx, 500THY2xxx, 1000SW2xxx 等)
        if _INDEX_CROSS_RE.match(name):
            return False
        # 排除加权指数变体 (SW2贵金属加权)
        if name.endswith('加权'):
            return False
        return True

    result: List[Dict[str, str]] = []
    skipped = 0
    for name in sectors:
        st = _classify(name)
        if a_stock_only and not _is_a_stock_relevant(name, st):
            skipped += 1
            continue
        if sector_type != 'all' and st != sector_type:
            continue
        # 板块名即 code（xtquant 用 name 作为查询键）
        result.append({
            'sector_code': name,
            'sector_name': name,
            'sector_type': st,
        })
    if skipped:
        logger.info(f"fetch_sector_list: 跳过 {skipped} 个非 A 股板块 (a_stock_only={a_stock_only})")
    return result


def fetch_sector_stocks(sector_code: str, trade_date: Optional[str] = None, sector_name: Optional[str] = None) -> List[str]:
    """
    获取板块成分股代码列表（6 位代码，已剔除非 A 股市场后缀）。

    Args:
        sector_code: 板块名（xtquant 用名称作为查询键）。
        trade_date: 'YYYY-MM-DD' / 'YYYYMMDD'，为空则返回最新成分。
        sector_name: 板块名称（优先使用，与 sector_code 含义相同但更明确）。
    """
    xtdata = _get_xtdata()
    _ensure_sector_data(xtdata)
    # xtquant 使用板块名称作为查询键，优先使用 sector_name
    query_key = sector_name or sector_code
    if trade_date:
        ts = to_yyyymmdd(trade_date)
        try:
            real_timetag = int(ts)
        except ValueError:
            real_timetag = 0
        stocks = xtdata.get_stock_list_in_sector(query_key, real_timetag) or []
    else:
        stocks = xtdata.get_stock_list_in_sector(query_key) or []
    return [normalize_code(s) for s in stocks]


# ============================== 资金流接口 不支持 ==============================

def fetch_capital_flow(symbol: str, trade_date: str) -> Optional[Dict[str, Any]]:
    """
    获取股票当日的"资金流"数据。
    不支持
    """
    logger.error('xtquant fetch_capital_flow 无历史资金流数据，仅当天有效，请用别的数据源。')
    return None


def fetch_capital_flow_history(symbol: str, start_date: str, end_date: str) -> List[Dict[str, Any]]:
    """
    历史资金流数据
    不支持
    """
    logger.error('xtquant fetch_capital_flow_history 无历史资金流数据，仅当天有效，请用别的数据源。')
    return []


# ============================== 交易状态接口 ==============================

def fetch_trade_status(symbol: str, trade_date: str) -> Optional[Dict[str, Any]]:
    """
    获取股票的交易状态：is_st / is_suspended / is_limit_up / is_limit_down /
    days_listed / open_date / up_stop_price / down_stop_price 等。
    """
    xtdata = _get_xtdata()
    xt_code = to_xt_code(symbol)
    detail = xtdata.get_instrument_detail(xt_code, iscomplete=False)
    if not detail:
        return None

    name = str(detail.get('InstrumentName') or '')
    open_date = str(detail.get('OpenDate') or '')
    is_st = ('ST' in name.upper()) or name.startswith('*ST')

    # 上市天数
    days_listed = 0
    if len(open_date) == 8 and open_date.isdigit():
        try:
            open_dt = datetime.strptime(open_date, '%Y%m%d')
            ref_dt = datetime.strptime(to_yyyymmdd(trade_date), '%Y%m%d')
            days_listed = max((ref_dt - open_dt).days, 0)
        except Exception:
            days_listed = 0

    up_stop = float(detail.get('UpStopPrice', 0.0) or 0.0)
    down_stop = float(detail.get('DownStopPrice', 0.0) or 0.0)
    pre_close = float(detail.get('PreClose', 0.0) or 0.0)

    # 实时盘口判断涨停/跌停 / 停牌
    is_suspended = False
    is_limit_up = False
    is_limit_down = False
    try:
        tick = (xtdata.get_full_tick([xt_code]) or {}).get(xt_code) or {}
        last_price = float(tick.get('lastPrice', 0.0) or 0.0)
        if last_price > 0 and up_stop > 0:
            is_limit_up = abs(last_price - up_stop) < 1e-6
        if last_price > 0 and down_stop > 0:
            is_limit_down = abs(last_price - down_stop) < 1e-6
        # stockStatus: 17/20 等为停牌相关
        ss = tick.get('stockStatus')
        if ss in (17, 20):
            is_suspended = True
    except Exception as e:
        logger.debug(f"读取 {xt_code} 实时 tick 失败: {e}")

    is_trading = bool(detail.get('IsTrading', True))
    # 注：IsTrading 表示"当前可交易"，盘后/周末全部为 False，不能直接当作停牌判断；
    # 真正的停牌应通过 stockStatus（17/20）或 InstrumentStatus 判断。

    return {
        'code': normalize_code(symbol),
        'trade_date': to_yyyymmdd(trade_date),
        'name': name,
        'open_date': open_date,
        'is_st': is_st,
        'is_suspended': is_suspended,
        'is_limit_up': is_limit_up,
        'is_limit_down': is_limit_down,
        'is_trading': is_trading,
        'days_listed': days_listed,
        'up_stop_price': up_stop,
        'down_stop_price': down_stop,
        'pre_close': pre_close,
    }


def fetch_trade_status_batch(
    codes: List[str], trade_date: str,
) -> Dict[str, Dict[str, Any]]:
    """
    批量获取交易状态：get_full_tick 一次拿全市场 tick，
    get_instrument_detail 逐只取基本信息（名称/涨跌停价/上市日期）。

    Returns: {code: {trade_date, name, is_st, is_suspended, is_limit_up, ...}}
    """
    xtdata = _get_xtdata()
    td = to_yyyymmdd(trade_date)
    result: Dict[str, Dict[str, Any]] = {}

    # 构建 xt_code → 纯代码 映射
    xt_to_pure = {}
    xt_codes = []
    for c in codes:
        pure = normalize_code(c)
        xt = to_xt_code(pure)
        xt_to_pure[xt] = pure
        xt_codes.append(xt)

    # 批量拿 tick（核心优化：一次请求）
    ticks = xtdata.get_full_tick(xt_codes) or {}

    for xt_code, pure_code in xt_to_pure.items():
        try:
            detail = xtdata.get_instrument_detail(xt_code, iscomplete=False)
        except Exception:
            detail = None
        if not detail:
            continue

        name = str(detail.get('InstrumentName') or '')
        open_date = str(detail.get('OpenDate') or '')
        is_st = ('ST' in name.upper()) or name.startswith('*ST')

        days_listed = 0
        if len(open_date) == 8 and open_date.isdigit():
            try:
                open_dt = datetime.strptime(open_date, '%Y%m%d')
                ref_dt = datetime.strptime(td, '%Y%m%d')
                days_listed = max((ref_dt - open_dt).days, 0)
            except Exception:
                pass

        up_stop = float(detail.get('UpStopPrice', 0.0) or 0.0)
        down_stop = float(detail.get('DownStopPrice', 0.0) or 0.0)
        pre_close = float(detail.get('PreClose', 0.0) or 0.0)

        tick = ticks.get(xt_code) or {}
        last_price = float(tick.get('lastPrice', 0.0) or 0.0)
        is_limit_up = False
        is_limit_down = False
        is_suspended = False
        if last_price > 0 and up_stop > 0:
            is_limit_up = abs(last_price - up_stop) < 1e-6
        if last_price > 0 and down_stop > 0:
            is_limit_down = abs(last_price - down_stop) < 1e-6
        ss = tick.get('stockStatus')
        if ss in (17, 20):
            is_suspended = True

        result[pure_code] = {
            'code': pure_code,
            'trade_date': td,
            'name': name,
            'open_date': open_date,
            'is_st': is_st,
            'is_suspended': is_suspended,
            'is_limit_up': is_limit_up,
            'is_limit_down': is_limit_down,
            'is_trading': bool(detail.get('IsTrading', True)),
            'days_listed': days_listed,
            'up_stop_price': up_stop,
            'down_stop_price': down_stop,
            'pre_close': pre_close,
        }

    logger.info(f"fetch_trade_status_batch: {len(xt_codes)} 只入参, {len(result)} 只成功")
    return result


def fetch_capital_flow_batch(
    codes: List[str], trade_date: str,
) -> Dict[str, Dict[str, Any]]:
    """
    批量获取当日资金流：逐只调用 fetch_capital_flow。
    """
    result: Dict[str, Dict[str, Any]] = {}
    logger.error('xtquant fetch_capital_flow_batch 无历史资金流数据，仅当天有效，请用别的数据源。')
    return result


# ============================== 全 A 股列表 ==============================

def fetch_all_stocks(trade_date: Optional[str] = None) -> List[Dict[str, str]]:
    """
    获取全 A 股列表（沪深+北交所，含创业板/科创板 ST/B 股）。
    返回 [{'code': '000001', 'name': '平安银行'}, ...]
    """
    xtdata = _get_xtdata()

    timetag = 0
    if trade_date:
        ts = to_yyyymmdd(trade_date)
        try:
            timetag = int(ts)
        except ValueError:
            timetag = 0

    if timetag:
        raw = xtdata.get_stock_list_in_sector('沪深京A股', timetag) or []
    else:
        raw = xtdata.get_stock_list_in_sector('沪深京A股') or []

    out: List[Dict[str, str]] = []
    seen: set = set()
    total = len(raw)
    for idx, xt_code in enumerate(raw, 1):
        if not (xt_code.endswith('.SH') or xt_code.endswith('.SZ') or xt_code.endswith('.BJ')):
            continue
        c = normalize_code(xt_code)
        if c in seen:
            continue
        seen.add(c)
        try:
            d = xtdata.get_instrument_detail(xt_code, iscomplete=False)
        except Exception:
            d = None
        if not d:
            continue
        name = str(d.get('InstrumentName') or '')
        out.append({'code': c, 'name': name})

        # 每 500 只打印一次进度
        if idx % 500 == 0:
            logger.info(f"fetch_all_stocks: 处理进度 {idx}/{total}, 已获取 {len(out)} 只")
    return out
