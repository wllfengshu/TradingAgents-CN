import pandas as pd
import asyncio
import threading
import time
from typing import Any, Dict, List

import logging
logger = logging.getLogger(__name__)


_RETRYABLE_ERROR_KEYWORDS = (
    "connection aborted",
    "remotedisconnected",
    "max retries exceeded",
    "timed out",
    "10054",
    "10053",
)


def call_akshare_with_retry(fetcher, context: str, max_retries: int = 3, base_delay: float = 0.8):
    """对 AKShare 的易抖动网络请求做有限重试，降低偶发 Connection aborted 的影响。"""
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            return fetcher()
        except Exception as e:
            last_error = e
            error_text = str(e).lower()
            is_retryable = any(k in error_text for k in _RETRYABLE_ERROR_KEYWORDS)
            if not is_retryable or attempt >= max_retries:
                break

            delay = base_delay * attempt
            logger.error(
                f"{context} 网络抖动，第 {attempt}/{max_retries} 次失败，{delay:.1f}s 后重试: {e}"
            )
            time.sleep(delay)

    raise last_error


def to_yi(value: Any) -> float:
    """将AKShare金额字段统一转换为"亿元"数值。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().replace(",", "")
    if not text:
        return 0.0

    multiplier = 1.0
    if text.endswith("亿元"):
        text = text[:-2]
        multiplier = 1.0
    elif text.endswith("亿"):
        text = text[:-1]
        multiplier = 1.0
    elif text.endswith("万元"):
        text = text[:-2]
        multiplier = 0.0001
    elif text.endswith("万"):
        text = text[:-1]
        multiplier = 0.0001
    elif text.endswith("元"):
        text = text[:-1]
        multiplier = 1e-8

    try:
        return float(text) * multiplier
    except ValueError:
        logger.debug(f"金额字段解析失败，原始值: {value}")
        return 0.0


def is_main_board_stock(code: str) -> bool:
    """判断是否为主板股票（沪深主板，排除创业板/科创板/北交所）。"""
    code = str(code).strip()
    return (
        code.startswith(("600", "601", "603", "605", "606"))  # 上海主板
        or code.startswith(("000", "001", "002", "003"))       # 深圳主板
    )


def cached_ak_call(method_name: str, func, expire: int = 600, **kwargs):
    """统一的 AKShare 缓存调用，减少各模块重复代码。"""
    import tradingagents.utils.api_cache as api_cache
    args_str = ", ".join(f"{k}={repr(v)}" for k, v in kwargs.items())
    cache_key = f"ak.{method_name}({args_str})" if args_str else f"ak.{method_name}()"
    return api_cache.call(cache_key, func, expire=expire, **kwargs)


def _latest_date_fmt() -> str:
    """返回今日日期字符串，供 ak_date_call_with_fallback 回退使用（可在测试中 monkeypatch）。"""
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d")


def ak_date_call_with_fallback(method_name: str, func, date_fmt: str, label: str = "", expire: int = 600):
    """AKShare 日期参数调用，超出30日窗口时自动回退到最新交易日。

    返回 (df, actual_date_fmt)。
    """
    data_date = date_fmt
    try:
        df = cached_ak_call(method_name, func, expire=expire, date=data_date)
    except Exception as e:
        msg = str(e)
        if "最近" not in msg and "30" not in msg:
            raise
        data_date = _latest_date_fmt()
        logger.warning(f"{label} 日期 {date_fmt} 超出AKShare可查窗口，回退到 {data_date}")
        df = cached_ak_call(method_name, func, expire=expire, date=data_date)
    return df, data_date


def resolve_coroutine(value: Any) -> Any:
    """在同步上下文中安全执行可能是协程的返回值。"""
    if not asyncio.iscoroutine(value):
        return value

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)

    # 当前线程已有运行中的事件循环，则在新线程中执行
    result_holder: dict = {}
    error_holder: dict = {}

    def _runner():
        try:
            result_holder["value"] = asyncio.run(value)
        except Exception as exc:
            error_holder["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()

    if "error" in error_holder:
        raise error_holder["error"]
    return result_holder.get("value")


def invoke_resolved_method(target: Any, method_name: str, *args, **kwargs) -> Any:
    """调用对象方法并兼容同步/异步返回值。"""
    method = getattr(target, method_name, None)
    if method is None:
        return None
    return resolve_coroutine(method(*args, **kwargs))


def call_provider_method(target: Any, method_name: str, *args, **kwargs) -> Any:
    """调用 provider 的指定方法，并兼容同步/异步返回值。"""
    return invoke_resolved_method(target, method_name, *args, **kwargs)


def normalize_stock_code(code: str) -> str:
    """标准化股票代码，兼容 000001.SZ / sh600519 / 600519 等格式。"""
    raw = str(code or "").strip()
    if not raw:
        return ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 6:
        return digits[-6:]
    return digits.zfill(6) if digits else ""


def normalize_stock_name(name: str) -> str:
    return str(name or "").strip()


def is_high_risk_stock_name(name: str) -> bool:
    """名称前缀风险判断：ST、*ST、退 开头的股票直接视为高风险。"""
    raw = str(name or "").strip()
    if not raw:
        return False

    upper = raw.upper()
    return upper.startswith("ST") or upper.startswith("*ST") or raw.startswith("退")


def coerce_float(value: Any):
    """尽量把各种财务字段转换为 float。"""
    if value is None:
        return None

    text = str(value).strip().replace(",", "")
    if not text or text in {"--", "nan", "None", "N/A"}:
        return None

    text = text.replace("倍", "").replace("%", "")
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def extract_indicator_map(main_indicators) -> Dict[str, object]:
    """把 AKShare 财务主表转换成“指标 -> 最新值”的映射。"""
    if main_indicators is None:
        return {}

    if hasattr(main_indicators, "to_dict") and not isinstance(main_indicators, list):
        try:
            main_indicators = main_indicators.to_dict("records")
        except Exception:
            return {}

    if not isinstance(main_indicators, list) or not main_indicators:
        return {}

    first_row = main_indicators[0]
    if not isinstance(first_row, dict) or "指标" not in first_row:
        return {}

    value_keys = [k for k in first_row.keys() if k != "指标"]
    latest_key = value_keys[-1] if value_keys else None
    if not latest_key:
        return {}

    indicator_map: Dict[str, object] = {}
    for row in main_indicators:
        if not isinstance(row, dict):
            continue
        indicator_name = str(row.get("指标", "")).strip()
        if not indicator_name:
            continue
        indicator_map[indicator_name] = row.get(latest_key)
    return indicator_map


def pick_indicator_value(indicator_map: Dict[str, object], keywords: List[str]):
    """按关键词在指标名称里模糊匹配需要的值。"""
    for key, value in indicator_map.items():
        key_text = str(key).strip().lower()
        for keyword in keywords:
            if keyword.lower() in key_text:
                return value
    return None


def extract_financial_metrics(financial_data: Dict) -> Dict[str, str]:
    """提取 PE、PB、资产负债率等关键财务指标。"""
    metrics = {"pe": "N/A", "pb": "N/A", "debt_ratio": "N/A"}
    if not isinstance(financial_data, dict):
        return metrics

    indicator_map = extract_indicator_map(financial_data.get("main_indicators"))

    pe_value = pick_indicator_value(indicator_map, ["市盈率", "动态市盈率", "pe_ttm", "pe"])
    pe_float = coerce_float(pe_value)
    if pe_float is not None:
        metrics["pe"] = f"{pe_float:.1f}倍"

    pb_value = pick_indicator_value(indicator_map, ["市净率", "pb"])
    pb_float = coerce_float(pb_value)
    if pb_float is not None:
        metrics["pb"] = f"{pb_float:.2f}倍"

    debt_value = pick_indicator_value(indicator_map, ["资产负债率", "负债率", "debt_to_assets"])
    debt_float = coerce_float(debt_value)
    if debt_float is None:
        balance_sheet = financial_data.get("balance_sheet") or []
        if isinstance(balance_sheet, list) and balance_sheet:
            latest_balance = balance_sheet[0] if isinstance(balance_sheet[0], dict) else {}
            total_assets = coerce_float(latest_balance.get("total_assets"))
            total_liab = coerce_float(latest_balance.get("total_liab"))
            if total_assets and total_assets > 0 and total_liab is not None:
                debt_float = (total_liab / total_assets) * 100

    if debt_float is not None:
        metrics["debt_ratio"] = f"{debt_float:.1f}%"

    return metrics


