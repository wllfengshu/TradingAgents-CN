"""
AI选股记录服务
专门处理"AI选股记录"相关的查询接口（任务状态、任务结果、历史记录列表）。
分析逻辑（Agent调度、决策生成、定时任务等）请见 ai_selector_service.py。
"""

import logging
import threading
from typing import Any, Dict, Optional

from app.core.database import get_mongo_db

logger = logging.getLogger("app.services.ai_selector_records_service")


class AiSelectorRecordsService:
    """AI选股记录查询服务"""

    async def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务状态"""
        try:
            db = get_mongo_db()
            task = await db.ai_selector_tasks.find_one(
                {"task_id": task_id},
                {"_id": 0}
            )
            return task
        except Exception as e:
            logger.error(f"获取AI选股任务状态失败: {e}")
            return None

    async def get_task_list(self, user_id: str, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """获取AI选股任务列表（分页）"""
        try:
            db = get_mongo_db()
            query = {"user_id": user_id}
            total = await db.ai_selector_tasks.count_documents(query)
            skip = (page - 1) * page_size
            cursor = db.ai_selector_tasks.find(
                query,
                {"_id": 0, "result.analyst_results": 0, "result.decision_report": 0}
            ).sort("created_at", -1).skip(skip).limit(page_size)
            tasks = await cursor.to_list(length=page_size)
            return {"tasks": tasks, "total": total, "page": page, "page_size": page_size}
        except Exception as e:
            logger.error(f"获取AI选股任务列表失败: {e}")
            return {"tasks": [], "total": 0, "page": page, "page_size": page_size}

    async def get_task_result(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务结果"""
        try:
            db = get_mongo_db()
            task = await db.ai_selector_tasks.find_one(
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
                logger.info(
                    f"get_task_result: task_id={task_id}, status=completed, "
                    f"result.user_id={result.get('user_id') if result else 'N/A'}, "
                    f"result_keys={list(result.keys())[:10] if result else 'N/A'}"
                )
                return result
            logger.info(f"get_task_result: task_id={task_id}, status={task.get('status')}")
            return task
        except Exception as e:
            logger.error(f"获取AI选股任务结果失败: {e}")
            return None


# 单例
_ai_selector_records_service: Optional[AiSelectorRecordsService] = None
_ai_selector_records_service_lock = threading.Lock()


def get_ai_selector_records_service() -> AiSelectorRecordsService:
    global _ai_selector_records_service
    if _ai_selector_records_service is None:
        with _ai_selector_records_service_lock:
            if _ai_selector_records_service is None:
                _ai_selector_records_service = AiSelectorRecordsService()
    return _ai_selector_records_service
