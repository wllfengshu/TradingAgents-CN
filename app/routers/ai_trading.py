"""
AI交易API路由
"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Query
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
import logging

from app.routers.auth_db import get_current_user
from app.services.ai_trading.ai_trading_service import get_ai_trading_service
from app.services.ai_trading.trading_records_service import get_ai_trading_records_service
from app.services.ai_trading.portfolio_service import get_portfolio_service

router = APIRouter()
logger = logging.getLogger("webapi")


class AiTradingRunRequest(BaseModel):
    """AI交易运行请求"""
    mode: str = Field(default="paper", description="交易模式: paper=模拟, live=实盘")

class AiTradingScheduleRequest(BaseModel):
    """AI交易定时运行请求"""
    cron_expression: str = Field(..., description="Cron表达式，如：0 30 9 * * 1-5")


class CronPreviewRequest(BaseModel):
    """Cron表达式预览请求"""
    cron_expression: str = Field(..., description="Cron表达式")
    count: int = Field(default=5, ge=1, le=20, description="预览次数")

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
        service = get_ai_trading_records_service()
        task = await service.get_task_status(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        task_user_id = task.get("user_id")
        if task_user_id != user["id"]:
            logger.error(
                f"AI交易状态权限校验失败(403): task_id={task_id}, "
                f"task.user_id={task_user_id}, current_user.id={user['id']}"
            )
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
        service = get_ai_trading_records_service()
        result = await service.get_task_result(task_id)
        if not result:
            raise HTTPException(status_code=404, detail="任务不存在")
        result_user_id = result.get("user_id")
        if result_user_id != user["id"]:
            logger.warning(
                f"AI交易结果权限校验失败(403): task_id={task_id}, "
                f"result.user_id={result_user_id}, current_user.id={user['id']}"
            )
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
        service = get_ai_trading_records_service()
        task = await service.get_task_status(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        task_user_id = task.get("user_id")
        if task_user_id != user["id"]:
            logger.warning(
                f"AI交易停止权限校验失败(403): task_id={task_id}, "
                f"task.user_id={task_user_id}, current_user.id={user['id']}"
            )
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
    status: Optional[str] = None,
    start_date: Optional[str] = Query(default=None, description="开始日期，格式 YYYY-MM-DD"),
    end_date: Optional[str] = Query(default=None, description="结束日期，格式 YYYY-MM-DD"),
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页条数"),
    user: dict = Depends(get_current_user),
):
    """获取AI交易操作记录"""
    try:
        service = get_ai_trading_records_service()
        result = await service.get_records(
            user["id"],
            mode=mode,
            status=status,
            start_date=start_date,
            end_date=end_date,
            page=page,
            page_size=page_size,
        )
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"❌ 获取AI交易记录失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/records/{task_id}", response_model=Dict[str, Any])
async def get_record_detail(
    task_id: str,
    user: dict = Depends(get_current_user),
):
    """获取AI交易记录详情"""
    try:
        service = get_ai_trading_records_service()
        result = await service.get_task_result(task_id)
        if not result:
            raise HTTPException(status_code=404, detail="记录不存在")

        result_user_id = result.get("user_id")
        if result_user_id != user["id"]:
            logger.warning(
                f"AI交易记录详情权限校验失败(403): task_id={task_id}, "
                f"result.user_id={result_user_id}, current_user.id={user['id']}"
            )
            raise HTTPException(status_code=403, detail="无权访问此记录")

        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 获取AI交易记录详情失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/records/{task_id}", response_model=Dict[str, Any])
async def delete_record(
    task_id: str,
    user: dict = Depends(get_current_user),
):
    """删除AI交易记录"""
    try:
        from app.core.database import get_mongo_db

        db = get_mongo_db()
        result = await db.ai_trading_tasks.delete_one(
            {"task_id": task_id, "user_id": user["id"]}
        )
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="记录不存在")

        return {"success": True, "message": "记录已删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 删除AI交易记录失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ── 持仓收益 ──────────────────────────────────────────────


class InitPortfolioRequest(BaseModel):
    initial_capital: float = Field(default=1000000.0, description="初始资金（默认100万）")


@router.get("/portfolio", response_model=Dict[str, Any])
async def get_portfolio(
    mode: str = Query(default="paper", description="交易模式: paper=模拟, live=实盘"),
    user: dict = Depends(get_current_user),
):
    """获取持仓收益概览"""
    try:
        if mode not in ("paper", "live"):
            raise HTTPException(status_code=400, detail="mode 仅支持 paper/live")
        service = get_portfolio_service()
        result = await service.get_portfolio(user["id"], mode=mode)
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 获取持仓收益失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/portfolio/history", response_model=Dict[str, Any])
async def get_portfolio_history(
    mode: str = Query(default="paper", description="交易模式: paper=模拟, live=实盘"),
    days: int = Query(default=30, ge=1, le=365, description="回看天数"),
    user: dict = Depends(get_current_user),
):
    """获取持仓历史净值曲线"""
    try:
        if mode not in ("paper", "live"):
            raise HTTPException(status_code=400, detail="mode 仅支持 paper/live")
        service = get_portfolio_service()
        result = await service.get_portfolio_history(user["id"], mode=mode, days=days)
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 获取持仓历史失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/portfolio/init", response_model=Dict[str, Any])
async def init_paper_portfolio(
    request: InitPortfolioRequest,
    user: dict = Depends(get_current_user),
):
    """初始化/重置模拟账户"""
    try:
        service = get_portfolio_service()
        result = await service.init_paper_portfolio(
            user["id"], initial_capital=request.initial_capital
        )
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"❌ 初始化模拟账户失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# ── 定时任务 ──────────────────────────────────────────────


@router.post("/schedule", response_model=Dict[str, Any])
async def create_schedule(
        request: AiTradingScheduleRequest,
        user: dict = Depends(get_current_user),
):
    """创建AI交易定时任务"""
    try:
        service = get_ai_trading_service()
        result = await service.create_schedule(
            user_id=user["id"],
            cron_expression=request.cron_expression,
        )
        return {"success": True, "data": result, "message": "定时任务已创建"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"❌ 创建AI交易定时任务失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/schedule", response_model=Dict[str, Any])
async def get_schedule(
        user: dict = Depends(get_current_user),
):
    """获取当前用户的AI交易定时任务"""
    try:
        service = get_ai_trading_service()
        result = await service.get_schedule(user_id=user["id"])
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"❌ 获取AI交易定时任务失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/schedule", response_model=Dict[str, Any])
async def delete_schedule(
        user: dict = Depends(get_current_user),
):
    """删除当前用户的AI交易定时任务"""
    try:
        service = get_ai_trading_service()
        result = await service.delete_schedule(user_id=user["id"])
        if not result:
            return {"success": True, "message": "无定时任务需要删除"}
        return {"success": True, "message": "定时任务已删除"}
    except Exception as e:
        logger.error(f"❌ 删除AI交易定时任务失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/schedule/preview", response_model=Dict[str, Any])
async def preview_cron(
        request: CronPreviewRequest,
        user: dict = Depends(get_current_user),
):
    """预览Cron表达式的下次执行时间"""
    try:
        service = get_ai_trading_service()
        result = await service.preview_cron(
            cron_expression=request.cron_expression,
            count=request.count,
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"❌ 预览Cron表达式失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))

