"""
stockdb 行情与资金流。

本地库默认 127.0.0.1:7899，SDK 在 D:\\stockdb\\pybao（stock_sdk.py）。
日线、分钟线走 rd.get_data，一次可传代码列表。
资金流走 get_money_flow。净额单位与现有因子文档一致，是万元；
占比字段是百分数，同时给出除以 100 之后的比例，供 M4 门槛使用。

现金流量表 cash_flow 不是这里的资金流。
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Dict, Iterable, List, Optional, Sequence

import pandas as pd

from zstock.common.utils.common_utils import normalize_code, normalize_date

logger = logging.getLogger(__name__)

_DEFAULT_PYBAO = os.environ.get("STOCKDB_PYBAO", r"D:\stockdb\pybao")
_FLOW_CHUNK = 80

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
        # SDK 约定：rd.get_data 的 symbols 参数始终传列表，
        # 单代码也包一成列表，避免字符串/列表两种分支导致行为不一致。
        raw = sdk.rd.get_data(
            symbols,
            start=_compact_date(start),
            end=_compact_date(end),
            frequency=frequency,
            fq=fq,
            as_df=False,
        )
        grouped = _group_bars(raw, symbols)
        return {code: _bars_frame(code, rows) for code, rows in grouped.items()}

    def get_money_flow(
        self,
        codes: Sequence[str],
        start: str,
        end: str,
    ) -> Dict[str, pd.DataFrame]:
        """批量资金流。main_net / xl_net 单位是万元，与现有因子文档一致。"""
        sdk = self._require()
        getter = getattr(sdk, "get_money_flow", None)
        if getter is None:
            raise StockDbError("stock_sdk 未导出 get_money_flow")
        symbols = [normalize_code(code) for code in codes if normalize_code(code)]
        frames: Dict[str, List[dict]] = {code: [] for code in symbols}
        for chunk in _chunks(symbols, _FLOW_CHUNK):
            payload = getter(
                security_list=chunk,
                start_date=normalize_date(start),
                end_date=normalize_date(end),
            )
            if isinstance(payload, dict) and payload.get("error"):
                raise StockDbError(str(payload["error"]))
            for row in _as_records(payload):
                code = normalize_code(str(row.get("sec_code") or row.get("code") or ""))
                if code not in frames:
                    continue
                frames[code].append(_flow_row(code, row))
        return {
            code: pd.DataFrame(rows) if rows else _empty_flow()
            for code, rows in frames.items()
        }


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


def _chunks(items: Sequence[str], size: int) -> Iterable[List[str]]:
    step = max(int(size), 1)
    for i in range(0, len(items), step):
        yield list(items[i:i + step])


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


def _flow_row(code: str, row: dict) -> dict:
    def _num(key: str) -> float:
        try:
            value = row.get(key)
            if value is None or value == "":
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    main_pct = _num("net_pct_main")
    xl_pct = _num("net_pct_xl")
    trade_date = row.get("date") or row.get("trade_date") or ""
    return {
        "code": code,
        "trade_date": normalize_date(str(trade_date)) if trade_date else "",
        "main_net": _num("net_amount_main"),
        "xl_net": _num("net_amount_xl"),
        "l_net": _num("net_amount_l"),
        "m_net": _num("net_amount_m"),
        "s_net": _num("net_amount_s"),
        "main_net_pct": main_pct,
        "xl_net_pct": xl_pct,
        "main_net_ratio": main_pct / 100.0,
        "xl_net_ratio": xl_pct / 100.0,
    }


def _empty_flow() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "code", "trade_date", "main_net", "xl_net", "l_net", "m_net", "s_net",
        "main_net_pct", "xl_net_pct", "main_net_ratio", "xl_net_ratio",
    ])
