"""
AI选股API路由
"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Query
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
import logging
import asyncio

from app.routers.auth_db import get_current_user
from app.services.ai_selector_service import get_ai_selector_service

router = APIRouter()
logger = logging.getLogger("webapi")


class AiSelectorRequest(BaseModel):
    """AI选股请求"""
    quick_model: str = Field(default="qwen-turbo", description="快速分析模型")
    deep_model: str = Field(default="qwen-max", description="深度决策模型")


@router.post("/run", response_model=Dict[str, Any])
async def run_ai_selector(
    request: AiSelectorRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    """启动AI选股任务"""
    try:
        logger.info(f"🎯 收到AI选股请求: quick_model={request.quick_model}, deep_model={request.deep_model}")

        service = get_ai_selector_service()
        result = await service.create_task(user["id"])

        task_id = result["task_id"]
        user_id = user["id"]
        task_id = result["task_id"]
        user_id = user["id"]

        async def run_task():
            try:
                logger.info(f"🚀 [AI选股后台任务] 开始: {task_id}")
                svc = get_ai_selector_service()
                await svc.execute_task(task_id, user_id)
                logger.info(f"✅ [AI选股后台任务] 完成: {task_id}")
            except Exception as e:
                logger.error(f"❌ [AI选股后台任务] 失败: {task_id}, 错误: {e}", exc_info=True)

        background_tasks.add_task(run_task)

        return {
            "success": True,
            "data": result,
            "message": "AI选股任务已启动",
        }
    except Exception as e:
        logger.error(f"❌ 启动AI选股任务失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/status/{task_id}", response_model=Dict[str, Any])
async def get_task_status(
    task_id: str,
    user: dict = Depends(get_current_user),
):
    """获取AI选股任务状态"""
    try:
        service = get_ai_selector_service()
        task = await service.get_task_status(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        # Fix: 校验任务归属，防止越权查询他人任务
        task_user_id = task.get("user_id")
        if task_user_id != user["id"]:
            logger.warning(
                f"AI选股状态权限校验失败(403): task_id={task_id}, "
                f"task.user_id={task_user_id}, current_user.id={user['id']}"
            )
            raise HTTPException(status_code=403, detail="无权访问此任务")
        return {"success": True, "data": task}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 获取AI选股任务状态失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/result/{task_id}", response_model=Dict[str, Any])
async def get_task_result(
    task_id: str,
    user: dict = Depends(get_current_user),
):
    """获取AI选股任务结果"""
    try:
        service = get_ai_selector_service()
        result = await service.get_task_result(task_id)
        if not result:
            raise HTTPException(status_code=404, detail="任务不存在")
        # Fix: 校验任务归属，防止越权查询他人任务
        result_user_id = result.get("user_id")
        if result_user_id != user["id"]:
            logger.warning(
                f"AI选股结果权限校验失败(403): task_id={task_id}, "
                f"result.user_id={result_user_id}, current_user.id={user['id']}"
            )
            raise HTTPException(status_code=403, detail="无权访问此任务")
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 获取AI选股任务结果失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/history", response_model=Dict[str, Any])
async def get_task_history(
    # Fix: 添加分页参数校验，防止 page_size=100000 导致性能问题
    page: int = Query(default=1, ge=1, description="页码，从1开始"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页条数，最多100"),
    user: dict = Depends(get_current_user),
):
    """获取AI选股历史记录列表"""
    try:
        service = get_ai_selector_service()
        result = await service.get_task_list(user["id"], page, page_size)
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"❌ 获取AI选股历史记录失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/history/{task_id}", response_model=Dict[str, Any])
async def get_task_history_detail(
    task_id: str,
    user: dict = Depends(get_current_user),
):
    """获取AI选股历史记录详情"""
    try:
        service = get_ai_selector_service()
        result = await service.get_task_result(task_id)
        if not result:
            logger.warning(f"AI选股记录详情查询: task_id={task_id} 未找到记录")
            raise HTTPException(status_code=404, detail="记录不存在")
        # Fix: 校验任务归属，防止越权查询他人任务
        result_user_id = result.get("user_id")
        logger.info(
            f"AI选股记录详情权限校验: task_id={task_id}, "
            f"result.user_id={result_user_id}, current_user.id={user['id']}, "
            f"result_keys={list(result.keys())[:10]}"
        )
        if result_user_id != user["id"]:
            logger.warning(
                f"AI选股记录详情权限校验失败(403): task_id={task_id}, "
                f"result.user_id={result_user_id}, current_user.id={user['id']}"
            )
            raise HTTPException(status_code=403, detail="无权访问此记录")
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 获取AI选股记录详情失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/history/{task_id}", response_model=Dict[str, Any])
async def delete_task_history(
    task_id: str,
    user: dict = Depends(get_current_user),
):
    """删除AI选股历史记录"""
    try:
        from app.core.database import get_mongo_db
        db = get_mongo_db()
        result = await db.ai_selector_tasks.delete_one(
            {"task_id": task_id, "user_id": user["id"]}
        )
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="记录不存在")
        return {"success": True, "message": "记录已删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 删除AI选股记录失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))
