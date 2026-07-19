"""
AI选股服务
直接调用 AiSelectorGraph（LangGraph架构），负责任务生命周期管理和MongoDB存储。
"""

import asyncio
import contextlib
import uuid
import logging
import time
import threading
from datetime import datetime
from typing import Dict, Any, Optional
from zoneinfo import ZoneInfo

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.graph.selector.selector_graph import AiSelectorGraph
from app.services.simple_analysis_service import create_analysis_config, get_provider_and_url_by_model_sync
from app.services.model_capability_service import get_model_capability_service
from app.utils.stock_utils import make_serializable
from app.utils.schedule_utils import ScheduleManager, preview_cron
from app.core.database import get_mongo_db

logger = logging.getLogger(__name__)

_CN_TZ = ZoneInfo("Asia/Shanghai")


def _now_cn() -> datetime:
    return datetime.now(_CN_TZ).replace(tzinfo=None)


class AiSelectorService:
    """AI选股服务：任务管理 + LangGraph调用"""

    # ============================================================
    # LLM 初始化
    # ============================================================

    def _build_llm_config(self) -> Dict[str, Any]:
        """复用现有模型配置逻辑，与股票分析保持一致"""
        capability_service = get_model_capability_service()
        quick_model, deep_model = capability_service.recommend_models_for_depth("快速")
        logger.info(f"AI选股 - 自动推荐模型: quick={quick_model}, deep={deep_model}")

        quick_info = get_provider_and_url_by_model_sync(quick_model)
        deep_info = get_provider_and_url_by_model_sync(deep_model)

        config = create_analysis_config(
            research_depth="快速",
            selected_analysts=["market"],
            quick_model=quick_model,
            deep_model=deep_model,
            llm_provider=quick_info["provider"],
            market_type="A股",
        )
        config["quick_provider"] = quick_info["provider"]
        config["deep_provider"] = deep_info["provider"]
        config["quick_backend_url"] = quick_info["backend_url"]
        config["deep_backend_url"] = deep_info["backend_url"]
        config["backend_url"] = quick_info["backend_url"]
        return config

    def _create_llm_instances(self, config: Dict[str, Any]):
        """通过 TradingAgentsGraph 创建正确配置的 LLM 实例（复用现有机制）"""
        graph = TradingAgentsGraph(
            selected_analysts=config.get("selected_analysts", ["market"]),
            debug=False,
            config=config,
        )
        logger.info(
            f"AI选股 - LLM就绪: quick={graph.quick_thinking_llm.__class__.__name__}"
            f"/{getattr(graph.quick_thinking_llm, 'model_name', '?')}, "
            f"deep={graph.deep_thinking_llm.__class__.__name__}"
            f"/{getattr(graph.deep_thinking_llm, 'model_name', '?')}"
        )
        return graph.quick_thinking_llm, graph.deep_thinking_llm

    # ============================================================
    # 任务管理
    # ============================================================

    async def create_task(self, user_id: str, trigger_type: str = "manual") -> Dict[str, Any]:
        """创建 AI 选股任务记录"""
        if trigger_type not in ("manual", "scheduled"):
            raise ValueError(f"非法触发类型: {trigger_type}")

        task_id = str(uuid.uuid4())
        try:
            db = get_mongo_db()
            running = await db.ai_selector_tasks.find_one(
                {"user_id": user_id, "status": {"$in": ["pending", "running"]}}
            )
            if running:
                raise ValueError(
                    f"已有AI选股任务正在执行（ID: {running['task_id'][:8]}...），请等待完成后再试"
                )
            await db.ai_selector_tasks.insert_one({
                "task_id": task_id,
                "user_id": user_id,
                "trigger_type": trigger_type,
                "status": "pending",
                "progress": 0,
                "current_step": "",
                "created_at": _now_cn(),
                "updated_at": _now_cn(),
            })
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"创建AI选股任务失败: {e}")
            raise RuntimeError(f"创建AI选股任务失败: {e}")

        return {"task_id": task_id, "status": "pending", "message": "AI选股任务已创建"}

    async def execute_task(self, task_id: str, user_id: str = None, debate_rounds: int = 1):
        """执行 AI 选股任务（后台运行）"""
        try:
            await self._update_status(task_id, "running", 5, "正在初始化AI模型...")
            start_time = time.time()

            config = await asyncio.to_thread(self._build_llm_config)
            quick_llm, deep_llm = await asyncio.to_thread(self._create_llm_instances, config)

            await self._update_status(task_id, "running", 15, "正在启动LangGraph选股引擎...")

            loop = asyncio.get_running_loop()
            progress_state = {"progress": 15, "message": "正在启动LangGraph选股引擎..."}

            def on_graph_progress(progress: int, message: str) -> None:
                bounded_progress = max(progress_state["progress"], min(int(progress), 99))
                if bounded_progress == progress_state["progress"] and message == progress_state["message"]:
                    return
                progress_state["progress"] = bounded_progress
                progress_state["message"] = message

                future = asyncio.run_coroutine_threadsafe(
                    self._update_status(task_id, "running", bounded_progress, message),
                    loop,
                )

                def _log_future_error(done_future):
                    with contextlib.suppress(Exception):
                        exc = done_future.exception()
                        if exc:
                            logger.warning(f"AI选股进度回调更新失败: {exc}")

                future.add_done_callback(_log_future_error)

            analysis_result = await asyncio.to_thread(
                self._run_graph,
                quick_llm,
                deep_llm,
                debate_rounds,
                on_graph_progress,
            )

            elapsed = time.time() - start_time
            result = {
                "task_id": task_id,
                "status": "completed",
                "progress": 100,
                "current_step": "分析完成",
                "elapsed_time": round(elapsed, 2),
                "early_stop": analysis_result["early_stop"],
                "early_stop_reason": analysis_result["early_stop_reason"],
                "analyst_results": analysis_result["analyst_results"],
                "decision": analysis_result["decision"],
                "decision_report": analysis_result["decision_report"],
                "execution_trace": analysis_result.get("execution_trace", {}),
                "completed_at": _now_cn().isoformat(),
            }

            try:
                db = get_mongo_db()
                await db.ai_selector_tasks.update_one(
                    {"task_id": task_id},
                    {"$set": {
                        "status": "completed",
                        "progress": 100,
                        "current_step": "分析完成",
                        "result": make_serializable(result),
                        "elapsed_time": round(elapsed, 2),
                        "updated_at": _now_cn(),
                    }}
                )
            except Exception as e:
                logger.error(f"保存AI选股结果到MongoDB失败: {e}")

            return result

        except Exception as e:
            logger.error(f"AI选股任务执行失败: {e}", exc_info=True)
            await self._update_status(task_id, "failed", 0, f"分析失败: {str(e)}", error_message=str(e))
            raise

    # ============================================================
    # LangGraph 调用
    # ============================================================

    def _run_graph(self, quick_llm, deep_llm, debate_rounds: int = 1, progress_callback=None) -> Dict[str, Any]:
        """同步调用 AiSelectorGraph，供 asyncio.to_thread 使用"""
        graph = AiSelectorGraph(
            config={
                "max_sector_debate_rounds": debate_rounds,
                "max_stock_debate_rounds": debate_rounds,
            },
            quick_llm=quick_llm,
            deep_llm=deep_llm,
        )
        analysis_date = datetime.now().strftime("%Y-%m-%d")
        result = graph.run(analysis_date=analysis_date, progress_callback=progress_callback)

        analyst_results = result.get("analyst_results", [])
        decision_data = result.get("decision", {})
        decision_report = next(
            (r.get("content", "") for r in analyst_results if r.get("name") == "决策分析师"),
            "",
        )
        return {
            "analyst_results": analyst_results,
            "decision": decision_data,
            "decision_report": decision_report,
            "execution_trace": result.get("execution_trace", {}),
            "early_stop": result.get("early_stop", False),
            "early_stop_reason": result.get("early_stop_reason", ""),
        }

    # ============================================================
    # 状态更新
    # ============================================================

    async def _update_status(self, task_id: str, status: str, progress: int,
                             current_step: str, error_message: str = None):
        try:
            db = get_mongo_db()
            update_data = {
                "status": status,
                "progress": progress,
                "current_step": current_step,
                "updated_at": _now_cn(),
            }
            if error_message:
                update_data["error_message"] = error_message
            await db.ai_selector_tasks.update_one(
                {"task_id": task_id},
                {"$set": update_data}
            )
        except Exception as e:
            logger.error(f"更新AI选股任务状态失败: {e}")

    # ============================================================
    # 定时任务
    # ============================================================

    _schedule_mgr = ScheduleManager(
        collection_name="ai_selector_schedules",
        job_id_prefix="ai_selector_schedule_",
        job_name="AI选股定时运行",
    )

    async def create_schedule(self, user_id: str, cron_expression: str) -> Dict[str, Any]:
        return await self._schedule_mgr.create_schedule(
            user_id, cron_expression, self._run_scheduled_task
        )

    async def get_schedule(self, user_id: str) -> Optional[Dict[str, Any]]:
        return await self._schedule_mgr.get_schedule(user_id)

    async def delete_schedule(self, user_id: str) -> bool:
        return await self._schedule_mgr.delete_schedule(user_id)

    async def preview_cron(self, cron_expression: str, count: int = 5) -> Dict[str, Any]:
        return preview_cron(cron_expression, count)

    async def _run_scheduled_task(self, user_id: str):
        try:
            logger.info(f"AI选股定时任务触发: user={user_id}")
            result = await self.create_task(user_id, trigger_type="scheduled")
            await self.execute_task(result["task_id"], user_id)
            logger.info(f"AI选股定时任务完成: user={user_id}")
        except Exception as e:
            logger.error(f"AI选股定时任务失败: user={user_id}, error={e}", exc_info=True)


# ============================================================
# 单例
# ============================================================

_service_instance: Optional[AiSelectorService] = None
_service_lock = threading.Lock()


def get_ai_selector_service() -> AiSelectorService:
    global _service_instance
    if _service_instance is None:
        with _service_lock:
            if _service_instance is None:
                _service_instance = AiSelectorService()
    return _service_instance
