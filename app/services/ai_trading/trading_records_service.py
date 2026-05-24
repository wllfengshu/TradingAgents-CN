"""
AI交易记录服务
专门处理"AI交易记录"相关的查询接口（任务状态、任务结果、操作记录列表）。
立即交易（执行下单、调度Agent等）请见 ai_trading_service.py。
"""

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from app.core.database import get_mongo_db

logger = logging.getLogger("app.services.ai_trading_records_service")


class AiTradingRecordsService:
    """AI交易记录查询服务"""

    async def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务状态"""
        try:
            db = get_mongo_db()
            task = await db.ai_trading_tasks.find_one(
                {"task_id": task_id},
                {"_id": 0}
            )
            return task
        except Exception as e:
            logger.error(f"获取AI交易任务状态失败: {e}")
            return None

    async def get_task_result(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务结果"""
        try:
            db = get_mongo_db()
            task = await db.ai_trading_tasks.find_one(
                {"task_id": task_id},
                {"_id": 0}
            )
            if not task:
                logger.warning(f"get_task_result: task_id={task_id} 未找到记录")
                return None
            if task.get("status") == "completed":
                result = task.get("result", task)
                # result 子文档不含 user_id，需要从 task 顶层补上，否则路由层权限校验会 403
                if result and "user_id" not in result:
                    result["user_id"] = task.get("user_id")
                return result
            return task
        except Exception as e:
            logger.error(f"获取AI交易任务结果失败: {e}")
            return None

    async def get_records(self, user_id: str, mode: str = None,
                          status: str = None,
                          start_date: str = None,
                          end_date: str = None,
                          page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """获取AI交易操作记录"""
        try:
            db = get_mongo_db()
            query = {"user_id": user_id}
            if mode:
                query["mode"] = mode
            if status:
                query["status"] = status

            if start_date or end_date:
                shanghai_tz = ZoneInfo("Asia/Shanghai")
                utc_tz = ZoneInfo("UTC")
                created_at_query = {}

                if start_date:
                    start_dt = datetime.fromisoformat(start_date).replace(tzinfo=shanghai_tz)
                    created_at_query["$gte"] = start_dt.astimezone(utc_tz)

                if end_date:
                    end_dt = datetime.fromisoformat(end_date).replace(tzinfo=shanghai_tz) + timedelta(days=1)
                    created_at_query["$lt"] = end_dt.astimezone(utc_tz)

                if created_at_query:
                    query["created_at"] = created_at_query

            total = await db.ai_trading_tasks.count_documents(query)
            skip = (page - 1) * page_size
            cursor = db.ai_trading_tasks.find(
                query,
                {"_id": 0, "result.analyst_results": 0, "result.decision_report": 0}
            ).sort("created_at", -1).skip(skip).limit(page_size)
            tasks = await cursor.to_list(length=page_size)

            for task in tasks:
                result = task.get("result")
                if isinstance(result, dict) and "user_id" not in result:
                    result["user_id"] = task.get("user_id")

            return {"tasks": tasks, "total": total, "page": page, "page_size": page_size}
        except Exception as e:
            logger.error(f"获取AI交易记录失败: {e}")
            return {"tasks": [], "total": 0, "page": page, "page_size": page_size}


# 单例
_ai_trading_records_service: Optional[AiTradingRecordsService] = None
_ai_trading_records_service_lock = threading.Lock()


def get_ai_trading_records_service() -> AiTradingRecordsService:
    global _ai_trading_records_service
    if _ai_trading_records_service is None:
        with _ai_trading_records_service_lock:
            if _ai_trading_records_service is None:
                _ai_trading_records_service = AiTradingRecordsService()
    return _ai_trading_records_service
