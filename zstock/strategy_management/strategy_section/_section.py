"""截面策略。

select 不接收行情，自己向 query_service 取数，返回 {代码: 权重}。流程：

1. 打分。读预计算因子，先算市场灯和风格，再过滤股票池。
   板块打分取主线，主线里打龙头，合力门禁通过后按最终得分排序。
2. 取名。进攻日从排序最前面取股票，目标 3 只。红灯不新开仓。
3. 配权。按得分分配权重，单票不超过上限。
4. 合规。检查权重和、只数、单票上限和行业暴露，超限就改回来。
5. 卖出。排名掉出分位，或趋势走坏，这些持仓必须卖。
6. 换手。新股票要明显强于当前最弱持仓才换入；未满最短持有天数的不因调仓卖出。
7. 缩仓。按灯把股票权重量缩小。
8. 补余。股票没占满的权重交给行业 ETF；还不满就买国债。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from zstock.common.book_codes import BOOK_OHLCV_CODES
from zstock.common.utils.common_utils import normalize_code, normalize_date
from zstock.data_management.query_service import get_data_query_service


class SectionStrategy:
    """截面策略入口。select 按模块注释里的 8 步产出目标持仓。"""

    def __init__(self, query_service=None, pipeline=None):
        self._query_service = query_service
        self._pipeline = pipeline

    async def select(self, trade_date: Optional[str] = None) -> Dict[str, float]:
        from zstock.strategy_management.strategy_section.execute import run_equity_execution
        from zstock.strategy_management.market_book import BookDecision
        from zstock.strategy_management.pipeline import StrategyPipeline
        from zstock.strategy_management.strategy_section.optimizer import PortfolioOptimizer

        td = normalize_date(trade_date) if trade_date else datetime.now().strftime("%Y-%m-%d")
        pipe = self._pipeline or StrategyPipeline()
        if self._query_service is not None:
            pipe._query_service = self._query_service
        signals = await pipe.score_signals(td)
        grade = str(getattr(signals, "attrs", {}).get("market_grade") or "yellow")
        regime = str(getattr(signals, "attrs", {}).get("regime") or "neutral")
        qs = pipe._query_service or self._query_service or get_data_query_service()
        asset_closes = await _load_closes(qs, list(BOOK_OHLCV_CODES), td)
        played = run_equity_execution(
            trade_date=td,
            signals_df=signals,
            current_positions=None,
            total_capital=1.0,
            cfg=getattr(pipe, "runtime", None) or pipe.config,
            results={"style_regime": regime, "market_grade": grade},
            portfolio_optimizer=PortfolioOptimizer(),
            risk_manager=pipe._risk_manager,
            turnover_controller=pipe._turnover_controller,
            decision=BookDecision(state="attack", weights=None, grade=grade),
            asset_closes=asset_closes,
        )
        holdings = (played.get("results") or {}).get("final_holdings")
        return _weight_map(holdings)


def _weight_map(holdings) -> Dict[str, float]:
    if holdings is None or getattr(holdings, "empty", True) or "code" not in holdings.columns:
        return {}
    out: Dict[str, float] = {}
    for _, row in holdings.iterrows():
        code = normalize_code(str(row.get("code") or ""))
        if not code:
            continue
        try:
            weight = float(row.get("weight") or 0.0)
        except (TypeError, ValueError):
            weight = 0.0
        if weight > 1e-8:
            out[code] = out.get(code, 0.0) + weight
    return out


async def _load_closes(qs, codes: List[str], trade_date: str) -> Dict[str, List[float]]:
    end = normalize_date(trade_date)
    start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=500)).strftime("%Y-%m-%d")
    frames = await qs.get_ohlcv_batch(list(codes), start, end, period="daily")
    out: Dict[str, List[float]] = {}
    for code, frame in (frames or {}).items():
        if frame is None or getattr(frame, "empty", True) or "close" not in frame.columns:
            continue
        view = frame
        if "trade_date" in frame.columns:
            days = frame["trade_date"].map(lambda x: normalize_date(str(x)[:10]))
            view = frame.loc[days <= end].sort_values("trade_date")
        closes = []
        for value in view["close"].tolist():
            try:
                px = float(value)
            except (TypeError, ValueError):
                continue
            if px > 0:
                closes.append(px)
        if closes:
            out[normalize_code(str(code))] = closes
    return out
