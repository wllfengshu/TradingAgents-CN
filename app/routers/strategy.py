"""策略每日信号 API（与回测共用 score_signals / StrategyPipeline 链路）。"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.response import ok
from app.routers.auth_db import get_current_user
from app.schemas.strategy import (
    ConsistencyCheckData,
    ConsistencyCheckRequest,
    DailySignalsData,
    ExecuteOrdersRequest,
)
from zstock.strategy_management.signal_service import StrategySignalService

router = APIRouter(prefix="/strategy", tags=["strategy"])
logger = logging.getLogger("webapi")


@router.get("/meta")
async def get_strategy_meta(user: dict = Depends(get_current_user)):
    """返回当前 strategy_params.json 摘要（版本、自适应调仓等）。"""
    svc = StrategySignalService.instance()
    meta = svc.get_strategy_meta()
    return ok(data=meta, message="ok")


@router.get("/signals", response_model=None)
async def get_daily_signals(
    trade_date: str = Query(..., description="交易日 YYYY-MM-DD"),
    include_targets: bool = Query(False, description="是否运行 StrategyPipeline 并返回目标持仓"),
    include_watch: bool = Query(False, description="是否返回 watch 候选"),
    prefer_precomputed: bool = Query(True, description="优先 Mongo 预计算因子"),
    user: dict = Depends(get_current_user),
):
    """
    获取指定交易日截面买入信号。

    信号来源与回测一致：CrossSectionStrategyPipeline.score_signals()（预计算优先）。
    """
    svc = StrategySignalService.instance()
    try:
        payload = await svc.get_daily_signals(
            trade_date,
            include_targets=include_targets,
            prefer_precomputed=prefer_precomputed,
            include_watch=include_watch,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except Exception as e:
        logger.exception("get_daily_signals failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"信号生成失败: {e}",
        ) from e

    params = svc.load_strategy_params()
    payload["meta"]["strategy_version"] = params.get("version", "")
    payload["meta"]["strategy_name"] = params.get("strategy_name", "")
    return ok(data=payload, message="ok")


@router.post("/signals/consistency-check", response_model=None)
async def validate_signal_consistency(
    req: ConsistencyCheckRequest,
    user: dict = Depends(get_current_user),
):
    """
    回测一致性校验：
    - score_signals（回测快路径） vs StrategyPipeline._generate_signals（API 路径）
    - execute_full_pipeline(precomputed) vs execute_full_pipeline(内部 generate)
    """
    svc = StrategySignalService.instance()
    try:
        result = await svc.validate_consistency(
            req.trade_date,
            score_tolerance=req.score_tolerance,
            include_pipeline=req.include_pipeline,
            prefer_precomputed=req.prefer_precomputed,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except Exception as e:
        logger.exception("consistency check failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"一致性校验失败: {e}",
        ) from e

    msg = "consistent" if result["consistent"] else "inconsistent"
    return ok(data=result, message=msg)


@router.post("/execute-orders", response_model=None)
async def execute_orders(
    req: ExecuteOrdersRequest,
    user: dict = Depends(get_current_user),
):
    """将当日目标持仓交给 OrderManagementPipeline（默认拒绝 Mock 下单）。"""
    svc = StrategySignalService.instance()
    try:
        result = await svc.execute_orders(
            req.trade_date,
            allow_mock=req.allow_mock,
            prefer_precomputed=req.prefer_precomputed,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except Exception as e:
        logger.exception("execute_orders failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"下单失败: {e}",
        ) from e
    exec_status = (result.get("execution") or {}).get("status", "unknown")
    return ok(data=result, message=str(exec_status))
