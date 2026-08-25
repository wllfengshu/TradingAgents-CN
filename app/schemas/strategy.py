"""策略信号 API 请求/响应模型。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SignalItem(BaseModel):
    code: str
    rank: Optional[int] = None
    signal_type: Optional[str] = None
    final_score: Optional[float] = None
    dragon_score: Optional[float] = None
    force_composite_score: Optional[float] = None
    sector_code: Optional[str] = None
    market_grade: Optional[str] = None
    position_scale: Optional[float] = None
    trade_date: Optional[str] = None


class SignalMeta(BaseModel):
    trade_date: str
    source: str = Field(..., description="precomputed | live")
    regime: str
    market_grade: str
    position_scale: float
    top_k: int
    universe_count: int
    buy_count: int
    strategy_version: Optional[str] = None
    strategy_name: Optional[str] = None


class DailySignalsData(BaseModel):
    meta: SignalMeta
    buy_signals: List[SignalItem]
    watch_signals: Optional[List[SignalItem]] = None
    targets: Optional[List[Dict[str, Any]]] = None
    pipeline_summary: Optional[Dict[str, Any]] = None


class ConsistencyCheckRequest(BaseModel):
    trade_date: str = Field(..., description="交易日 YYYY-MM-DD")
    score_tolerance: float = Field(1e-6, ge=0.0, description="分数/权重容差")
    include_pipeline: bool = Field(True, description="是否校验完整 StrategyPipeline")
    prefer_precomputed: bool = Field(True, description="API 路径是否优先预计算")


class ConsistencyDiff(BaseModel):
    field: str
    backtest_value: Any
    api_value: Any


class ConsistencyCheckData(BaseModel):
    trade_date: str
    strategy_version: str
    consistent: bool
    checks: Dict[str, Any]
    diffs: List[ConsistencyDiff]
