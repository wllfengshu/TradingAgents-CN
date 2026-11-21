"""截面持仓：每天按排名买前 n 只，掉出缓冲带才卖。"""

from __future__ import annotations

from typing import Dict, Iterable, Optional

import pandas as pd

from zstock.common.utils.common_utils import normalize_code


def rank_stock_weights(
    signals: Optional[pd.DataFrame],
    held: Iterable[str],
    *,
    n: int,
    buffer: int,
    cap: float,
) -> Dict[str, float]:
    """买排名 1..n 的股票。已持有且排名仍在 n+buffer 内的继续留着。

    名额满了不再换入。不在当日排名里的持仓卖掉。
    每只权重不超过 cap，凑不满 1 的部分留给后面的行业 ETF。
    """
    count = max(int(n), 0)
    hold_limit = count + max(int(buffer), 0)
    single = float(cap)
    if count <= 0 or single <= 0:
        return {}
    ranks = _ranks(signals)
    kept: list[str] = []
    for raw in held:
        code = normalize_code(str(raw))
        rank = ranks.get(code)
        if code and rank is not None and rank <= hold_limit and code not in kept:
            kept.append(code)
    for code, rank in sorted(ranks.items(), key=lambda item: item[1]):
        if len(kept) >= count:
            break
        if rank > count:
            break
        if code not in kept:
            kept.append(code)
    if not kept:
        return {}
    weight = min(single, 1.0 / count)
    return {code: weight for code in kept[:count]}


def _ranks(signals: Optional[pd.DataFrame]) -> Dict[str, int]:
    if signals is None or getattr(signals, "empty", True):
        return {}
    if "code" not in signals.columns or "rank" not in signals.columns:
        return {}
    out: Dict[str, int] = {}
    frame = signals.sort_values("rank")
    for _, row in frame.iterrows():
        code = normalize_code(str(row["code"]))
        if not code or code in out:
            continue
        try:
            rank = int(row["rank"])
        except (TypeError, ValueError):
            continue
        if rank > 0:
            out[code] = rank
    return out
