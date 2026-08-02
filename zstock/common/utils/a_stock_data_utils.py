"""
A股资金流数据工具 — 基于东财 push2/push2his 接口
kill教程: E:/02Learn/09/a-stock-data/SKILL.md

底层接口：
  日级资金流: push2his.eastmoney.com/api/qt/stock/fflow/daykline/get
  全市场排名: push2.eastmoney.com/api/qt/clist/get
  分钟级资金流: push2.eastmoney.com/api/qt/stock/fflow/kline/get

字段说明（日级 kline）：
  f51 日期, f52 主力净流入, f53 小单净流入, f54 中单净流入
  f55 大单净流入, f56 超大单净流入

secid 规则：
  沪市 6/9 开头 → 1.xxxxxx
  深市 0/3 开头 → 0.xxxxxx
  北交所 4/8 开头 → 0.xxxxxx（东财 secid 中深市=0）

防封：所有请求走 em_get() 统一节流入口（串行限流 + 会话复用 + 自动重试）。
"""

import logging
import time
from random import random
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from .common_utils import normalize_code

logger = logging.getLogger(__name__)

# ───────────────────── 东财防封基础设施 ─────────────────────

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

EM_MIN_INTERVAL = 5.0  # 两次东财请求最小间隔(秒)
_em_last_call = [0.0]


def em_get(
    url: str,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: int = 15,
    **kwargs,
) -> requests.Response:
    """东财统一请求入口：自动节流 + 默认 UA。

    注：不使用 session（keep-alive 连接被东财服务端间歇重置后复用死连接，
    导致 RemoteDisconnected）。每次新建短连接更稳定。
    """
    wait = EM_MIN_INTERVAL - (time.time() - _em_last_call[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.5))
    default_headers = {"User-Agent": UA, "Connection": "close"}
    if headers:
        default_headers.update(headers)
    try:
        return requests.get(url, params=params, headers=default_headers, timeout=timeout, **kwargs)
    finally:
        _em_last_call[0] = time.time()


# ───────────────────── secid 映射 ─────────────────────

def _to_secid(code: str) -> str:
    """
    6 位纯数字代码 → 东财 secid 格式。

    沪市 6/9 开头 → 1.xxxxxx
    深市 0/3 开头 → 0.xxxxxx
    北交所 4/8 开头 → 0.xxxxxx
    """
    c = normalize_code(code)
    if c.startswith(("6", "9")):
        return f"1.{c}"
    else:
        # 深市(0/3) + 北交所(4/8) 在东财 secid 中均用 0 前缀
        return f"0.{c}"


# ───────────────────── 单票日级资金流 ─────────────────────

def fetch_money_flow(
    code: str,
    lmt: int = 200,
    date: Optional[str] = None,
    timeout: int = 15,
) -> pd.DataFrame:
    """
    获取单只股票的日频资金流历史。

    Args:
        code: 6 位代码（如 '600519'）或带后缀（如 '600519.SH'）
        lmt: 拉取根数，200 可覆盖约 130 个交易日
        date: 可选，指定日期过滤。支持格式：
              'YYYYMMDD'（如 '20260710'）或 'YYYY-MM-DD'（如 '2026-07-10'）
              传入后只返回该日的数据，无数据则返回空 DataFrame。
        timeout: HTTP 超时秒数

    Returns:
        DataFrame，列：trade_date(YYYY-MM-DD), main_inflow, small_inflow,
        medium_inflow, large_inflow, super_large_inflow, code
        无数据返回空 DataFrame。

    Note:
        东财 push2his 端点无 beg/end 参数，只能靠 lmt 拉全量后 Python 侧截断。
        lmt=200 可覆盖约 130 个交易日（约 6 个月）。
    """
    from zstock.common.utils.common_utils import normalize_date

    pure_code = code.split(".")[0].strip()
    secid = _to_secid(pure_code)

    url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
    params = {
        "lmt": str(lmt),
        "klt": "101",
        "secid": secid,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
    }

    r = em_get(url, params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json().get("data")
    if not data or not data.get("klines"):
        return pd.DataFrame()

    klines = data["klines"]
    cols = [
        "trade_date", "main_inflow", "small_inflow",
        "medium_inflow", "large_inflow", "super_large_inflow",
    ]

    rows = [x.split(",") for x in klines]
    df = pd.DataFrame(rows, columns=cols)

    # 转换类型
    for c in cols[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # trade_date 统一为 YYYY-MM-DD（与 MongoDB / ohlcv 一致）
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
    df["code"] = pure_code

    # 按指定日期过滤
    if date is not None:
        target = normalize_date(date)
        df = df[df["trade_date"] == target].reset_index(drop=True)

    return df


def fetch_money_flow_range(
    code: str,
    start_date: str,
    end_date: str,
    lmt: int = 200,
    timeout: int = 15,
) -> pd.DataFrame:
    """
    获取单只股票在指定日期范围内的日频资金流。

    Args:
        code: 6 位代码
        start_date: 起始日期，'YYYYMMDD' 或 'YYYY-MM-DD'（含）
        end_date: 截止日期，'YYYYMMDD' 或 'YYYY-MM-DD'（含）
        lmt: 拉取根数（需足够覆盖日期范围，默认 200 约 130 个交易日）
        timeout: HTTP 超时秒数

    Returns:
        DataFrame，同 fetch_money_flow 格式，按 trade_date 升序。
        指定范围内无数据返回空 DataFrame。

    Note:
        东财 push2his 最多返回约 120 个交易日数据。超出范围的日期无法获取。
    """
    from zstock.common.utils.common_utils import normalize_date

    df = fetch_money_flow(code, lmt=lmt, timeout=timeout)
    if df.empty:
        return df

    s = normalize_date(start_date)
    e = normalize_date(end_date)
    mask = (df["trade_date"] >= s) & (df["trade_date"] <= e)
    return df.loc[mask].reset_index(drop=True)


# ───────────────────── 批量资金流 ─────────────────────

def fetch_money_flow_batch(
    codes: List[str],
    lmt: int = 200,
    delay: float = 1.2,
    date: Optional[str] = None,
    progress_every: int = 50,
) -> pd.DataFrame:
    """
    批量获取多只股票的日频资金流。

    Args:
        codes: 6 位代码列表
        lmt: 每只拉取根数
        delay: 请求间隔秒数（≥1.2 保证在 em_get 限流之上）
        date: 可选，指定日期过滤（'YYYYMMDD' 或 'YYYY-MM-DD'）
        progress_every: 每 N 只打印一次进度

    Returns:
        合并后的 DataFrame（同 fetch_money_flow 格式）
    """
    frames = []
    failed = []

    for i, code in enumerate(codes):
        try:
            df = fetch_money_flow(code, lmt=lmt, date=date)
            if not df.empty:
                frames.append(df)
        except Exception as e:
            failed.append(code)
            logger.warning("  ✗ %s: %s", code, e)

        if (i + 1) % progress_every == 0:
            logger.info(
                "  进度 %d/%d  成功 %d  失败 %d",
                i + 1, len(codes), len(frames), len(failed),
            )

        time.sleep(delay)

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    logger.info(
        "  ✓ 批量资金流完成: %d 行, %d 只成功, %d 只失败",
        len(result), len(frames), len(failed),
    )
    return result


# ───────────────────── 全市场资金流排名（单次请求分页） ─────────────────────

# 东财排名接口不同时间窗口的字段映射
_PERIOD_FIELDS = {
    "today": {
        "fid": "f62",
        "main": "f62",
        "super_large": "f66",
        "large": "f72",
        "medium": "f78",
        "small": "f84",
        "main_pct": "f184",
    },
    "3day": {
        "fid": "f267",
        "main": "f267",
        "super_large": "f268",
        "large": "f269",
        "medium": "f270",
        "small": "f271",
        "main_pct": "f268",
    },
    "5day": {
        "fid": "f164",
        "main": "f164",
        "super_large": "f165",
        "large": "f166",
        "medium": "f167",
        "small": "f168",
        "main_pct": "f165",
    },
    "10day": {
        "fid": "f174",
        "main": "f174",
        "super_large": "f175",
        "large": "f176",
        "medium": "f177",
        "small": "f178",
        "main_pct": "f175",
    },
}


def fetch_money_flow_all(
    period: str = "today",
    timeout: int = 30,
) -> pd.DataFrame:
    """
    分页拉取全市场所有股票的当前资金流排名。

    Args:
        period: 'today' / '3day' / '5day' / '10day'
        timeout: HTTP 超时秒数

    Returns:
        DataFrame，列：code, name, main_inflow, super_large_inflow,
        large_inflow, medium_inflow, small_inflow, main_pct, period, trade_date
    """
    from datetime import datetime

    pf = _PERIOD_FIELDS.get(period)
    if not pf:
        raise ValueError(f"不支持的 period: {period}，可选: {list(_PERIOD_FIELDS.keys())}")

    url = "https://push2.eastmoney.com/api/qt/clist/get"
    headers = {
        "Referer": "http://data.eastmoney.com/",
    }
    fields = (
        f"f12,f14,{pf['main']},{pf['super_large']},"
        f"{pf['large']},{pf['medium']},{pf['small']},{pf['main_pct']}"
    )
    base_params = {
        "pz": 500, "po": 1, "np": 1,
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "fltt": 2, "invt": 2,
        "fid": pf["fid"],
        "fs": (
            "m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,"
            "m:1+t:2+f:!2,m:1+t:23+f:!2,"
            "m:0+t:7+f:!2,m:1+t:3+f:!2"
        ),
        "fields": fields,
    }

    all_rows = []
    pn = 1
    while True:
        params = {**base_params, "pn": pn}
        r = em_get(url, params=params, headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json().get("data", {})
        if not data:
            break
        rows = data.get("diff", [])
        if not rows:
            break
        all_rows.extend(rows)
        total = data.get("total", 0)
        if len(all_rows) >= total:
            break
        pn += 1

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df = df.rename(columns={
        "f12": "code",
        "f14": "name",
        pf["main"]: "main_inflow",
        pf["super_large"]: "super_large_inflow",
        pf["large"]: "large_inflow",
        pf["medium"]: "medium_inflow",
        pf["small"]: "small_inflow",
        pf["main_pct"]: "main_pct",
    })
    df["period"] = period
    df["trade_date"] = datetime.now().strftime("%Y-%m-%d")

    keep = [
        "code", "name", "trade_date", "period",
        "main_inflow", "super_large_inflow", "large_inflow",
        "medium_inflow", "small_inflow", "main_pct",
    ]
    df = df[[c for c in keep if c in df.columns]]

    return df


# ───────────────────── 分钟级实时资金流 ─────────────────────

def fetch_money_flow_minute(code: str) -> List[Dict[str, Any]]:
    """
    个股分钟级实时资金流（当日盘中）。

    Args:
        code: 6 位股票代码

    Returns:
        [{time, main_net, small_net, mid_net, large_net, super_net}, ...]
        单位: 元
    """
    secid = _to_secid(code)
    url = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
    params = {
        "secid": secid,
        "klt": 1,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
    }
    headers = {
        "Referer": "https://quote.eastmoney.com/",
        "Origin": "https://quote.eastmoney.com",
    }

    try:
        r = em_get(url, params=params, headers=headers, timeout=10)
        d = r.json()
    except Exception as e:
        logger.warning("push2 分钟级资金流请求失败 %s: %s", code, e)
        return []

    rows = []
    for line in d.get("data", {}).get("klines", []):
        parts = line.split(",")
        if len(parts) >= 6:
            try:
                rows.append({
                    "time": parts[0],
                    "main_net": float(parts[1]),
                    "small_net": float(parts[2]),
                    "mid_net": float(parts[3]),
                    "large_net": float(parts[4]),
                    "super_net": float(parts[5]),
                })
            except (ValueError, TypeError):
                continue
    return rows
