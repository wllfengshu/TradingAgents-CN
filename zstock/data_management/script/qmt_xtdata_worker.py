# -*- coding: utf-8 -*-
"""QMT Python 3.6 子进程 worker：供主环境(3.11+) 通过 xtquant 拉取数据。"""
import json
import pickle
import sys
import traceback


def normalize_code(symbol):
    normalized = (symbol or '').strip().upper()
    if '.' in normalized:
        normalized = normalized.split('.', 1)[0]
    for prefix in ('SH', 'SZ', 'BJ'):
        if normalized.startswith(prefix):
            return normalized[len(prefix):]
    return normalized


def to_xt_code(code):
    c = normalize_code(code)
    if not c:
        return c
    head = c[0]
    if head == '6':
        return c + '.SH'
    if head in ('0', '3'):
        return c + '.SZ'
    if head in ('4', '8'):
        return c + '.BJ'
    if head == '9' and c[:3] == '920':
        return c + '.BJ'
    if head == '9':
        return c + '.SH'
    if head == '2':
        return c + '.SZ'
    return c + '.SH'


def to_yyyymmdd(date_str):
    return (date_str or '').replace('-', '').replace('/', '').strip()[:8]


def _get_xtdata():
    from xtquant import xtdata
    try:
        xtdata.enable_hello = False
    except Exception:
        pass
    # 交易端行情口常见为 58600，独立 miniQMT 为 58610
    try:
        xtdata.get_client()
        return xtdata
    except Exception:
        pass
    for port in (58600, 58610, 58611):
        try:
            xtdata.reconnect('localhost', port)
            xtdata.get_client()
            return xtdata
        except Exception:
            continue
    return xtdata


def _build_ohlcv_df(xtdata, xt_code, symbol, raw_df):
    import pandas as pd
    df = raw_df.copy()
    df.index = pd.to_datetime(df.index.astype(str), format='%Y%m%d', errors='coerce')
    df = df[df.index.notna()]
    df['trade_date'] = df.index.strftime('%Y-%m-%d')
    df['code'] = normalize_code(symbol)
    detail = xtdata.get_instrument_detail(xt_code, iscomplete=False)
    df['name'] = str(detail.get('InstrumentName') or '') if detail else ''
    cols = ['code', 'name', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount']
    for opt in ('preClose', 'suspendFlag', 'floatVolume'):
        if opt in df.columns:
            cols.append(opt)
    return df[[c for c in cols if c in df.columns]].reset_index(drop=True)


def fetch_all_stocks(trade_date=None):
    xtdata = _get_xtdata()
    timetag = 0
    if trade_date:
        try:
            timetag = int(to_yyyymmdd(trade_date))
        except ValueError:
            timetag = 0
    if timetag:
        raw = xtdata.get_stock_list_in_sector('沪深京A股', timetag) or []
    else:
        raw = xtdata.get_stock_list_in_sector('沪深京A股') or []
    out = []
    seen = set()
    for xt_code in raw:
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
        out.append({'code': c, 'name': str(d.get('InstrumentName') or '')})
    return out


def _download_history(xtdata, xt_code, st, et):
    try:
        xtdata.download_history_data(xt_code, period='1d', start_time=st, end_time=et, incrementally=True)
    except TypeError:
        xtdata.download_history_data(xt_code, period='1d', start_time=st, end_time=et)


def _download_history2(xtdata, xt_codes, st, et):
    try:
        xtdata.download_history_data2(xt_codes, period='1d', start_time=st, end_time=et, incrementally=True)
    except TypeError:
        try:
            xtdata.download_history_data2(xt_codes, period='1d', start_time=st, end_time=et)
        except Exception:
            for xt_code in xt_codes:
                _download_history(xtdata, xt_code, st, et)


def fetch_ohlcv(symbol, start_date, end_date, dividend_type='front'):
    import pandas as pd
    xtdata = _get_xtdata()
    xt_code = to_xt_code(symbol)
    st, et = to_yyyymmdd(start_date), to_yyyymmdd(end_date)
    _download_history(xtdata, xt_code, st, et)
    data = xtdata.get_market_data_ex([], [xt_code], period='1d', start_time=st, end_time=et,
                                     count=-1, dividend_type=dividend_type, fill_data=False)
    raw = data.get(xt_code) if data else None
    if raw is None or len(raw) == 0:
        return pd.DataFrame()
    return _build_ohlcv_df(xtdata, xt_code, symbol, raw)


DOWNLOAD_CHUNK = 40
READ_CHUNK = 80
FIN_CHUNK = 50


def fetch_ohlcv_batch(symbols, start_date, end_date, dividend_type='front'):
    import pandas as pd
    if not symbols:
        return pd.DataFrame()
    xtdata = _get_xtdata()
    xt_codes = [to_xt_code(s) for s in symbols]
    st, et = to_yyyymmdd(start_date), to_yyyymmdd(end_date)
    for i in range(0, len(xt_codes), DOWNLOAD_CHUNK):
        _download_history2(xtdata, xt_codes[i:i + DOWNLOAD_CHUNK], st, et)
    frames = []
    for i in range(0, len(xt_codes), READ_CHUNK):
        part_xt = xt_codes[i:i + READ_CHUNK]
        part_sym = symbols[i:i + READ_CHUNK]
        data = xtdata.get_market_data_ex(
            [], part_xt, period='1d', start_time=st, end_time=et,
            count=-1, dividend_type=dividend_type, fill_data=False,
        )
        if not data:
            continue
        for xt_code, symbol in zip(part_xt, part_sym):
            raw = data.get(xt_code)
            if raw is None or len(raw) == 0:
                continue
            frames.append(_build_ohlcv_df(xtdata, xt_code, symbol, raw))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def fetch_float_shares_map(codes):
    xtdata = _get_xtdata()
    result = {}
    for c in codes:
        pure = normalize_code(c)
        xt_code = to_xt_code(pure)
        try:
            detail = xtdata.get_instrument_detail(xt_code, iscomplete=False)
        except Exception:
            detail = None
        if not detail:
            continue
        fv = detail.get('FloatVolume') or detail.get('FloatVolumn') or 0
        try:
            fv = float(fv)
        except (TypeError, ValueError):
            continue
        if fv > 0:
            result[pure] = fv
    return result


def _ensure_sector_data(xtdata):
    try:
        client = xtdata.get_client()
        client.down_all_sector_data()
        return
    except Exception:
        pass
    try:
        xtdata.download_sector_data()
    except Exception:
        pass


def _classify_sector(name):
    if '概念' in name:
        return 'concept'
    if '指数' in name:
        return 'index'
    if any(k in name for k in ('A股', '主板', '创业板', '科创板', '北交所')):
        return 'board'
    if any(k in name for k in ('上期所', '中金所', '大商所', '郑商所', '能源中心',
                               '广期所', '上金所', '上期能源')):
        return 'exchange'
    if any(k in name for k in ('B股', 'ETF', '债券', '基金', '期权', '转债')):
        return 'board_non_a'
    return 'other'


def _is_a_stock_sector(name, st):
    import re
    if st in ('exchange', 'board_non_a'):
        return False
    exclude = ('B股', '期权', '债券', '基金', 'ETF', '转债', '期货',
               '港股', '美股', '新三板', '回购',
               '上期所', '中金所', '大商所', '郑商所', '能源中心',
               '广期所', '上金所', '上期能源')
    if any(k in name for k in exclude):
        return False
    if re.match(r'^\d+(SW|THY)\d', name):
        return False
    if name.endswith('加权'):
        return False
    return True


def fetch_sectors_with_stocks(sector_type='all', a_stock_only=True):
    """一次下载板块分类，返回板块元数据 + 成分股，避免主进程逐板块开子进程。"""
    xtdata = _get_xtdata()
    _ensure_sector_data(xtdata)
    names = xtdata.get_sector_list() or []
    result = []
    for name in names:
        st = _classify_sector(name)
        if a_stock_only and not _is_a_stock_sector(name, st):
            continue
        if sector_type != 'all' and st != sector_type:
            continue
        try:
            raw = xtdata.get_stock_list_in_sector(name) or []
        except Exception:
            raw = []
        stocks = []
        seen = set()
        for xt_code in raw:
            c = normalize_code(xt_code)
            if not c or c in seen:
                continue
            seen.add(c)
            stocks.append(c)
        result.append({
            'sector_code': name,
            'sector_name': name,
            'sector_type': st,
            'stocks': stocks,
        })
    return result


def download_financial_data2(xt_codes, table_list, start_time, end_time):
    xtdata = _get_xtdata()
    last = None
    codes = list(xt_codes or [])
    for i in range(0, len(codes), FIN_CHUNK):
        last = xtdata.download_financial_data2(
            codes[i:i + FIN_CHUNK], table_list=table_list,
            start_time=start_time, end_time=end_time,
        )
    return last


def get_financial_data(xt_codes, table_list, start_time, end_time, report_type):
    xtdata = _get_xtdata()
    out = {}
    codes = list(xt_codes or [])
    for i in range(0, len(codes), FIN_CHUNK):
        part = xtdata.get_financial_data(
            codes[i:i + FIN_CHUNK], table_list=table_list,
            start_time=start_time, end_time=end_time,
            report_type=report_type,
        )
        if part:
            out.update(part)
    return out


HANDLERS = {
    'fetch_all_stocks': fetch_all_stocks,
    'fetch_ohlcv': fetch_ohlcv,
    'fetch_ohlcv_batch': fetch_ohlcv_batch,
    'fetch_float_shares_map': fetch_float_shares_map,
    'fetch_sectors_with_stocks': fetch_sectors_with_stocks,
    'download_financial_data2': download_financial_data2,
    'get_financial_data': get_financial_data,
}


def main():
    if len(sys.argv) < 3:
        sys.stderr.write('usage: qmt_xtdata_worker.py <payload.json> <result.pkl>\n')
        sys.exit(2)
    payload_path, result_path = sys.argv[1], sys.argv[2]
    with open(payload_path, 'r', encoding='utf-8') as f:
        payload = json.load(f)
    method = payload.get('method')
    kwargs = payload.get('kwargs') or {}
    handler = HANDLERS.get(method)
    if handler is None:
        raise ValueError('unknown method: %s' % method)
    try:
        result = handler(**kwargs)
        with open(result_path, 'wb') as f:
            pickle.dump({'ok': True, 'result': result}, f, protocol=2)
    except Exception:
        with open(result_path, 'wb') as f:
            pickle.dump({'ok': False, 'error': traceback.format_exc()}, f, protocol=2)
        sys.exit(1)


if __name__ == '__main__':
    main()
