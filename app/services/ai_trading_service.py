"""
AI交易服务
多Agent协同完成智能交易流程：
1. 获取账户持仓情况
2. 如果有持仓：并发调用"单个股票分析" + "AI选股"
3. 仓位管理分析师：综合持仓、分析结果、选股结果，给出买卖信号
4. 交易决策分析师：审核信号，执行下单
"""

import asyncio
import uuid
import json
import logging
import time
import threading
from datetime import datetime
from typing import Dict, Any, Optional, List
from zoneinfo import ZoneInfo

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.utils.json_compressor import compress_json_for_llm
from app.utils.xtquant_util import MockQMTUtil, AccountInfo, Position
from langchain_core.messages import HumanMessage

from tradingagents.graph.trading_graph import TradingAgentsGraph
from app.services.simple_analysis_service import (
    create_analysis_config,
    get_provider_and_url_by_model_sync,
)
from app.core.database import get_mongo_db
from app.services.ai_selector_service import AiSelectorService, ApiCache

logger = logging.getLogger("app.services.ai_trading_service")


# ============================================================
# Agent提示词模板
# ============================================================

POSITION_MANAGER_PROMPT = """你是一位资深的仓位管理分析师，负责综合持仓状况、个股分析结果和AI选股结果，给出具体的买卖信号。

## 当前时间
{current_time}

## 账户信息
- 可用资金：{cash} 元
- 总资产：{total_value} 元
- 冻结资金：{frozen_cash} 元

## 当前持仓
{positions_info}

## 持仓个股分析结果
{position_analysis_results}

## AI选股结果（新机会）
{selector_results}

---

请基于以上信息，从以下维度进行分析：

1. **持仓评估**：
   - 当前持仓的股票基本面和技术面是否依然健康？
   - 是否有股票需要减仓或清仓？给出具体理由。

2. **新机会评估**：
   - AI选股推荐的标的，是否值得建仓？
   - 与现有持仓是否有板块重叠？如何分散风险？

3. **资金配置**：
   - 可用资金如何分配？优先加仓已有持仓还是新建仓？
   - 总仓位建议（占可用资金的比例）。

4. **买卖信号**：
   - 对每只需要操作的股票，给出明确信号：买入/卖出/持有
   - 买入：建议价格和金额（或股数）
   - 卖出：建议价格和股数（全部卖出或部分减仓）

在分析报告的最后，请用如下JSON格式输出结构化的买卖信号（放在```json代码块中）：
```json
{{
  "signals": [
    {{
      "code": "股票代码",
      "name": "股票名称",
      "action": "买入/卖出/持有",
      "price": 建议价格(数字),
      "volume": 建议数量(股,整数),
      "amount": 建议金额(元,买入时使用),
      "reason": "操作理由（含具体数据支撑）"
    }}
  ],
  "total_position_ratio": 建议总仓位比例(0-1的小数),
  "risk_assessment": "整体风险评估说明"
}}
```

注意：
- 买入信号的volume必须为100的整数倍（A股1手=100股）
- 卖出信号的volume不能超过当前持仓量
- 如果没有操作信号，signals返回空数组
- action为"持有"的信号仅表示建议维持当前仓位，不需要执行交易

使用中文输出，专业严谨。"""

TRADING_DECISION_PROMPT = """你是一位资深的交易决策分析师，负责审核仓位管理分析师给出的买卖信号，确认后执行下单。

## 当前时间
{current_time}

## 账户信息
- 可用资金：{cash} 元
- 总资产：{total_value} 元

## 当前持仓
{positions_info}

## 仓位管理分析师的买卖信号
{trading_signals}

---

请逐一审核每条买卖信号：

1. **信号审核**：
   - 该信号是否有充分的分析依据？
   - 价格是否合理（与当前市价偏离不大）？
   - 金额/数量是否在可承受范围内？

2. **风控检查**：
   - 单笔交易金额是否超过可用资金的30%？
   - 单只股票持仓是否超过总资产的40%？
   - 是否存在过度集中的风险？

3. **执行决策**：
   - 对审核通过的信号，确认执行
   - 对审核不通过的信号，给出拒绝理由
   - 可调整信号参数（如降低买入金额）

在分析报告的最后，请用如下JSON格式输出最终交易指令（放在```json代码块中）：
```json
{{
  "approved_signals": [
    {{
      "code": "股票代码",
      "name": "股票名称",
      "action": "买入/卖出",
      "price": 执行价格(数字),
      "volume": 执行数量(股),
      "amount": 执行金额(元,仅买入时),
      "reason": "审核意见"
    }}
  ],
  "rejected_signals": [
    {{
      "code": "股票代码",
      "name": "股票名称",
      "action": "原操作方向",
      "reject_reason": "拒绝理由"
    }}
  ],
  "decision_summary": "整体交易决策摘要"
}}
```

注意：approved_signals中的信号将被自动执行下单，请务必谨慎审核。

使用中文输出，专业严谨。"""


# ============================================================
# AI交易服务主类
# ============================================================

class AiTradingService:
    """AI交易服务"""

    def __init__(self):
        self._qmt_util = None

    def _get_qmt_util(self):
        """获取QMT工具实例（当前使用Mock，后续可切换为真实QMT）"""
        if self._qmt_util is None:
            self._qmt_util = MockQMTUtil()
        return self._qmt_util

    def _build_llm_config(self) -> Dict[str, Any]:
        """构建LLM配置，复用AI选股的配置逻辑"""
        from app.services.model_capability_service import get_model_capability_service

        capability_service = get_model_capability_service()
        research_depth = "标准"

        quick_model, deep_model = capability_service.recommend_models_for_depth(research_depth)
        logger.info(f"AI交易 - 自动推荐模型: quick={quick_model}, deep={deep_model}")

        quick_provider_info = get_provider_and_url_by_model_sync(quick_model)
        deep_provider_info = get_provider_and_url_by_model_sync(deep_model)

        quick_provider = quick_provider_info["provider"]
        deep_provider = deep_provider_info["provider"]
        quick_backend_url = quick_provider_info["backend_url"]
        deep_backend_url = deep_provider_info["backend_url"]

        config = create_analysis_config(
            research_depth=research_depth,
            selected_analysts=["market"],
            quick_model=quick_model,
            deep_model=deep_model,
            llm_provider=quick_provider,
            market_type="A股",
        )

        config["quick_provider"] = quick_provider
        config["deep_provider"] = deep_provider
        config["quick_backend_url"] = quick_backend_url
        config["deep_backend_url"] = deep_backend_url
        config["backend_url"] = quick_backend_url

        return config

    def _create_llm_instances(self, config: Dict[str, Any]):
        """创建LLM实例"""
        graph = TradingAgentsGraph(
            selected_analysts=config.get("selected_analysts", ["market"]),
            debug=False,
            config=config,
        )
        return graph.quick_thinking_llm, graph.deep_thinking_llm

    def _invoke_llm(self, llm, messages, analyst_name: str = "") -> Any:
        """LLM调用，带指数退避重试"""
        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            retry=retry_if_exception_type(Exception),
            reraise=True,
        )
        def _do_invoke():
            return llm.invoke(messages)
        return _do_invoke()

    def _run_analyst(self, llm, analyst_name: str, prompt_template: str,
                     extra_params: Dict[str, str] = None) -> str:
        """运行单个分析师Agent"""
        logger.info(f"AI交易 [{analyst_name}] 开始分析...")

        format_params = {}
        if extra_params:
            format_params.update(extra_params)

        prompt = prompt_template.format(**format_params)

        logger.info(
            f"[LLM提示词] [{analyst_name}] 提示词长度={len(prompt)}\n"
            f"{'='*60}\n{prompt}\n{'='*60}"
        )

        try:
            response = self._invoke_llm(llm, [HumanMessage(content=prompt)], analyst_name)
            report = response.content
            logger.info(f"AI交易 [{analyst_name}] 分析完成，报告长度: {len(report)}")
            logger.info(
                f"[LLM输出] [{analyst_name}] 输出长度={len(report)}\n"
                f"{'='*60}\n{report}\n{'='*60}"
            )
            return report
        except Exception as e:
            logger.error(f"AI交易 [{analyst_name}] LLM调用失败: {e}")
            return f"{analyst_name}分析失败: {str(e)}"

    # ============================================================
    # 结构化数据提取
    # ============================================================

    def _extract_json_block(self, text: str) -> Optional[Dict]:
        """从文本中提取最后一个```json代码块并解析"""
        import re
        try:
            matches = re.findall(r'```json\s*(.*?)\s*```', text, re.DOTALL)
            if matches:
                return json.loads(matches[-1])
        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"解析JSON代码块失败: {e}")
        return None

    def _extract_trading_signals(self, report: str) -> List[Dict]:
        """从仓位管理分析师报告中提取交易信号"""
        try:
            data = self._extract_json_block(report)
            if data and "signals" in data:
                signals = data["signals"]
                if isinstance(signals, list):
                    # 过滤掉"持有"信号，只保留需要操作的信号
                    return [s for s in signals if s.get("action") != "持有"]
        except Exception:
            pass
        return []

    def _extract_approved_signals(self, report: str) -> List[Dict]:
        """从交易决策分析师报告中提取审核通过的信号"""
        try:
            data = self._extract_json_block(report)
            if data and "approved_signals" in data:
                signals = data["approved_signals"]
                if isinstance(signals, list):
                    return signals
        except Exception:
            pass
        return []

    def _format_positions_for_prompt(self, positions: List[Position]) -> str:
        """格式化持仓信息用于提示词"""
        if not positions:
            return "当前无持仓（空仓）"
        lines = []
        for p in positions:
            pnl = p.unrealized_pnl
            pnl_pct = (p.current_price - p.cost_price) / p.cost_price * 100 if p.cost_price > 0 else 0
            lines.append(
                f"- {p.code} {p.name}: 持仓{p.volume}股, 成本{p.cost_price:.2f}, "
                f"现价{p.current_price:.2f}, 盈亏{pnl:+.2f}元({pnl_pct:+.2f}%), "
                f"市值{p.market_value:.2f}元"
            )
        return "\n".join(lines)

    def _format_selector_result_for_prompt(self, selector_result: Dict) -> str:
        """格式化AI选股结果用于提示词"""
        if not selector_result:
            return "AI选股未产出推荐结果"

        decision = selector_result.get("decision", {})
        analyst_results = selector_result.get("analyst_results", [])

        parts = []

        if decision:
            action = decision.get("action", "")
            stocks = decision.get("stocks", [])
            reasoning = decision.get("reasoning", "")
            position_suggestion = decision.get("position_suggestion", "")
            risk_warning = decision.get("risk_warning", "")

            parts.append(f"决策倾向：{action}")
            if stocks:
                stock_strs = [f"{s.get('code', '')} {s.get('name', '')}（{s.get('reason', '')}）" for s in stocks]
                parts.append(f"推荐标的：{', '.join(stock_strs)}")
            if reasoning:
                parts.append(f"决策依据：{reasoning}")
            if position_suggestion:
                parts.append(f"仓位建议：{position_suggestion}")
            if risk_warning:
                parts.append(f"风险提示：{risk_warning}")

        # 附加各分析师结论摘要
        if analyst_results:
            parts.append("\n--- 各分析师结论摘要 ---")
            for r in analyst_results:
                if r.get("conclusion") != "已跳过" and r.get("conclusion") != "未执行":
                    parts.append(f"【{r.get('name', '')}】{r.get('conclusion', '')}")

        return "\n".join(parts)

    def _format_analysis_result_for_prompt(self, stock_code: str, stock_name: str,
                                           analysis_result: Dict) -> str:
        """格式化个股分析结果用于提示词"""
        if not analysis_result:
            return f"{stock_code} {stock_name}: 分析未产出结果"

        # 从分析结果中提取关键信息
        parts = [f"【{stock_code} {stock_name}】"]

        # 提取决策结果
        decision = analysis_result.get("decision", {})
        if decision:
            parts.append(f"分析结论：{decision.get('action', '未知')}")
            reasoning = decision.get("reasoning", "")
            if reasoning:
                parts.append(f"分析依据：{reasoning[:500]}")

        # 提取各分析师结论
        analyst_results = analysis_result.get("analyst_results", [])
        if analyst_results:
            for r in analyst_results:
                name = r.get("name", "")
                conclusion = r.get("conclusion", "")
                if conclusion and conclusion not in ("已跳过", "未执行"):
                    parts.append(f"  - {name}: {conclusion}")

        return "\n".join(parts)

    # ============================================================
    # 任务管理
    # ============================================================

    async def create_task(self, user_id: str, mode: str = "paper") -> Dict[str, Any]:
        """创建AI交易任务"""
        task_id = str(uuid.uuid4())

        try:
            db = get_mongo_db()

            # 防止并发执行
            running_task = await db.ai_trading_tasks.find_one(
                {"user_id": user_id, "status": {"$in": ["pending", "running"]}}
            )
            if running_task:
                raise ValueError(
                    f"已有AI交易任务正在执行（ID: {running_task['task_id'][:8]}...），"
                    f"请等待当前任务完成后再试"
                )

            await db.ai_trading_tasks.insert_one({
                "task_id": task_id,
                "user_id": user_id,
                "mode": mode,
                "status": "pending",
                "progress": 0,
                "current_step": "",
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            })
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"保存AI交易任务到MongoDB失败: {e}")

        return {"task_id": task_id, "status": "pending", "message": "AI交易任务已创建"}

    async def execute_task(self, task_id: str, user_id: str = None, mode: str = "paper"):
        """执行AI交易任务（后台运行）

        完整流程：
        1. 获取账户持仓情况
        2. 如果有持仓：并发调用"单个股票分析" + "AI选股"
           如果无持仓：仅调用"AI选股"
        3. 仓位管理分析师：综合数据给出买卖信号
        4. 交易决策分析师：审核信号，执行下单
        """
        start_time = time.time()
        api_cache = ApiCache()

        analyst_results = []
        account_info = None
        positions = []
        position_analysis_results = {}
        selector_result = None
        trading_signals = []
        order_results = []
        decision = None
        decision_report = ""

        try:
            await self._update_status(task_id, "running", 5, "正在初始化AI交易分析...")

            # 构建配置并创建LLM
            await self._update_status(task_id, "running", 8, "正在初始化AI模型...")
            config = await asyncio.to_thread(self._build_llm_config)
            quick_llm, deep_llm = await asyncio.to_thread(self._create_llm_instances, config)

            # ====== Step 1: 获取账户持仓 ======
            await self._update_status(task_id, "running", 10, "正在查询账户持仓...")

            qmt = self._get_qmt_util()
            with qmt:
                account_info = qmt.get_account_info()
                positions = qmt.get_positions()

            has_position = len(positions) > 0
            logger.info(f"AI交易 账户: cash={account_info.cash}, total_value={account_info.total_value}, "
                        f"持仓数={len(positions)}")

            analyst_results.append({
                "name": "账户查询",
                "conclusion": f"持仓{len(positions)}只" if has_position else "空仓",
                "tag_type": "info",
                "content": f"可用资金: {account_info.cash:.2f}元\n"
                           f"总资产: {account_info.total_value:.2f}元\n"
                           f"冻结资金: {account_info.frozen_cash:.2f}元\n"
                           f"持仓: {len(positions)}只股票"
                           + (f"\n\n" + "\n".join(
                               f"- {p.code} {p.name}: {p.volume}股, "
                               f"成本{p.cost_price:.2f}, 现价{p.current_price:.2f}, "
                               f"盈亏{p.unrealized_pnl:+.2f}元"
                               for p in positions
                           ) if has_position else ""),
            })

            # ====== Step 2: 并发调用分析+选股 ======
            if has_position:
                await self._update_status(task_id, "running", 15, "正在并发执行持仓分析+AI选股...")

                # 并发执行：个股分析 + AI选股
                analysis_task = self._run_position_analysis(positions, quick_llm, deep_llm, api_cache)
                selector_task = self._run_ai_selector(quick_llm, deep_llm, api_cache)

                analysis_result, selector_result = await asyncio.gather(
                    analysis_task, selector_task
                )

                # 记录分析结果
                for code, result in analysis_result.items():
                    pos = next((p for p in positions if p.code == code), None)
                    stock_name = pos.name if pos else code
                    analyst_results.append({
                        "name": f"个股分析-{stock_name}",
                        "conclusion": self._extract_analysis_conclusion(result),
                        "tag_type": self._get_analysis_tag_type(result),
                        "content": result.get("report", "分析未产出结果"),
                    })
                    position_analysis_results[code] = result

                # 记录选股结果
                if selector_result:
                    selector_analyst_results = selector_result.get("analyst_results", [])
                    for r in selector_analyst_results:
                        if r.get("conclusion") not in ("已跳过", "未执行"):
                            analyst_results.append({
                                "name": f"AI选股-{r['name']}",
                                "conclusion": r.get("conclusion", ""),
                                "tag_type": r.get("tag_type", "info"),
                                "content": r.get("content", ""),
                            })
            else:
                # 无持仓：仅运行AI选股
                await self._update_status(task_id, "running", 15, "空仓，正在执行AI选股...")
                selector_result = await self._run_ai_selector(quick_llm, deep_llm, api_cache)

                if selector_result:
                    selector_analyst_results = selector_result.get("analyst_results", [])
                    for r in selector_analyst_results:
                        if r.get("conclusion") not in ("已跳过", "未执行"):
                            analyst_results.append({
                                "name": f"AI选股-{r['name']}",
                                "conclusion": r.get("conclusion", ""),
                                "tag_type": r.get("tag_type", "info"),
                                "content": r.get("content", ""),
                            })

            # ====== Step 3: 仓位管理分析师 ======
            await self._update_status(task_id, "running", 60, "仓位管理分析师生成买卖信号...")

            positions_info = self._format_positions_for_prompt(positions)
            position_analysis_str = "\n\n".join(
                self._format_analysis_result_for_prompt(code, "", result)
                for code, result in position_analysis_results.items()
            ) if position_analysis_results else "无持仓个股分析结果（空仓）"

            selector_str = self._format_selector_result_for_prompt(selector_result) if selector_result else "AI选股未产出结果"

            position_manager_report = await asyncio.to_thread(
                self._run_analyst, deep_llm, "仓位管理分析师",
                POSITION_MANAGER_PROMPT,
                extra_params={
                    "current_time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                    "cash": f"{account_info.cash:.2f}",
                    "total_value": f"{account_info.total_value:.2f}",
                    "frozen_cash": f"{account_info.frozen_cash:.2f}",
                    "positions_info": positions_info,
                    "position_analysis_results": position_analysis_str,
                    "selector_results": selector_str,
                }
            )

            trading_signals = self._extract_trading_signals(position_manager_report)
            logger.info(f"AI交易 仓位管理分析师信号: {trading_signals}")

            # 提取结论标签
            if trading_signals:
                buy_count = sum(1 for s in trading_signals if s.get("action") == "买入")
                sell_count = sum(1 for s in trading_signals if s.get("action") == "卖出")
                conclusion = f"买入{buy_count}只, 卖出{sell_count}只"
            else:
                conclusion = "无交易信号（建议持有/观望）"

            analyst_results.append({
                "name": "仓位管理分析师",
                "conclusion": conclusion,
                "tag_type": "warning" if trading_signals else "info",
                "content": position_manager_report,
            })

            # ====== Step 4: 交易决策分析师 ======
            if trading_signals:
                await self._update_status(task_id, "running", 80, "交易决策分析师审核信号...")

                # 格式化交易信号
                signals_str = "\n".join(
                    f"- {s.get('code', '')} {s.get('name', '')}: "
                    f"{s.get('action', '')} "
                    f"价格{s.get('price', '市价')} "
                    f"数量{s.get('volume', '')}股 "
                    f"金额{s.get('amount', '')}元 "
                    f"理由: {s.get('reason', '')}"
                    for s in trading_signals
                )

                trading_decision_report = await asyncio.to_thread(
                    self._run_analyst, deep_llm, "交易决策分析师",
                    TRADING_DECISION_PROMPT,
                    extra_params={
                        "current_time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                        "cash": f"{account_info.cash:.2f}",
                        "total_value": f"{account_info.total_value:.2f}",
                        "positions_info": positions_info,
                        "trading_signals": signals_str,
                    }
                )

                approved_signals = self._extract_approved_signals(trading_decision_report)
                logger.info(f"AI交易 审核通过信号: {approved_signals}")

                approved_count = len(approved_signals)
                rejected_count = len(trading_signals) - approved_count
                decision_conclusion = f"通过{approved_count}笔, 拒绝{rejected_count}笔"

                analyst_results.append({
                    "name": "交易决策分析师",
                    "conclusion": decision_conclusion,
                    "tag_type": "success" if approved_count > 0 else "info",
                    "content": trading_decision_report,
                })

                # ====== Step 5: 执行下单 ======
                if approved_signals and mode == "live":
                    await self._update_status(task_id, "running", 90, "正在执行下单...")
                    order_results = await asyncio.to_thread(
                        self._execute_orders, approved_signals
                    )
                elif approved_signals and mode == "paper":
                    await self._update_status(task_id, "running", 90, "模拟模式：生成模拟下单结果...")
                    order_results = self._simulate_orders(approved_signals)
                else:
                    order_results = []

                # 构建决策
                decision = self._build_decision(
                    trading_signals, approved_signals, order_results,
                    position_manager_report, mode
                )
                decision_report = trading_decision_report
            else:
                # 无交易信号
                decision = {
                    "action": "观望",
                    "reasoning": "仓位管理分析师未给出买卖信号，建议维持当前仓位或观望",
                    "position_suggestion": "维持当前仓位不变" if has_position else "空仓观望",
                    "risk_warning": "当前无明确交易机会",
                }
                decision_report = position_manager_report

            # ====== 保存完整结果 ======
            elapsed = time.time() - start_time

            # 序列化positions
            positions_data = []
            for p in positions:
                positions_data.append({
                    "code": p.code,
                    "name": p.name,
                    "volume": p.volume,
                    "cost_price": p.cost_price,
                    "current_price": p.current_price,
                })

            result = {
                "task_id": task_id,
                "status": "completed",
                "progress": 100,
                "current_step": "交易完成",
                "elapsed_time": round(elapsed, 2),
                "mode": mode,
                "account_info": {
                    "cash": account_info.cash,
                    "total_value": account_info.total_value,
                    "frozen_cash": account_info.frozen_cash,
                },
                "positions": positions_data,
                "analyst_results": analyst_results,
                "trading_signals": trading_signals,
                "order_results": order_results,
                "decision": decision,
                "decision_report": decision_report,
                "completed_at": datetime.utcnow().isoformat(),
            }

            try:
                db = get_mongo_db()
                serializable_result = self._make_serializable(result)
                await db.ai_trading_tasks.update_one(
                    {"task_id": task_id},
                    {"$set": {
                        "status": "completed",
                        "progress": 100,
                        "current_step": "交易完成",
                        "result": serializable_result,
                        "elapsed_time": round(elapsed, 2),
                        "updated_at": datetime.utcnow(),
                    }}
                )
            except Exception as e:
                logger.error(f"保存AI交易结果到MongoDB失败: {e}")

            return result

        except Exception as e:
            logger.error(f"AI交易任务执行失败: {e}", exc_info=True)
            await self._update_status(task_id, "failed", 0, f"交易失败: {str(e)}", error_message=str(e))
            raise
        finally:
            api_cache.clear()

    # ============================================================
    # 并发分析子任务
    # ============================================================

    async def _run_position_analysis(self, positions: List[Position],
                                     quick_llm, deep_llm,
                                     api_cache: ApiCache) -> Dict[str, Dict]:
        """并发分析所有持仓股票"""
        tasks = {}
        for pos in positions:
            tasks[pos.code] = self._analyze_single_stock(
                pos.code, pos.name, quick_llm, deep_llm, api_cache
            )

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        output = {}
        for code, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                logger.error(f"持仓分析 {code} 失败: {result}")
                output[code] = {"report": f"分析失败: {str(result)}", "error": str(result)}
            else:
                output[code] = result

        return output

    HOLDING_ANALYSIS_PROMPT = """你是一位资深的股票分析师，请对以下股票进行快速分析评估。

## 股票信息
- 代码：{code}
- 名称：{name}
- 当前持仓：{volume}股
- 成本价：{cost_price}
- 当前价：{current_price}
- 浮动盈亏：{pnl}元（{pnl_pct}%）

## 持仓数据
{position_data}

---

请从以下维度进行简要分析：

1. **基本面判断**：该股票所属行业当前景气度如何？公司基本面是否健康？
2. **技术面判断**：当前价格相对成本价的位置如何？短期趋势向上还是向下？
3. **持仓建议**：基于当前盈亏情况，建议继续持有、加仓还是减仓？

在分析报告的最后，请用如下JSON格式输出结构化结论（放在```json代码块中）：
```json
{{
  "action": "持有/加仓/减仓/清仓",
  "confidence": 0.7,
  "target_price": 建议目标价(数字),
  "stop_loss_price": 建议止损价(数字),
  "reasoning": "简要理由"
}}
```

使用中文输出，简洁专业。"""

    async def _analyze_single_stock(self, code: str, name: str,
                                     quick_llm, deep_llm,
                                     api_cache: ApiCache) -> Dict:
        """分析单只持仓股票（使用LLM直接分析）"""
        logger.info(f"AI交易 持仓分析: {code} {name}")

        try:
            # 获取该股票的实时数据
            position_data = self._get_stock_market_data(code, api_cache)

            # 查找持仓信息
            qmt = self._get_qmt_util()
            with qmt:
                positions = qmt.get_positions()
            pos = next((p for p in positions if p.code == code), None)

            volume = pos.volume if pos else 0
            cost_price = pos.cost_price if pos else 0
            current_price = pos.current_price if pos else 0
            pnl = (current_price - cost_price) * volume if pos else 0
            pnl_pct = ((current_price - cost_price) / cost_price * 100) if pos and cost_price > 0 else 0

            report = await asyncio.to_thread(
                self._run_analyst, quick_llm, f"持仓分析-{name}",
                self.HOLDING_ANALYSIS_PROMPT,
                extra_params={
                    "code": code,
                    "name": name,
                    "volume": str(volume),
                    "cost_price": f"{cost_price:.2f}",
                    "current_price": f"{current_price:.2f}",
                    "pnl": f"{pnl:+.2f}",
                    "pnl_pct": f"{pnl_pct:+.2f}",
                    "position_data": position_data,
                }
            )

            # 提取JSON结论
            conclusion_data = self._extract_json_block(report)

            return {
                "report": report,
                "decision": conclusion_data or {"action": "持有", "reasoning": "分析未产出结构化结论"},
            }
        except Exception as e:
            logger.error(f"持仓分析 {code} 失败: {e}")
            return {"report": f"分析失败: {str(e)}", "decision": {"action": "持有", "reasoning": f"分析失败: {str(e)}"}}

    def _get_stock_market_data(self, code: str, api_cache: ApiCache) -> str:
        """获取股票市场数据用于分析"""
        try:
            import akshare as ak
            import pandas as pd

            parts = []

            # 获取近期K线数据
            try:
                # akshare使用不带后缀的代码
                ak_code = code.replace(".SH", "").replace(".SZ", "")
                daily = api_cache.call(f"stock_zh_a_hist:{ak_code}", ak.stock_zh_a_hist,
                                       symbol=ak_code, period="daily", adjust="qfq")
                if daily is not None and not daily.empty:
                    recent = daily.tail(20)
                    parts.append(f"近20日K线数据（最新{len(recent)}条）：")
                    for _, row in recent.iterrows():
                        date_str = str(row.get("日期", row.get("date", "")))
                        close = row.get("收盘", row.get("close", 0))
                        volume = row.get("成交量", row.get("volume", 0))
                        parts.append(f"  {date_str}: 收盘{close}, 成交量{volume}")
            except Exception as e:
                parts.append(f"获取K线数据失败: {e}")

            return "\n".join(parts) if parts else "暂无市场数据"
        except ImportError:
            return "akshare未安装，暂无市场数据"
        except Exception as e:
            return f"获取市场数据失败: {e}"

    async def _run_ai_selector(self, quick_llm, deep_llm,
                               api_cache: ApiCache) -> Dict:
        """运行AI选股完整流程（直接调用AiSelectorService.run_analysis）"""
        logger.info("AI交易 并发运行AI选股...")

        try:
            service = AiSelectorService()
            result = await service.run_analysis(quick_llm, deep_llm, api_cache)
            return result
        except Exception as e:
            logger.error(f"AI交易 AI选股失败: {e}")
            return {"decision": None, "analyst_results": [], "error": str(e),
                    "early_stop": True, "early_stop_reason": str(e)}

    # ============================================================
    # 下单执行
    # ============================================================

    def _execute_orders(self, approved_signals: List[Dict]) -> List[Dict]:
        """执行审核通过的交易信号（实盘）"""
        results = []
        qmt = self._get_qmt_util()

        with qmt:
            for signal in approved_signals:
                code = signal.get("code", "")
                name = signal.get("name", "")
                action = signal.get("action", "")
                price = signal.get("price")
                volume = signal.get("volume")
                amount = signal.get("amount")

                try:
                    if action == "买入":
                        order_id = qmt.buy(
                            code=code,
                            amount=amount or 0,
                            price=price,
                        )
                    elif action == "卖出":
                        order_id = qmt.sell(
                            code=code,
                            price=price,
                            volume=volume,
                        )
                    else:
                        order_id = None

                    results.append({
                        "code": code,
                        "name": name,
                        "action": action,
                        "price": price,
                        "volume": volume,
                        "order_id": order_id,
                        "success": order_id is not None,
                        "error": None if order_id else "下单失败",
                    })
                except Exception as e:
                    results.append({
                        "code": code,
                        "name": name,
                        "action": action,
                        "price": price,
                        "volume": volume,
                        "order_id": None,
                        "success": False,
                        "error": str(e),
                    })

        return results

    def _simulate_orders(self, approved_signals: List[Dict]) -> List[Dict]:
        """模拟下单（模拟模式）"""
        import random
        results = []

        qmt = self._get_qmt_util()
        with qmt:
            for signal in approved_signals:
                code = signal.get("code", "")
                name = signal.get("name", "")
                action = signal.get("action", "")
                price = signal.get("price")
                volume = signal.get("volume")
                amount = signal.get("amount")

                try:
                    if action == "买入":
                        order_id = qmt.buy(code=code, amount=amount or 0, price=price)
                    elif action == "卖出":
                        order_id = qmt.sell(code=code, price=price, volume=volume)
                    else:
                        order_id = None

                    results.append({
                        "code": code,
                        "name": name,
                        "action": action,
                        "price": price,
                        "volume": volume,
                        "order_id": f"MOCK-{order_id}" if order_id else None,
                        "success": order_id is not None,
                        "error": None if order_id else "模拟下单失败",
                    })
                except Exception as e:
                    results.append({
                        "code": code,
                        "name": name,
                        "action": action,
                        "price": price,
                        "volume": volume,
                        "order_id": None,
                        "success": False,
                        "error": str(e),
                    })

        return results

    def _build_decision(self, trading_signals, approved_signals,
                        order_results, report, mode) -> Dict:
        """构建最终决策"""
        buy_signals = [s for s in approved_signals if s.get("action") == "买入"]
        sell_signals = [s for s in approved_signals if s.get("action") == "卖出"]

        if buy_signals and sell_signals:
            action = "调仓换股"
        elif buy_signals:
            action = "建仓买入"
        elif sell_signals:
            action = "减仓卖出"
        else:
            action = "观望"

        success_orders = [o for o in order_results if o.get("success")]
        failed_orders = [o for o in order_results if not o.get("success")]

        reasoning_parts = []
        if buy_signals:
            buy_names = [f"{s.get('code', '')} {s.get('name', '')}" for s in buy_signals]
            reasoning_parts.append(f"买入: {', '.join(buy_names)}")
        if sell_signals:
            sell_names = [f"{s.get('code', '')} {s.get('name', '')}" for s in sell_signals]
            reasoning_parts.append(f"卖出: {', '.join(sell_names)}")
        if failed_orders:
            reasoning_parts.append(f"下单失败{len(failed_orders)}笔")

        mode_label = "模拟" if mode == "paper" else "实盘"
        if success_orders:
            reasoning_parts.append(f"({mode_label}模式: {len(success_orders)}笔订单已提交)")

        return {
            "action": action,
            "reasoning": "; ".join(reasoning_parts) if reasoning_parts else "无交易操作",
            "position_suggestion": f"共{len(approved_signals)}笔交易信号已审核通过",
            "risk_warning": f"{'模拟模式' if mode == 'paper' else '实盘模式'}运行，"
                            f"成功{len(success_orders)}笔，失败{len(failed_orders)}笔",
        }

    # ============================================================
    # 辅助方法
    # ============================================================

    def _extract_analysis_conclusion(self, result: Dict) -> str:
        """从个股分析结果中提取结论"""
        decision = result.get("decision", {})
        if decision:
            return decision.get("action", "未知")
        if result.get("error"):
            return "分析失败"
        return "分析完成"

    def _get_analysis_tag_type(self, result: Dict) -> str:
        """获取个股分析结论标签类型"""
        decision = result.get("decision", {})
        if decision:
            action = decision.get("action", "")
            if "强烈推荐" in action or "买入" in action:
                return "success"
            elif "卖出" in action or "规避" in action:
                return "danger"
            elif "观望" in action:
                return "info"
        if result.get("error"):
            return "danger"
        return "info"

    def _make_serializable(self, obj):
        """将对象转换为可序列化的格式"""
        if isinstance(obj, dict):
            return {k: self._make_serializable(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._make_serializable(v) for v in obj]
        elif isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, (int, float, str, bool, type(None))):
            return obj
        else:
            return str(obj)

    async def _update_status(self, task_id: str, status: str, progress: int,
                             current_step: str, error_message: str = None):
        """更新任务状态"""
        try:
            db = get_mongo_db()
            update_data = {
                "status": status,
                "progress": progress,
                "current_step": current_step,
                "updated_at": datetime.utcnow(),
            }
            if error_message:
                update_data["error_message"] = error_message
            await db.ai_trading_tasks.update_one(
                {"task_id": task_id},
                {"$set": update_data}
            )
        except Exception as e:
            logger.error(f"更新AI交易任务状态失败: {e}")

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
            if task and task.get("status") == "completed":
                return task.get("result", task)
            return task
        except Exception as e:
            logger.error(f"获取AI交易任务结果失败: {e}")
            return None

    async def get_records(self, user_id: str, mode: str = None,
                          page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """获取AI交易操作记录"""
        try:
            db = get_mongo_db()
            query = {"user_id": user_id}
            if mode:
                query["mode"] = mode

            total = await db.ai_trading_tasks.count_documents(query)
            skip = (page - 1) * page_size
            cursor = db.ai_trading_tasks.find(
                query,
                {"_id": 0, "result.analyst_results": 0, "result.decision_report": 0}
            ).sort("created_at", -1).skip(skip).limit(page_size)
            tasks = await cursor.to_list(length=page_size)

            # 转换为前端需要的记录格式
            items = []
            for task in tasks:
                result = task.get("result", {})
                decision = result.get("decision", {})
                trading_signals = result.get("trading_signals", [])

                # 提取涉及的股票
                stocks = []
                for s in trading_signals:
                    stocks.append({"code": s.get("code", ""), "name": s.get("name", "")})

                items.append({
                    "id": task.get("task_id", ""),
                    "mode": task.get("mode", "paper"),
                    "action": decision.get("action", ""),
                    "status": task.get("status", ""),
                    "stocks": stocks,
                    "detail": decision.get("reasoning", ""),
                    "elapsed_time": result.get("elapsed_time"),
                    "created_at": task.get("created_at", ""),
                    "updated_at": task.get("updated_at", ""),
                })

            return {"items": items, "total": total, "page": page, "page_size": page_size}
        except Exception as e:
            logger.error(f"获取AI交易记录失败: {e}")
            return {"items": [], "total": 0, "page": page, "page_size": page_size}


# 单例
_ai_trading_service: Optional[AiTradingService] = None
_ai_trading_service_lock = threading.Lock()


def get_ai_trading_service() -> AiTradingService:
    global _ai_trading_service
    if _ai_trading_service is None:
        with _ai_trading_service_lock:
            if _ai_trading_service is None:
                _ai_trading_service = AiTradingService()
    return _ai_trading_service
