# -*- coding: utf-8 -*-
"""通过 QMT 自带 Python 3.6 子进程调用 xtquant（兼容主环境 Python 3.14）。"""
from __future__ import annotations

import json
import logging
import os
import pickle
import subprocess
import tempfile
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

WORKER_SCRIPT = Path(__file__).resolve().parent / 'qmt_xtdata_worker.py'
DEFAULT_QMT_PYTHON = r'D:\GJZQqmt\国金证券QMT交易端\bin.x64\pythonw.exe'
# 单次子进程股票数上限，避免 pickle / QMT 一次吃太多内存
OHLCV_SUBPROCESS_BATCH = 80
FIN_SUBPROCESS_BATCH = 60
FLOAT_SHARES_BATCH = 200


def get_qmt_python() -> str:
    return os.environ.get('QMT_PYTHON', DEFAULT_QMT_PYTHON)


def qmt_call(method: str, timeout: int = 7200, **kwargs) -> Any:
    qmt_py = get_qmt_python()
    if not Path(qmt_py).is_file():
        raise RuntimeError(f'QMT Python 不存在: {qmt_py}')
    if not WORKER_SCRIPT.is_file():
        raise RuntimeError(f'worker 脚本不存在: {WORKER_SCRIPT}')

    with tempfile.TemporaryDirectory(prefix='qmt_xt_') as tmp:
        payload_path = os.path.join(tmp, 'payload.json')
        result_path = os.path.join(tmp, 'result.pkl')
        with open(payload_path, 'w', encoding='utf-8') as f:
            json.dump({'method': method, 'kwargs': kwargs}, f, ensure_ascii=False)

        proc = subprocess.run(
            [qmt_py, str(WORKER_SCRIPT), payload_path, result_path],
            capture_output=True,
            timeout=timeout,
        )
        if not os.path.isfile(result_path):
            stderr = proc.stderr.decode('utf-8', errors='replace') if proc.stderr else ''
            raise RuntimeError(
                f'QMT worker 无结果 (exit={proc.returncode}): {stderr or proc.stdout}'
            )
        with open(result_path, 'rb') as f:
            payload = pickle.load(f)
        if not payload.get('ok'):
            raise RuntimeError(payload.get('error') or 'QMT worker failed')
        return payload.get('result')


class SubprocessXtDataProxy:
    """sync_fundamental 直接调 xtdata 时的子进程代理。"""

    enable_hello = False

    def download_financial_data2(self, xt_codes, table_list, start_time='', end_time=''):
        codes = list(xt_codes or [])
        last = None
        for i in range(0, max(len(codes), 1), FIN_SUBPROCESS_BATCH):
            chunk = codes[i:i + FIN_SUBPROCESS_BATCH]
            if not chunk:
                break
            last = qmt_call(
                'download_financial_data2',
                timeout=1800,
                xt_codes=chunk,
                table_list=table_list,
                start_time=start_time,
                end_time=end_time,
            )
        return last

    def get_financial_data(self, xt_codes, table_list, start_time='', end_time='', report_type='announce_time'):
        codes = list(xt_codes or [])
        out = {}
        for i in range(0, max(len(codes), 1), FIN_SUBPROCESS_BATCH):
            chunk = codes[i:i + FIN_SUBPROCESS_BATCH]
            if not chunk:
                break
            part = qmt_call(
                'get_financial_data',
                timeout=1800,
                xt_codes=chunk,
                table_list=table_list,
                start_time=start_time,
                end_time=end_time,
                report_type=report_type,
            )
            if part:
                out.update(part)
        return out


def patch_xtquant_data_utils() -> None:
    """在主进程导入 sync 脚本前调用，注入 QMT 子进程回源。"""
    from zstock.common.utils import xtquant_data_utils as xtu

    proxy = SubprocessXtDataProxy()

    def _get_xtdata_subprocess():
        return proxy

    def fetch_all_stocks_subprocess(trade_date: Optional[str] = None):
        return qmt_call('fetch_all_stocks', trade_date=trade_date)

    def fetch_ohlcv_subprocess(symbol: str, start_date: str, end_date: str, dividend_type: str = 'front'):
        df = qmt_call('fetch_ohlcv', symbol=symbol, start_date=start_date,
                      end_date=end_date, dividend_type=dividend_type)
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()

    def fetch_ohlcv_batch_subprocess(symbols: List[str], start_date: str, end_date: str,
                                     dividend_type: str = 'front'):
        if not symbols:
            return pd.DataFrame()
        frames: List[pd.DataFrame] = []
        for i in range(0, len(symbols), OHLCV_SUBPROCESS_BATCH):
            chunk = symbols[i:i + OHLCV_SUBPROCESS_BATCH]
            df = qmt_call(
                'fetch_ohlcv_batch',
                timeout=90,
                symbols=chunk,
                start_date=start_date,
                end_date=end_date,
                dividend_type=dividend_type,
            )
            if isinstance(df, pd.DataFrame) and not df.empty:
                frames.append(df)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def fetch_float_shares_map_subprocess(codes: List[str]) -> Dict[str, float]:
        result: Dict[str, float] = {}
        for i in range(0, len(codes), FLOAT_SHARES_BATCH):
            part = qmt_call(
                'fetch_float_shares_map',
                timeout=600,
                codes=codes[i:i + FLOAT_SHARES_BATCH],
            )
            if part:
                result.update(part)
        return result

    def fetch_sectors_with_stocks_subprocess(sector_type: str = 'all', a_stock_only: bool = True):
        return qmt_call(
            'fetch_sectors_with_stocks',
            timeout=3600,
            sector_type=sector_type,
            a_stock_only=a_stock_only,
        )

    xtu._get_xtdata = _get_xtdata_subprocess
    xtu.fetch_all_stocks = fetch_all_stocks_subprocess
    xtu.fetch_ohlcv = fetch_ohlcv_subprocess
    xtu.fetch_ohlcv_batch = fetch_ohlcv_batch_subprocess
    xtu.fetch_float_shares_map = fetch_float_shares_map_subprocess
    xtu.fetch_sectors_with_stocks = fetch_sectors_with_stocks_subprocess
    logger.info('已启用 QMT 子进程 xtquant 桥接: %s', get_qmt_python())
