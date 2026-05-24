"""
定时任务通用工具
封装 APScheduler + MongoDB 的定时任务 CRUD 逻辑，
供 AI选股、AI交易等服务复用。
"""

import logging
from datetime import datetime
from typing import Dict, Any, Optional, List, Callable
from zoneinfo import ZoneInfo

from croniter import croniter
from app.core.database import get_mongo_db

logger = logging.getLogger(__name__)

_CN_TZ = ZoneInfo("Asia/Shanghai")


def _now_cn() -> datetime:
    """返回上海时区当前时间（无tzinfo）"""
    return datetime.now(_CN_TZ).replace(tzinfo=None)


def validate_cron(cron_expression: str) -> None:
    """验证Cron表达式，无效时抛出ValueError"""
    try:
        cron = croniter(cron_expression, datetime.now(_CN_TZ))
        cron.get_next(datetime)
    except Exception as e:
        raise ValueError(f"无效的Cron表达式: {e}")


def get_next_run_times(cron_expression: str, count: int = 5) -> List[str]:
    """获取Cron表达式的下次执行时间列表"""
    cron = croniter(cron_expression, datetime.now(_CN_TZ))
    runs = []
    for _ in range(count):
        next_time = cron.get_next(datetime)
        runs.append(next_time.strftime("%Y-%m-%d %H:%M:%S"))
    return runs


def describe_cron(cron_expression: str) -> str:
    """生成Cron表达式的中文描述"""
    parts = cron_expression.strip().split()
    if len(parts) != 5:
        return cron_expression

    minute, hour, day, month, dow = parts
    desc_parts = []

    if month != "*":
        desc_parts.append(f"{month}月")

    if dow != "*" and day == "*":
        dow_map = {"0": "周日", "1": "周一", "2": "周二", "3": "周三",
                   "4": "周四", "5": "周五", "6": "周六", "7": "周日"}
        if "-" in dow:
            start, end = dow.split("-")
            desc_parts.append(f"每{dow_map.get(start, start)}至{dow_map.get(end, end)}")
        elif "," in dow:
            days = [dow_map.get(d.strip(), d.strip()) for d in dow.split(",")]
            desc_parts.append(f"每{','.join(days)}")
        else:
            desc_parts.append(f"每{dow_map.get(dow, dow)}")
    elif day != "*" and dow == "*":
        desc_parts.append(f"每月{day}日")
    elif day == "*" and dow == "*":
        desc_parts.append("每天")

    if hour != "*" and minute != "*":
        desc_parts.append(f"{hour.zfill(2)}:{minute.zfill(2)}")
    elif hour != "*":
        desc_parts.append(f"{hour}点每分钟")
    elif minute != "*":
        desc_parts.append(f"每小时{minute}分")

    return "".join(desc_parts) if desc_parts else cron_expression


def preview_cron(cron_expression: str, count: int = 5) -> Dict[str, Any]:
    """预览Cron表达式的下次执行时间"""
    try:
        next_runs = get_next_run_times(cron_expression, count)
        description = describe_cron(cron_expression)
        return {
            "cron_expression": cron_expression,
            "description": description,
            "next_run_times": next_runs,
        }
    except Exception:
        raise ValueError(f"无效的Cron表达式: {cron_expression}")


class ScheduleManager:
    """通用定时任务管理器

    封装 APScheduler + MongoDB 的定时任务 CRUD 逻辑，
    供各业务服务实例化使用。
    """

    def __init__(
        self,
        collection_name: str,
        job_id_prefix: str,
        job_name: str,
    ):
        """
        Args:
            collection_name: MongoDB集合名（如 "ai_selector_schedules"）
            job_id_prefix: APScheduler job ID前缀（如 "ai_selector_schedule_"）
            job_name: 任务名称（如 "AI选股定时运行"）
        """
        self.collection_name = collection_name
        self.job_id_prefix = job_id_prefix
        self.job_name = job_name

    def _job_id(self, user_id: str) -> str:
        return f"{self.job_id_prefix}{user_id}"

    async def create_schedule(
        self,
        user_id: str,
        cron_expression: str,
        task_callback: Callable,
    ) -> Dict[str, Any]:
        """创建定时任务

        Args:
            user_id: 用户ID
            cron_expression: Cron表达式
            task_callback: 定时触发的回调函数，签名为 async (user_id: str) -> None
        """
        validate_cron(cron_expression)

        from app.services.scheduler_service import get_scheduler_service
        from apscheduler.triggers.cron import CronTrigger

        scheduler_service = get_scheduler_service()
        scheduler = scheduler_service.scheduler

        job_id = self._job_id(user_id)

        # 如果已存在该用户的定时任务，先移除
        existing_job = scheduler.get_job(job_id)
        if existing_job:
            scheduler.remove_job(job_id)

        parts = cron_expression.strip().split()
        trigger = CronTrigger(
            minute=parts[0] if len(parts) > 0 else "*",
            hour=parts[1] if len(parts) > 1 else "*",
            day=parts[2] if len(parts) > 2 else "*",
            month=parts[3] if len(parts) > 3 else "*",
            day_of_week=parts[4] if len(parts) > 4 else "*",
            timezone=_CN_TZ,
        )

        scheduler.add_job(
            task_callback,
            trigger=trigger,
            id=job_id,
            name=self.job_name,
            kwargs={"user_id": user_id},
            replace_existing=True,
        )

        # 保存定时配置到 MongoDB
        db = get_mongo_db()
        await db[self.collection_name].update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "user_id": user_id,
                    "cron_expression": cron_expression,
                    "job_id": job_id,
                    "enabled": True,
                    "updated_at": _now_cn(),
                }
            },
            upsert=True,
        )

        next_runs = get_next_run_times(cron_expression, 1)
        logger.info(f"✅ {self.job_name}已创建: user={user_id}, cron={cron_expression}")

        return {
            "job_id": job_id,
            "cron_expression": cron_expression,
            "enabled": True,
            "next_run_time": next_runs[0] if next_runs else None,
        }

    async def get_schedule(self, user_id: str) -> Optional[Dict[str, Any]]:
        """获取用户的定时任务配置"""
        db = get_mongo_db()
        schedule = await db[self.collection_name].find_one(
            {"user_id": user_id},
            {"_id": 0}
        )
        if not schedule:
            return None

        from app.services.scheduler_service import get_scheduler_service
        scheduler_service = get_scheduler_service()
        job = scheduler_service.scheduler.get_job(schedule.get("job_id", ""))

        return {
            "cron_expression": schedule.get("cron_expression", ""),
            "enabled": schedule.get("enabled", False),
            "job_id": schedule.get("job_id", ""),
            "next_run_time": job.next_run_time.isoformat() if job and job.next_run_time else None,
        }

    async def delete_schedule(self, user_id: str) -> bool:
        """删除用户的定时任务"""
        db = get_mongo_db()
        schedule = await db[self.collection_name].find_one({"user_id": user_id})
        if not schedule:
            return False

        job_id = schedule.get("job_id", "")

        from app.services.scheduler_service import get_scheduler_service
        scheduler_service = get_scheduler_service()
        job = scheduler_service.scheduler.get_job(job_id)
        if job:
            scheduler_service.scheduler.remove_job(job_id)

        await db[self.collection_name].delete_one({"user_id": user_id})
        logger.info(f"✅ {self.job_name}已删除: user={user_id}")
        return True
