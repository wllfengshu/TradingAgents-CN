"""按灯把截面、行业 ETF、低波三份结果和当前持仓合成一个目标仓。

绿灯先买股票，再买行业 ETF，最后用低波补满。
黄灯不再买新股票。截面还要的旧股票留下，腾出来的权重买行业 ETF，没占满的用低波补上。
红灯卖掉全部股票。行业 ETF 只留策略还要、而且已经持有的，不买新的，空余换成低波。
"""

from __future__ import annotations

from typing import Dict, Mapping

from zstock.common.book_codes import INDEX_CODE, INDUSTRY_ETFS, T0_CODES
from zstock.common.utils.common_utils import normalize_code

_INDUSTRY = {normalize_code(c) for c in INDUSTRY_ETFS}
_NOT_STOCK = _INDUSTRY | set(T0_CODES) | {INDEX_CODE}


def is_stock(code: str) -> bool:
    return normalize_code(code) not in _NOT_STOCK


def combine_by_light(
    grade: str,
    current: Mapping[str, float],
    stocks: Mapping[str, float],
    industry: Mapping[str, float],
    low_vol: Mapping[str, float],
) -> Dict[str, float]:
    """返回合计为 1 的 {代码: 权重}。"""
    grade = str(grade or "yellow").lower()
    current_w = _clean(current)
    stock_w = {c: w for c, w in _clean(stocks).items() if is_stock(c)}
    industry_w = _clean(industry)
    low_w = _clean(low_vol)
    if grade == "green":
        out = _fit(stock_w, 1.0)
        residual = 1.0 - sum(out.values())
        etf = {c: w for c, w in industry_w.items() if c in _INDUSTRY}
        taken = _fit(etf, residual)
        _add(out, taken)
        residual = 1.0 - sum(out.values())
        _add(out, _fill(low_w, residual))
        return _clean(out)
    if grade == "red":
        kept = {}
        for code, cur in current_w.items():
            if code in _INDUSTRY and industry_w.get(code, 0.0) > 0:
                kept[code] = min(cur, industry_w[code])
        out = _fit(kept, 1.0)
        residual = 1.0 - sum(out.values())
        _add(out, _fill(low_w, residual))
        return _clean(out)
    kept = {}
    for code, cur in current_w.items():
        if is_stock(code) and stock_w.get(code, 0.0) > 0:
            kept[code] = min(cur, stock_w[code])
    out = _fit(kept, 1.0)
    residual = 1.0 - sum(out.values())
    etf = {c: w for c, w in industry_w.items() if c in _INDUSTRY}
    taken = _fit(etf, residual)
    _add(out, taken)
    residual = 1.0 - sum(out.values())
    _add(out, _fill(low_w, residual))
    return _clean(out)


def _clean(weights: Mapping[str, float]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for code, raw in (weights or {}).items():
        code = normalize_code(str(code))
        try:
            weight = float(raw)
        except (TypeError, ValueError):
            continue
        if code and weight > 1e-8:
            out[code] = out.get(code, 0.0) + weight
    return out


def _fit(weights: Dict[str, float], budget: float) -> Dict[str, float]:
    """按原权重放入，不超过 budget，也不超过这些权重自己的合计。"""
    total = sum(weights.values())
    if budget <= 1e-12 or total <= 1e-12:
        return {}
    take = min(budget, total)
    scale = take / total
    return {code: weight * scale for code, weight in weights.items()}


def _fill(weights: Dict[str, float], budget: float) -> Dict[str, float]:
    """把一份合计约 1 的策略结果缩放到 budget。"""
    total = sum(weights.values())
    if budget <= 1e-12 or total <= 1e-12:
        return {}
    scale = budget / total
    return {code: weight * scale for code, weight in weights.items()}


def _add(dest: Dict[str, float], extra: Dict[str, float]) -> None:
    for code, weight in extra.items():
        dest[code] = dest.get(code, 0.0) + weight
