"""
AI交易API路由
"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Query
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
import logging
import asyncio

from app.routers.auth_db import get_current_user
from app.services.ai_trading_service import get_ai_trading_service

router = APIRouter()
logger = logging.getLogger("webapi")


class AiTradingRunRequest(BaseModel):
    """AI交易运行请求"""
    mode: str = Field(default="paper", description="交易模式: paper=模拟, live=实盘")


@router.post("/run", response_model=Dict[str, Any])
async def run_ai_trading(
    request: AiTradingRunRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    """启动AI交易任务"""
    try:
        logger.info(f"📈 收到AI交易请求: mode={request.mode}, user={user['id']}")

        service = get_ai_trading_service()
        result = await service.create_task(user["id"], mode=request.mode)

        task_id = result["task_id"]
        user_id = user["id"]
        mode = request.mode

        async def run_task():
            try:
                logger.info(f"🚀 [AI交易后台任务] 开始: {task_id}, mode={mode}")
                svc = get_ai_trading_service()
                await svc.execute_task(task_id, user_id, mode=mode)
                logger.info(f"✅ [AI交易后台任务] 完成: {task_id}")
            except Exception as e:
                logger.error(f"❌ [AI交易后台任务] 失败: {task_id}, 错误: {e}", exc_info=True)

        background_tasks.add_task(run_task)

        return {
            "success": True,
            "data": result,
            "message": "AI交易任务已启动",
        }
    except ValueError as e:
        logger.warning(f"⚠️ AI交易任务被拒绝: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"❌ 启动AI交易任务失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/status/{task_id}", response_model=Dict[str, Any])
async def get_task_status(
    task_id: str,
    user: dict = Depends(get_current_user),
):
    """获取AI交易任务状态"""
    try:
        service = get_ai_trading_service()
        task = await service.get_task_status(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        if task.get("user_id") != user["id"]:
            raise HTTPException(status_code=403, detail="无权访问此任务")
        return {"success": True, "data": task}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 获取AI交易任务状态失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/result/{task_id}", response_model=Dict[str, Any])
async def get_task_result(
    task_id: str,
    user: dict = Depends(get_current_user),
):
    """获取AI交易任务结果"""
    try:
        service = get_ai_trading_service()
        result = await service.get_task_result(task_id)
        if not result:
            raise HTTPException(status_code=404, detail="任务不存在")
        if result.get("user_id") != user["id"]:
            raise HTTPException(status_code=403, detail="无权访问此任务")
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 获取AI交易任务结果失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/stop/{task_id}", response_model=Dict[str, Any])
async def stop_task(
    task_id: str,
    user: dict = Depends(get_current_user),
):
    """停止AI交易任务"""
    try:
        service = get_ai_trading_service()
        task = await service.get_task_status(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        if task.get("user_id") != user["id"]:
            raise HTTPException(status_code=403, detail="无权操作此任务")

        # 标记任务为失败状态（停止）
        from app.core.database import get_mongo_db
        from datetime import datetime
        db = get_mongo_db()
        await db.ai_trading_tasks.update_one(
            {"task_id": task_id},
            {"$set": {
                "status": "failed",
                "error_message": "用户手动停止",
                "updated_at": datetime.utcnow(),
            }}
        )

        return {"success": True, "data": {"message": "任务已停止"}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 停止AI交易任务失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/records", response_model=Dict[str, Any])
async def get_records(
    mode: Optional[str] = None,
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页条数"),
    user: dict = Depends(get_current_user),
):
    """获取AI交易操作记录"""
    try:
        service = get_ai_trading_service()
        result = await service.get_records(user["id"], mode=mode, page=page, page_size=page_size)
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"❌ 获取AI交易记录失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))
