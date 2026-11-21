"""
stockdb 行情与资金流。

本地库默认 127.0.0.1:7899，SDK 在 D:\\stockdb\\pybao（stock_sdk.py）。
日线、分钟线走 rd.get_data，一次可传代码列表。
资金流走本地 rd.get(\"资金流\", code, YYYYMMDD).do()（见 a.123128.xyz 文档「股票资金流向【本地批量】」）。
本地净额单位是元，这里换算成万元，与现有因子 / Mongo 约定一致。
不要用远端 get_money_flow（会打到 8.138.x:12328）。

数据更新不是查询接口。官方客户端是 D:\\stockdb\\数据更新.exe（GUI，v0.3.5）：
读 sync_url.txt，按 .sync_manifest.json 从数据源拉增量，结束后请求
http://127.0.0.1:7899/?cmd=reload&t= 。update_data() 用来代替手动双击。

现金流量表 cash_flow 不是这里的资金流。
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence

import pandas as pd

from zstock.common.utils.common_utils import WAN_TO_YUAN, normalize_code, normalize_date

logger = logging.getLogger(__name__)

_DEFAULT_PYBAO = os.environ.get("STOCKDB_PYBAO", r"D:\stockdb\pybao")
_DEFAULT_STOCKDB_DIR = os.environ.get("STOCKDB_DIR", r"D:\stockdb")
_UPDATER_EXE_NAME = "数据更新.exe"
_FLOW_WORKERS = int(os.environ.get("STOCKDB_FLOW_WORKERS", "8"))
_RUN_AT_RE = re.compile(r"^(?:\d{4}|\d{6}|\d{2}:\d{2}|\d{2}:\d{2}:\d{2})$")

_BAR_FIELDS = {
    "date": "trade_date",
    "trade_date": "trade_date",
    "datetime": "trade_date",
    "time": "trade_date",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
    "vol": "volume",
    "amount": "amount",
    "money": "amount",
    "pre_close": "pre_close",
    "pct_chg": "pct_chg",
    "amplitude": "amplitude",
}


def _normalize_bar_timestamp(value) -> str:
    """日线 → YYYY-MM-DD；分钟线 → YYYY-MM-DD HH:MM:SS。

    stockdb 分钟线 date 常见为 int/str：20260916093500。
    """
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        try:
            # pandas/Timestamp / datetime
            if getattr(value, "hour", None) is not None or " " in str(value):
                return value.strftime("%Y-%m-%d %H:%M:%S")
            return value.strftime("%Y-%m-%d")
        except Exception:
            pass
    digits = "".join(ch for ch in str(value).strip() if ch.isdigit())
    if len(digits) >= 14:
        return (
            f"{digits[:4]}-{digits[4:6]}-{digits[6:8]} "
            f"{digits[8:10]}:{digits[10:12]}:{digits[12:14]}"
        )
    if len(digits) == 12:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]} {digits[8:10]}:{digits[10:12]}:00"
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return normalize_date(str(value))



class StockDbError(RuntimeError):
    """stockdb 不可用或返回了业务错误。"""


class StockDbClient:
    """stock_sdk 的薄封装。sdk 可注入，便于测试。"""

    def __init__(
        self,
        pybao_dir: Optional[str] = None,
        host: str = "127.0.0.1",
        port: int = 7899,
        sdk=None,
    ):
        self.pybao_dir = pybao_dir or _DEFAULT_PYBAO
        self.host = host
        self.port = port
        self._sdk = sdk
        self._ready = sdk is not None

    def connect(self) -> bool:
        if self._sdk is None:
            self._sdk = _load_sdk(self.pybao_dir)
        init = getattr(self._sdk, "init", None)
        if init is not None:
            init(host=self.host, port=self.port)
        self._ready = True
        logger.info("stockdb 已配置: %s:%s sdk=%s", self.host, self.port, self.pybao_dir)
        return True

    def _require(self):
        if not self._ready or self._sdk is None:
            raise StockDbError("stockdb 未连接，请先调用 connect()")
        return self._sdk

    def get_bars(
        self,
        codes: Sequence[str],
        start: str,
        end: str,
        frequency: str = "1d",
        fq: str = "qfq",
    ) -> Dict[str, pd.DataFrame]:
        """批量 K 线。返回 {6位代码: DataFrame}，列含 trade_date/open/high/low/close/volume。"""
        sdk = self._require()
        symbols = [normalize_code(code) for code in codes if normalize_code(code)]
        if not symbols:
            return {}
        # 本地 stockdb 多码一次 get_data 常返回空 list；单码稳定。
        # 因此按单码拉取，再拼结果（同步层仍可按批切股票列表）。
        out: Dict[str, pd.DataFrame] = {}
        start_c = _compact_date(start)
        end_c = _compact_date(end)
        for code in symbols:
            raw = sdk.rd.get_data(
                [code],
                start=start_c,
                end=end_c,
                frequency=frequency,
                fq=fq,
                as_df=False,
            )
            grouped = _group_bars(raw, [code])
            out[code] = _bars_frame(code, grouped.get(code) or [])
        return out

    def get_money_flow(
        self,
        codes: Sequence[str],
        start: str,
        end: str,
    ) -> Dict[str, pd.DataFrame]:
        """本地批量资金流。返回 main_net / xl_net 等，单位万元。"""
        sdk = self._require()
        rd = getattr(sdk, "rd", None)
        if rd is None:
            raise StockDbError("stock_sdk 无 rd，无法读本地资金流")
        symbols = [normalize_code(code) for code in codes if normalize_code(code)]
        if not symbols:
            return {}
        days = _trade_days(sdk, start, end)
        if not days:
            return {code: _empty_flow() for code in symbols}

        frames: Dict[str, List[dict]] = {code: [] for code in symbols}
        tasks = [(code, day) for code in symbols for day in days]
        workers = max(1, min(_FLOW_WORKERS, len(tasks)))

        def _one(code: str, day: str):
            raw = rd.get("资金流", code, day).do()
            if not raw:
                return code, None
            if isinstance(raw, dict) and raw.get("error"):
                raise StockDbError(str(raw["error"]))
            if isinstance(raw, list):
                return code, [_local_flow_row(code, row) for row in raw if isinstance(row, dict)]
            if isinstance(raw, dict):
                return code, [_local_flow_row(code, raw)]
            return code, None

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_one, code, day) for code, day in tasks]
            for fut in as_completed(futures):
                code, rows = fut.result()
                if rows:
                    frames[code].extend(rows)

        out: Dict[str, pd.DataFrame] = {}
        for code, rows in frames.items():
            if not rows:
                out[code] = _empty_flow()
                continue
            frame = pd.DataFrame(rows)
            if "trade_date" in frame.columns:
                frame = frame.drop_duplicates(subset=["trade_date"], keep="last")
                frame = frame.sort_values("trade_date").reset_index(drop=True)
            out[code] = frame
        return out

    def update_data(self, **kwargs) -> dict:
        """拉取最新行情文件。stockdb 目录默认取 pybao 的上一级。"""
        if not kwargs.get("stockdb_dir"):
            kwargs["stockdb_dir"] = os.path.dirname(os.path.abspath(self.pybao_dir))
        return update_data(**kwargs)


def update_data(
    stockdb_dir: Optional[str] = None,
    *,
    run_at: Optional[str] = None,
    source_url: Optional[str] = None,
    threads: Optional[int] = None,
    retries: Optional[int] = None,
    secret: Optional[str] = None,
    verbose_ip: bool = False,
    no_keep_old: bool = False,
    wait: bool = True,
    timeout: Optional[float] = None,
) -> dict:
    """启动官方数据更新客户端，代替手动双击 数据更新.exe。

    这个程序是同步客户端，不是 HTTP 服务。它读取同目录 sync_url.txt
    （也可用 source_url / --url 覆盖），下载有变化的库文件，再通知本机
    stockdb 执行 /?cmd=reload。

    run_at 对应 -run，格式 HHMM、HH:MM、HHMMSS 或 HH:MM:SS，进程会停到下次同步。
    wait=False 时立即返回 pid，窗口自己跑完。超时不会杀掉进程，避免截断下载。
    """
    directory = stockdb_dir or _DEFAULT_STOCKDB_DIR
    argv = _updater_argv(
        directory,
        run_at=run_at,
        source_url=source_url,
        threads=threads,
        retries=retries,
        secret=secret,
        verbose_ip=verbose_ip,
        no_keep_old=no_keep_old,
    )
    logger.info("启动 stockdb 数据更新: %s", _redact_argv(argv))
    popen_kwargs = {"cwd": directory, "args": argv}
    if wait:
        popen_kwargs["stdout"] = subprocess.PIPE
        popen_kwargs["stderr"] = subprocess.PIPE
    elif os.name == "nt":
        popen_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
        popen_kwargs["stdout"] = subprocess.DEVNULL
        popen_kwargs["stderr"] = subprocess.DEVNULL
        popen_kwargs["stdin"] = subprocess.DEVNULL
    proc = subprocess.Popen(**popen_kwargs)
    result = {
        "exe": argv[0],
        "args": argv[1:],
        "pid": proc.pid,
        "cwd": directory,
        "running": True,
        "returncode": None,
        "stdout": "",
        "stderr": "",
    }
    if not wait:
        return result
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        result["stdout"] = _decode_output(exc.stdout)
        result["stderr"] = _decode_output(exc.stderr)
        logger.info("数据更新仍在运行 pid=%s", proc.pid)
        return result
    result["running"] = False
    result["returncode"] = proc.returncode
    result["stdout"] = _decode_output(out)
    result["stderr"] = _decode_output(err)
    logger.info("数据更新结束 pid=%s code=%s", proc.pid, proc.returncode)
    return result


def _updater_argv(
    stockdb_dir: str,
    *,
    run_at: Optional[str],
    source_url: Optional[str],
    threads: Optional[int],
    retries: Optional[int],
    secret: Optional[str],
    verbose_ip: bool,
    no_keep_old: bool,
) -> List[str]:
    exe = os.path.join(stockdb_dir, _UPDATER_EXE_NAME)
    if not os.path.isfile(exe):
        raise StockDbError(f"找不到数据更新程序: {exe}")
    argv = [exe]
    if run_at:
        text = run_at.strip()
        if not _RUN_AT_RE.fullmatch(text):
            raise StockDbError("-run 需要 HHMM、HH:MM、HHMMSS 或 HH:MM:SS")
        argv.extend(["-run", text])
    if source_url:
        argv.extend(["--url", source_url])
    if threads is not None:
        argv.extend(["--threads", str(int(threads))])
    if retries is not None:
        argv.extend(["--retries", str(int(retries))])
    if secret:
        argv.extend(["--secret", secret])
    if verbose_ip:
        argv.append("--verbose-ip")
    if no_keep_old:
        argv.append("--no-keep-old")
    return argv


def _redact_argv(argv: Sequence[str]) -> List[str]:
    safe: List[str] = []
    hide_next = False
    for item in argv:
        if hide_next:
            safe.append("***")
            hide_next = False
            continue
        safe.append(item)
        if item == "--secret":
            hide_next = True
    return safe


def _decode_output(data) -> str:
    if not data:
        return ""
    if isinstance(data, str):
        return data
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _load_sdk(pybao_dir: str):
    if pybao_dir and pybao_dir not in sys.path:
        sys.path.insert(0, pybao_dir)
    try:
        import stock_sdk
    except ImportError as e:
        raise StockDbError(
            f"无法导入 stock_sdk。确认 STOCKDB_PYBAO 指向 pybao 目录，当前为 {pybao_dir}"
        ) from e
    return stock_sdk


def _compact_date(value: str) -> str:
    return normalize_date(value).replace("-", "")


def _group_bars(raw, symbols: Sequence[str]) -> Dict[str, list]:
    if isinstance(raw, dict) and "error" in raw and len(raw) == 1:
        raise StockDbError(str(raw["error"]))
    if isinstance(raw, dict):
        out = {}
        for code in symbols:
            rows = raw.get(code) or []
            # SDK 部分版本对代码做了去前导零处理（如 "000001" → "1"），
            # 用 int 比对而非 lstrip("0")，避免 "000010" 被截成 "10" 后误匹配 "0010"。
            if not rows:
                for key in raw:
                    try:
                        if int(key) == int(code):
                            rows = raw[key]
                            break
                    except (ValueError, TypeError):
                        continue
            out[code] = list(rows) if isinstance(rows, list) else _as_records(rows)
        return out
    if len(symbols) == 1:
        return {symbols[0]: _as_records(raw)}
    raise StockDbError("rd.get_data 批量返回值不是按代码分组的字典")


def _as_records(payload) -> List[dict]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if hasattr(payload, "to_dict"):
        rows = payload.to_dict(orient="records")
        return [row for row in rows if isinstance(row, dict)]
    return []


def _bars_frame(code: str, rows: list) -> pd.DataFrame:
    records = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = {"code": code}
        for key, value in row.items():
            target = _BAR_FIELDS.get(str(key))
            if target and target not in item:
                item[target] = value
        if "trade_date" in item:
            item["trade_date"] = _normalize_bar_timestamp(item["trade_date"])
        records.append(item)
    frame = pd.DataFrame(records)
    if frame.empty:
        return pd.DataFrame(
            columns=["code", "trade_date", "open", "high", "low", "close", "volume", "amount"]
        )
    return frame


def _trade_days(sdk, start: str, end: str) -> List[str]:
    """返回 YYYYMMDD 交易日列表。

    优先用本地 rd.get_data 抽日历，避免 get_trade_days 走远端 12328。
    """
    start_n = normalize_date(start)
    end_n = normalize_date(end)
    days: List[str] = []
    rd = getattr(sdk, "rd", None)
    if rd is not None and hasattr(rd, "get_data"):
        try:
            raw = rd.get_data(
                ["000001"],
                start=_compact_date(start_n),
                end=_compact_date(end_n),
                frequency="1d",
                fq="qfq",
                as_df=False,
            )
            rows = []
            if isinstance(raw, dict):
                rows = raw.get("000001") or raw.get("1") or []
                if not rows:
                    for key, val in raw.items():
                        try:
                            if int(key) == 1:
                                rows = val
                                break
                        except (TypeError, ValueError):
                            continue
            elif isinstance(raw, list):
                rows = raw
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                digits = "".join(
                    ch for ch in str(row.get("date") or row.get("trade_date") or "") if ch.isdigit()
                )
                if len(digits) >= 8:
                    days.append(digits[:8])
        except Exception as e:
            logger.warning("本地交易日抽取失败，改用自然日: %s", e)
    if days:
        return sorted(set(days))
    start_dt = datetime.strptime(start_n, "%Y-%m-%d")
    end_dt = datetime.strptime(end_n, "%Y-%m-%d")
    cur = start_dt
    while cur <= end_dt:
        if cur.weekday() < 5:
            days.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return days


def _num_field(row: dict, *keys: str) -> float:
    for key in keys:
        if key not in row:
            continue
        value = row.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _local_flow_row(code: str, row: dict) -> dict:
    """本地 rd.get(\"资金流\") 单条 → Mongo 约定字段（净额万元）。"""
    # 文档单位为元；因子侧约定万元。
    yuan_to_wan = 1.0 / WAN_TO_YUAN
    main_net = _num_field(row, "main_net") * yuan_to_wan
    xl_net = _num_field(row, "jumbo_net") * yuan_to_wan
    l_net = _num_field(row, "big_net") * yuan_to_wan
    m_net = _num_field(row, "mid_net") * yuan_to_wan
    s_net = _num_field(row, "small_net") * yuan_to_wan
    main_pct = _num_field(row, "main_net_pct", "net_pct_main")
    xl_pct = _num_field(row, "xl_net_pct", "net_pct_xl")
    trade_date = row.get("date") or row.get("trade_date") or ""
    return {
        "code": code,
        "trade_date": normalize_date(str(trade_date)) if trade_date else "",
        "main_net": main_net,
        "xl_net": xl_net,
        "l_net": l_net,
        "m_net": m_net,
        "s_net": s_net,
        "main_net_pct": main_pct,
        "xl_net_pct": xl_pct,
        "main_net_ratio": main_pct / 100.0 if main_pct else 0.0,
        "xl_net_ratio": xl_pct / 100.0 if xl_pct else 0.0,
    }


def _empty_flow() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "code", "trade_date", "main_net", "xl_net", "l_net", "m_net", "s_net",
        "main_net_pct", "xl_net_pct", "main_net_ratio", "xl_net_ratio",
    ])
