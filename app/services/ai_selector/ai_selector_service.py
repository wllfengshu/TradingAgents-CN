"""
AI选股服务
6个分析师Agent协同工作：大盘分析师、主线板块分析师、市场合力分析师、
股票龙头分析师、风险分析师、决策分析师
每个Agent先使用代码计算指标，然后将计算结果发送给LLM分析
"""

import re
import asyncio
import uuid
import json
import logging
import time
import threading
from datetime import datetime
from typing import Dict, Any, Optional, List
from zoneinfo import ZoneInfo
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
from app.utils.json_compressor import compress_json_for_llm
from app.utils.stock_utils import make_serializable, is_main_board_stock, extract_json_block
from langchain_core.messages import HumanMessage, SystemMessage
from tradingagents.graph.trading_graph import TradingAgentsGraph
from app.services.simple_analysis_service import create_analysis_config, get_provider_and_url_by_model_sync
from app.core.database import get_mongo_db
import pandas as pd
from app.utils.api_cache import ApiCache
from app.services.ai_selector.compute_indicators import (
    compute_market_indicators,
    compute_sector_indicators,
    compute_force_indicators,
    compute_leader_indicators,
    compute_risk_indicators,
)
from app.services.model_capability_service import get_model_capability_service
from croniter import croniter

logger = logging.getLogger(__name__)

_CN_TZ = ZoneInfo("Asia/Shanghai")

def _now_cn() -> datetime:
    """返回上海时区"""
    return datetime.now(_CN_TZ).replace(tzinfo=None)


# ============================================================
# Agent提示词模板
# ============================================================

MARKET_ANALYST_PROMPT = """你是一位资深的大盘分析师，专注于分析A股大盘走势与市场环境。

以下是通过代码计算获取的最新市场指标数据：

{indicators_data}

**数据说明**：
- "近5日收盘价(从旧到新)" 提供了上证/深证最近5个交易日的收盘序列（共5个数据点），请据此判断趋势方向。
- "5日涨跌幅(%)" 基于 **5个bar区间**（即T-5交易日收盘价 → T日收盘价，共5个区间），与近5日序列的口径略有不同——前者衡量区间累计涨跌，后者展示逐日走势，两者需结合看：序列方向一致且区间涨跌幅同向，趋势信号更可靠。
- "北向ETF方向信号_涨跌方向" 以A50/MSCI/互联互通ETF的平均涨跌幅判断外资情绪（偏多/偏空/中性），比ETF成交额更可靠。
- "北向资金净持仓聚合.净方向" 是基于全量北向持股增持估计市值求和的净方向（净增持/净减持/持平），与ETF方向信号结合：**两者同向则外资信号强，两者分歧则信号弱**，需降低权重。

请基于上述数据，从以下维度进行分析：

1. **指数走势判断**：结合近5日收盘价序列和5日区间涨跌幅，判断上证/深成当前处于上涨、下跌还是震荡趋势？幅度如何？
2. **外资方向分析**：对比"北向ETF方向信号"与"北向资金净持仓聚合"两个指标是否同向？同向则外资信号可靠；若分歧，说明外资行为分化，应降低权重。重点关注南向资金和行业资金集中度。
3. **涨跌比分析**：上涨/下跌家数比值如何？是普涨（上涨占比>60%）、普跌（<40%）还是分化？
4. **综合判断**：综合指数趋势、资金方向、市场广度，给出偏多/偏空/中性结论。

请给出明确的大盘分析结论：
- 大盘环境：偏多/偏空/中性
- 核心依据（2-3条，引用具体数据）
- 重点关注的风险点

在分析报告的最后，请用如下JSON格式输出结构化结论（放在```json代码块中）：
```json
{{
  "market_sentiment": "偏多/偏空/中性",
  "confidence": 0.8,
  "trend_5d": "上涨/下跌/震荡",
  "key_points": ["依据1（含数据）", "依据2（含数据）"]
}}
```

使用中文输出，简洁专业。"""

SECTOR_ANALYST_PROMPT = """你是一位资深的主线板块分析师，专注于识别当前市场的热点主线板块。

## 上游大盘分析师结论
{market_summary}

以下是通过代码计算获取的最新板块指标数据：

{indicators_data}

**数据说明**：
- "涨幅前10板块" 包含今日涨跌幅，以及数据源提供的5日/10日涨跌幅（如有），请优先引用这些字段判断板块强度而非主观猜测。
- "涨停统计.涨停股列表" 包含各涨停股的所属行业/板块字段（如有），请统计哪些行业的涨停股最多，判断资金聚焦方向。
- "强势股池统计" 是连续涨停2板及以上的股票，连板数越高说明市场热度越集中。
- "封板比统计" 反映涨停质量：平均封板比越高（尤其>1），说明主力锁仓意愿越强，涨停板越牢固，次日溢价概率更高。
- "炸板统计" 反映市场获利了结意愿：炸板率<10%为情绪偏强；≥25%为情绪偏弱，追板需谨慎；≥40%为情绪极度不稳，建议不追。

请基于上述数据，从以下维度进行分析：

1. **板块涨停集中度**：统计涨停股中各行业/板块的数量，哪个方向涨停股最多？是否有明确的板块效应？
2. **板块持续强度**：若数据中包含5日/10日涨跌幅，对比今日与近期涨幅判断是启动还是持续；若无多日数据，说明仅做今日判断。
3. **连板高度与市场情绪**：
   - 当前市场连板最多的股票是几板？高度板存在说明情绪如何？
   - 结合**封板比**判断涨停质量：封板比高=主力真实锁仓，封板比低=散户堆砌，容易炸板。
   - 结合**炸板率**判断市场惜售意愿：炸板率高说明获利盘多，市场情绪脆弱，不宜激进追板。
4. **主线认定**：结合上述维度，哪个板块具备"量升、涨停集中、少炸板、封板比高"的主线特征？

请给出明确的板块分析结论：
- 当前主线板块（1-2个，须有数据支撑）
- 核心逻辑（各板块走强的具体依据，引用数据）
- 持续性判断：持续/一日游，依据是什么？（必须引用封板比或炸板率数据）

在分析报告的最后，请用如下JSON格式输出结构化结论（放在```json代码块中）：
```json
{{
  "has_main_sector": true,
  "main_sectors": ["板块1", "板块2"],
  "max_consecutive_limit": 3,
  "avg_seal_ratio": 0.8,
  "broken_limit_rate": 15.0,
  "sustainability": "持续/一日游",
  "reasoning": "简要逻辑（含具体数据依据）"
}}
```
注意：只有当板块涨停数量明显集中（同一板块≥3支涨停）、或有高度连板（≥3板）时，才判断has_main_sector为true；否则设为false。
若炸板率≥40%，无论涨停数量多少，sustainability必须设为"一日游"。

使用中文输出，简洁专业。"""

FORCE_ANALYST_PROMPT = """你是一位资深的市场合力分析师，专注于分析主力与散户的资金动向。

## 上游分析师传入的主线板块
主线板块分析师识别出以下主线板块：{sector_themes}
请重点围绕这些主线板块分析资金合力。

以下是通过代码计算获取的最新资金流向数据：

{indicators_data}

**重要约束**：
- 你只能从"个股主力净流入前20"列表中推荐股票，**严禁推荐列表中未出现的股票代码**。
- 如果该列表中没有属于主线板块的股票，则recommended_stocks返回空数组，不得凭空捏造。
- 推荐时必须引用列表中的"代码"字段，确保准确。

请基于上述数据，从以下维度进行分析：

1. **行业资金流向**：主力资金净流入哪些行业？净流出哪些行业？重点关注主线板块（{sector_themes}）的资金净额情况。
2. **个股资金交叉验证**：在"个股主力净流入前20"中，哪些股票属于主线板块？它们的净额、换手率、涨跌幅配合情况如何？
3. **合力判断**：行业资金与个股资金方向是否一致？是"正向共振"（行业+个股同向净流入）、"主力主导"（行业流入但个股分散）还是"反向分歧"？
4. **量价配合**：净流入的股票，其换手率是否同步放大？换手率>3%且净流入为正是强信号。

请给出明确的合力分析结论：
- 合力方向：正向共振/反向分歧/主力主导
- 资金最集中的方向（行业+具体股票）
- 需要警惕的资金信号
- **从"个股主力净流入前20"列表中，筛选出属于主线板块且净额最高的2到3支股票**

在分析报告的最后，请用如下JSON格式输出结构化结论（放在```json代码块中）：
```json
{{
  "force_direction": "正向共振/反向分歧/主力主导",
  "recommended_stocks": [
    {{"code": "股票代码（必须来自数据列表）", "name": "股票名称", "net_inflow": "净流入金额", "reason": "属于主线板块且具体依据"}}
  ]
}}
```
注意：recommended_stocks中的股票代码必须完全匹配"个股主力净流入前20"中的代码；如列表中确无主线板块股票，返回空数组。

使用中文输出，简洁专业。"""

LEADER_ANALYST_PROMPT = """你是一位资深的股票龙头分析师，专注于筛选板块龙头与连板强势股。

## 上游分析师传入的候选股票
市场合力分析师筛选出以下候选股票（这些是你重点分析的对象）：
{candidate_stocks}

以下是通过代码计算获取的最新龙头指标数据（用于交叉验证候选股的龙头属性）：

{indicators_data}

**⚠️ A股 T+1 风险提示（必须考虑）**：
- A股实行T+1制度：当日买入的涨停股次日才能卖出，存在隔夜缺口风险。
- 历史数据显示，3板以上高位连板股次日开板（炸板）后低开概率显著上升，需在推荐时明确标注连板位置风险。
- **优先推荐"低位首板"**（距近30日最高价跌幅≥30%的首次涨停）：启动位置低、获利盘少、次日溢价概率更高。
- **谨慎推荐高位连板（≥3板且股价接近近期高位）**：即便今日涨停，若连板高度已远超同期市场平均水平，追高风险极大，须在 reason 中明确说明。

**分析指引**：
- 优先在"涨停龙头股前20"中查找候选股票的代码，若出现则说明今日已涨停，重点关注其"连板数"字段——连板数越高龙头地位越稳。
- 优先在"涨停龙头股前20"中查找候选股票的代码，若出现则说明今日已涨停，重点关注其"连板数"字段——连板数越高龙头地位越稳，但同时追高风险越大。
- 若候选股在两个列表中均未出现，说明今日表现一般，需降低评级。
- "换手率"是流动性和市场关注度的重要指标，>5%为活跃标志。

请基于上述分析，从以下维度研判候选股：

1. **连板属性与位置风险**：候选股中是否有连板股？连板数是多少？在当前市场连板高度中处于什么位置（龙头/跟风/尾部）？股价是否处于低位启动（近30日跌幅明显）还是高位追涨（接近近期高点）？
2. **龙头地位**：该股今日是否涨停或涨幅居前？在所属板块中是领头羊还是跟风股？
3. **量价配合**：换手率与成交额是否同步放大？是否有"放量涨停"或"缩量跌停"等异常信号？
4. **板块共振**：候选股所属板块今日表现如何？龙头股应与板块形成共振，而非逆势。

请从候选股票中筛选出最多1到2支龙头股（宁缺毋滥）：
- 龙头股推荐及理由（必须引用数据中的具体字段值）
- 所属板块及该板块今日表现
- 龙头强度评级：强（低位连板≥2或低位首板封板比高）、中（涨停但高位或非连板）、弱（未涨停）
- **T+1风险评估**：明确说明该股是否为高位连板（>=3板且接近近期高点），以便决策分析师综合风险

在分析报告的最后，请用如下JSON格式输出结构化结论（放在```json代码块中）：
```json
{{
  "leading_stocks": [
    {{"code": "股票代码", "name": "股票名称", "sector": "所属板块", "consecutive_limit": 连板数或0, "strength": "强/中/弱", "in_zt_pool": true或false, "position_risk": "低位/中位/高位", "t1_risk_note": "T+1隔夜风险说明"}}
  ]
}}
```
注意：leading_stocks只推荐能从数据中找到支撑的股票；如候选股全部表现平庸或全部为高位高风险，leading_stocks返回空数组。

使用中文输出，简洁专业。"""

RISK_ANALYST_PROMPT = """你是一位资深的风险分析师，专注于排除高风险标的，保障投资安全边际。

## 强制风险排查规则（不可违背）
1. **ST / *ST**：名称含 ST 或 *ST 的股票，直接标记为高风险，必须排除，禁止买入。
2. **退市风险**：名称含"退市"或"退"字的股票，直接标记为高风险，必须排除。
3. **停牌股票**：名称含"停牌"或"停"字的股票，直接标记为高风险，必须排除。
4. **板块限制**：只允许买入沪深主板股票（代码以 600/601/603/605/000/001/002/003 开头），
   排除科创板（688）、创业板（300/301）、北交所（8 开头）。

以下是通过代码计算获取的最新风险指标数据：

{indicators_data}

**数据说明**：
- "候选标的实时行情" 包含上游龙头分析师推荐股票的实时价格、涨跌幅、成交量、换手率等，请基于这些**真实数据**评估涨幅异常和流动性风险，而非主观猜测。
- "次新股" 列表用于判断推荐标的是否为上市不满60个交易日的次新股。

同时，上游龙头分析师推荐了以下标的：
{recommended_stocks}

请逐一对每只推荐股进行如下风险核查：

1. **名称合规检查**：股票名称是否含 ST/*ST/退市/停牌？（对照候选标的实时行情中的"名称"字段）
2. **代码合规检查**：代码是否属于主板范围？（600/601/603/605/000/001/002/003开头为主板）
3. **次新股核查**：是否出现在次新股列表中？
4. **涨幅异常核查**：基于实时行情中的"涨跌幅"，若今日涨跌幅>9.8%（即涨停），叠加是否已有多个连板，评估追高风险。
5. **高位连板追板风险（T+1隔夜风险）**：
   - 若龙头分析师在 t1_risk_note 中标注"高位"且连板数≥3，评估为**高追板风险**——须在 excluded_stocks 中标注追高风险，或在 safe_stocks 中附加警示。
   - 原则：**低位（近30日内涨幅<30%的首次涨停）优先于高位连板**；高位3板及以上，非极端强势市场不建议追入。
6. **基本面安全底线**（参考"候选标的基本面"数据）：
   - 市盈率(动) PE < 0（亏损）：标记为中高风险，须说明亏损状态。
   - 市盈率(动) PE > 200 或无法获取：说明估值极高或数据缺失，需特别注意。
   - 总市值 < 20亿 ：流通盘极小，容易被操控，标记为中风险。
   - 若无法获取基本面数据，在报告中注明"基本面数据未获取，该维度无法评估"，不得因此直接排除。

请给出明确的风险分析结论：
- 对每只推荐标的的逐项风险评估
- 需要排除的标的及具体原因（引用数据字段）
- 最终安全标的清单
- 整体风险评级：低（无风险项）/中（有1项警示）/高（有强制排除项）

在分析报告的最后，请用如下JSON格式输出结构化结论（放在```json代码块中）：
```json
{{
  "risk_level": "低/中/高",
  "safe_stocks": [
    {{"code": "股票代码", "name": "股票名称", "risk_notes": "风险提示（如高位连板T+1风险、PE偏高等；无则填无）"}}
  ],
  "excluded_stocks": [
    {{"code": "股票代码", "name": "股票名称", "reason": "排除原因（引用具体字段值）"}}
  ]
}}
```
注意：若safe_stocks为空（所有推荐标的均有风险），risk_level必须设为"高"。

使用中文输出，简洁专业。"""

DECISION_ANALYST_PROMPT = """你是一位资深的决策分析师，负责综合所有分析师的结论，给出最终的AI选股决策。

## 当前时间是：{current_time}
## 各分析师的分析结论

### 大盘分析师结论
{market_report}

### 主线板块分析师结论
{sector_report}

### 市场合力分析师结论
{force_report}

### 股票龙头分析师结论
{leader_report}

### 风险分析师结论
{risk_report}

### 风险分析师确认的安全标的
{safe_stocks_info}

请综合以上所有分析师的结论，给出最终的AI选股决策。注意：

**决策要点**：
1. **综合研判**：大盘5日趋势 + 今日情绪是否一致？如果趋势向上但今日偏空可能是短期波动，反之亦然。
2. **主线有效性**：板块分析师确认的主线，是否得到了合力分析师的资金验证？若两者结论一致才是强信号。
3. **标的质量**：安全标的中是否有连板龙头（优先级最高）、涨幅居前但未封板（次之）、普通强势股（最低）？
4. **仓位原则**：
   - 大盘偏多+主线明确+龙头确认 → 可用总仓位30~50%
   - 大盘中性或主线不明确 → 轻仓试探，不超过20%
   - 多只安全标的时，按连板数高低加权分配（龙头股占比高于跟风股）
5. **失败保护**：如安全标的为空或不确定性极高，action应为"观望"，不强制推荐。

请给出JSON格式的最终决策（放在```json代码块中，报告内容放在JSON之后）：
```json
{{
  "action": "强烈推荐/谨慎推荐/观望/规避",
  "stocks": [
    {{"code": "股票代码", "name": "股票名称", "reason": "推荐理由（含连板数/涨幅/板块等具体数据）"}}
  ],
  "reasoning": "综合决策的详细依据（说明大盘+板块+资金三者是否共振）",
  "position_suggestion": "具体仓位建议（如：总仓位40%，XXX股30%+YYY股10%）",
  "risk_warning": "风险提示（针对当前具体标的和市场环境）"
}}
```

在JSON之后，请用中文输出一份完整的决策分析报告，涵盖市场环境、主线逻辑、标的分析、仓位方案、风险管理五个章节。

使用中文输出，专业严谨。"""


# ============================================================
# AI选股服务主类
# ============================================================

class AiSelectorService:
    """AI选股服务"""

    def __init__(self):
        pass

    def _build_llm_config(self) -> Dict[str, Any]:
        """复用已有的模型配置逻辑
        """

        capability_service = get_model_capability_service()
        research_depth = "标准"

        # 从系统已启用的配置中自动推荐模型（与 simple_analysis_service 逻辑一致）
        quick_model, deep_model = capability_service.recommend_models_for_depth(research_depth)
        logger.info(f"AI选股 - 自动推荐模型: quick={quick_model}, deep={deep_model}")

        # 获取供应商信息
        quick_provider_info = get_provider_and_url_by_model_sync(quick_model)
        deep_provider_info = get_provider_and_url_by_model_sync(deep_model)

        quick_provider = quick_provider_info["provider"]
        deep_provider = deep_provider_info["provider"]
        quick_backend_url = quick_provider_info["backend_url"]
        deep_backend_url = deep_provider_info["backend_url"]

        logger.info(f"AI选股 - 快速模型 {quick_model} -> 供应商: {quick_provider}, URL: {quick_backend_url}")
        logger.info(f"AI选股 - 深度模型 {deep_model} -> 供应商: {deep_provider}, URL: {deep_backend_url}")

        # 使用create_analysis_config构建完整配置（与股票分析完全一致）
        config = create_analysis_config(
            research_depth=research_depth,
            selected_analysts=["market"],  # 仅用于初始化，不影响我们的自定义分析流程
            quick_model=quick_model,
            deep_model=deep_model,
            llm_provider=quick_provider,
            market_type="A股",
        )

        # 添加混合模式配置
        config["quick_provider"] = quick_provider
        config["deep_provider"] = deep_provider
        config["quick_backend_url"] = quick_backend_url
        config["deep_backend_url"] = deep_backend_url
        config["backend_url"] = quick_backend_url

        return config

    def _create_llm_instances(self, config: Dict[str, Any]):
        """通过TradingAgentsGraph创建正确配置的LLM实例
        """
        graph = TradingAgentsGraph(
            selected_analysts=config.get("selected_analysts", ["market"]),
            debug=False,
            config=config,
        )
        logger.info(f"AI选股 - TradingAgentsGraph创建成功，LLM已就绪")
        logger.info(f"  quick_llm: {graph.quick_thinking_llm.__class__.__name__}, model={getattr(graph.quick_thinking_llm, 'model_name', 'unknown')}")
        logger.info(f"  deep_llm: {graph.deep_thinking_llm.__class__.__name__}, model={getattr(graph.deep_thinking_llm, 'model_name', 'unknown')}")

        return graph.quick_thinking_llm, graph.deep_thinking_llm

    async def create_task(self, user_id: str) -> Dict[str, Any]:
        """创建AI选股任务"""
        task_id = str(uuid.uuid4())

        try:
            db = get_mongo_db()

            # 防止并发执行——同一用户有进行中的任务时拒绝新建
            running_task = await db.ai_selector_tasks.find_one(
                {"user_id": user_id, "status": {"$in": ["pending", "running"]}}
            )
            if running_task:
                raise ValueError(
                    f"已有AI选股任务正在执行（ID: {running_task['task_id'][:8]}...），"
                    f"请等待当前任务完成后再试"
                )

            await db.ai_selector_tasks.insert_one({
                "task_id": task_id,
                "user_id": user_id,
                "status": "pending",
                "progress": 0,
                "current_step": "",
                "created_at": _now_cn(),
                "updated_at": _now_cn(),
            })
        except ValueError:
            raise  # 业务异常直接向上抛出，不吞掉
        except Exception as e:
            logger.error(f"保存AI选股任务到MongoDB失败: {e}")

        return {"task_id": task_id, "status": "pending", "message": "AI选股任务已创建"}

    async def run_analysis(self, quick_llm, deep_llm, api_cache: ApiCache,
                           on_progress=None) -> Dict[str, Any]:
        """执行AI选股核心分析流程（不涉及任务管理/MongoDB，可供外部直接调用）

        Args:
            quick_llm: 快速模型LLM实例
            deep_llm: 深度模型LLM实例
            api_cache: API缓存实例
            on_progress: 可选的异步进度回调 async (progress: int, step: str) -> None

        Returns:
            包含 analyst_results, decision, decision_report, early_stop 等字段的字典
        """
        async def _report(progress: int, step: str):
            if on_progress:
                await on_progress(progress, step)
        analyst_results = []
        decision = None
        decision_report = ""
        early_stop_reason = ""

        market_report = ""
        sector_report = ""
        force_report = ""
        leader_report = ""
        risk_report = ""
        sector_themes_str = ""
        candidate_stocks_str = ""
        recommended_stocks_str = ""
        safe_stocks_info = ""
        leading_stocks: List[Dict] = []

        # ====== Step 1: 大盘分析师 ======
        await _report(15, "正在获取大盘数据...")
        market_indicators = await asyncio.to_thread(compute_market_indicators, api_cache)

        await _report(20, "大盘分析师正在分析...")
        market_report = await asyncio.to_thread(
            self._run_analyst, quick_llm, "大盘分析师", MARKET_ANALYST_PROMPT, market_indicators
        )
        analyst_results.append({
            "name": "大盘分析师",
            "conclusion": self._extract_conclusion(market_report, "大盘分析师"),
            "tag_type": self._get_conclusion_tag_type(market_report),
            "content": market_report,
        })

        market_sentiment = self._extract_market_sentiment(market_report)
        logger.info(f"AI选股 大盘情绪判断: {market_sentiment}")
        if market_sentiment == "偏空":
            early_stop_reason = "大盘环境偏空，建议观望，终止后续分析"
            logger.info(f"AI选股 提前终止: {early_stop_reason}")
            decision = {
                "action": "观望",
                "stocks": [],
                "reasoning": early_stop_reason,
                "position_suggestion": "空仓观望",
                "risk_warning": "大盘环境偏空，不宜入场",
            }
            decision_report = f"## 决策结论\n\n{early_stop_reason}\n\n大盘分析师判断当前大盘环境偏空，建议空仓观望，等待市场企稳后再考虑入场。"

        # ====== Step 2: 主线板块分析师 ======
        if not early_stop_reason:
            await _report(32, "正在获取板块数据...")
            sector_indicators = await asyncio.to_thread(compute_sector_indicators, api_cache)

            await _report(38, "主线板块分析师正在分析...")

            market_summary = self._extract_conclusion(market_report, "大盘分析师")
            sector_report = await asyncio.to_thread(
                self._run_analyst, quick_llm, "主线板块分析师", SECTOR_ANALYST_PROMPT, sector_indicators,
                extra_params={"market_summary": market_summary}
            )
            analyst_results.append({
                "name": "主线板块分析师",
                "conclusion": self._extract_conclusion(sector_report, "主线板块分析师"),
                "tag_type": self._get_conclusion_tag_type(sector_report),
                "content": sector_report,
            })

            sector_themes = self._extract_sector_themes(sector_report)
            logger.info(f"AI选股 主线板块: {sector_themes}")
            if not sector_themes:
                early_stop_reason = "当前市场无明显主线板块，资金分散，终止后续分析"
                logger.info(f"AI选股 提前终止: {early_stop_reason}")
                decision = {
                    "action": "观望",
                    "stocks": [],
                    "reasoning": early_stop_reason,
                    "position_suggestion": "空仓观望",
                    "risk_warning": "市场无明显主线，不宜追涨",
                }
                decision_report = f"## 决策结论\n\n{early_stop_reason}\n\n主线板块分析师未发现明显主线板块，市场资金分散无序，建议观望等待主线清晰后再入场。"
            else:
                sector_themes_str = "、".join(sector_themes)

        # ====== Step 3: 市场合力分析师 ======
        if not early_stop_reason:
            await _report(52, "正在获取资金流向数据...")
            force_indicators = await asyncio.to_thread(compute_force_indicators, api_cache)

            await _report(57, "市场合力分析师正在分析...")

            force_report = await asyncio.to_thread(
                self._run_analyst, quick_llm, "市场合力分析师", FORCE_ANALYST_PROMPT,
                force_indicators,
                extra_params={"sector_themes": sector_themes_str}
            )
            analyst_results.append({
                "name": "市场合力分析师",
                "conclusion": self._extract_conclusion(force_report, "市场合力分析师"),
                "tag_type": self._get_conclusion_tag_type(force_report),
                "content": force_report,
            })

            candidate_stocks = self._extract_candidate_stocks(force_report)
            logger.info(f"AI选股 合力分析师候选股票: {candidate_stocks}")
            if not candidate_stocks:
                early_stop_reason = "市场合力分析师未筛选出资金合力正向的股票，终止后续分析"
                logger.info(f"AI选股 提前终止: {early_stop_reason}")
                decision = {
                    "action": "观望",
                    "stocks": [],
                    "reasoning": early_stop_reason,
                    "position_suggestion": "空仓观望",
                    "risk_warning": "当前市场资金合力不足，不建议入场",
                }
                decision_report = f"## 决策结论\n\n{early_stop_reason}\n\n市场合力分析师围绕主线板块（{sector_themes_str}）分析后，未发现资金合力正向的标的，建议观望。"
            else:
                candidate_stocks_str = self._format_stocks_for_prompt(candidate_stocks)

        # ====== Step 4: 股票龙头分析师 ======
        if not early_stop_reason:
            await _report(68, "正在获取龙头股数据...")
            leader_indicators = await asyncio.to_thread(compute_leader_indicators, api_cache)

            await _report(73, "股票龙头分析师正在分析...")

            leader_report = await asyncio.to_thread(
                self._run_analyst, quick_llm, "股票龙头分析师", LEADER_ANALYST_PROMPT,
                leader_indicators,
                extra_params={"candidate_stocks": candidate_stocks_str}
            )
            analyst_results.append({
                "name": "股票龙头分析师",
                "conclusion": self._extract_conclusion(leader_report, "股票龙头分析师"),
                "tag_type": self._get_conclusion_tag_type(leader_report),
                "content": leader_report,
            })

            leading_stocks = self._extract_leading_stocks(leader_report)
            logger.info(f"AI选股 龙头股: {leading_stocks}")
            if not leading_stocks:
                early_stop_reason = "股票龙头分析师未筛选出明确的龙头股，终止后续分析"
                logger.info(f"AI选股 提前终止: {early_stop_reason}")
                decision = {
                    "action": "观望",
                    "stocks": [],
                    "reasoning": early_stop_reason,
                    "position_suggestion": "空仓观望",
                    "risk_warning": "候选股票中未发现明确龙头，不建议追涨",
                }
                decision_report = f"## 决策结论\n\n{early_stop_reason}\n\n股票龙头分析师分析候选股票后，未确认具备龙头属性的标的，建议观望。"
            else:
                recommended_stocks_str = self._format_stocks_for_prompt(leading_stocks)

        # ====== Step 5: 风险分析师 ======
        if not early_stop_reason:
            leading_stock_codes = [s.get("code", "") for s in leading_stocks if s.get("code")]
            await _report(82, "正在获取风险数据...")
            risk_indicators = await asyncio.to_thread(
                compute_risk_indicators, api_cache, leading_stock_codes
            )

            await _report(86, "风险分析师正在评估...")
            risk_report = await asyncio.to_thread(
                self._run_analyst, quick_llm, "风险分析师", RISK_ANALYST_PROMPT,
                risk_indicators,
                extra_params={"recommended_stocks": recommended_stocks_str}
            )
            analyst_results.append({
                "name": "风险分析师",
                "conclusion": self._extract_conclusion(risk_report, "风险分析师"),
                "tag_type": self._get_risk_conclusion_tag_type(risk_report),
                "content": risk_report,
            })

            risk_level = self._extract_risk_level(risk_report)
            logger.info(f"AI选股 风险等级: {risk_level}")
            if risk_level == "高":
                early_stop_reason = "风险分析师评估整体风险较高，终止后续分析"
                logger.info(f"AI选股 提前终止: {early_stop_reason}")
                decision = {
                    "action": "规避",
                    "stocks": [],
                    "reasoning": early_stop_reason,
                    "position_suggestion": "空仓规避",
                    "risk_warning": "推荐标的整体风险较高，建议规避",
                }
                decision_report = f"## 决策结论\n\n{early_stop_reason}\n\n风险分析师评估推荐标的整体风险较高，建议规避，等待风险释放后再考虑入场。"
            else:
                safe_stocks = self._extract_safe_stocks(risk_report)
                safe_stocks_info = self._format_stocks_for_prompt(safe_stocks)

        # ====== Step 6: 决策分析师（使用深度模型） ======
        if not early_stop_reason:
            await _report(93, "决策分析师正在综合研判...")
            decision_report = await asyncio.to_thread(
                self._run_decision_analyst, deep_llm,
                market_report, sector_report, force_report, leader_report, risk_report,
                safe_stocks_info=safe_stocks_info
            )
            decision = self._parse_decision(decision_report)

        # 补充提前终止的步骤为"跳过"状态
        all_analyst_names = ["大盘分析师", "主线板块分析师", "市场合力分析师", "股票龙头分析师", "风险分析师"]
        completed_names = {r["name"] for r in analyst_results}
        for name in all_analyst_names:
            if name not in completed_names:
                if early_stop_reason:
                    analyst_results.append({
                        "name": name,
                        "conclusion": "已跳过",
                        "tag_type": "info",
                        "content": f"由于{early_stop_reason}，本步骤已跳过。",
                    })
                else:
                    analyst_results.append({
                        "name": name,
                        "conclusion": "未执行",
                        "tag_type": "info",
                        "content": "本步骤未执行。",
                    })

        return {
            "analyst_results": analyst_results,
            "decision": decision,
            "decision_report": decision_report,
            "early_stop": bool(early_stop_reason),
            "early_stop_reason": early_stop_reason,
        }

    async def execute_task(self, task_id: str, user_id: str = None):
        """执行AI选股任务（后台运行）

        Agent间存在依赖关系和条件终止：
        1. 大盘分析师 -> 偏空则终止
        2. 主线板块分析师 -> 无主线板块则终止，有则传板块给合力分析师
        3. 市场合力分析师 -> 根据主线板块分析，筛选2-3支股票，无结果则终止
        4. 股票龙头分析师 -> 从候选股中选出1-2支龙头股，传给风险分析师
        5. 风险分析师 -> 风险高则终止，无风险则传安全标的给决策分析师
        6. 决策分析师 -> 给出最终选股决策
        """
        api_cache = ApiCache()
        try:
            await self._update_status(task_id, "running", 5, "正在初始化AI选股分析...")

            start_time = time.time()

            await self._update_status(task_id, "running", 8, "正在初始化AI模型...")
            config = await asyncio.to_thread(self._build_llm_config)
            quick_llm, deep_llm = await asyncio.to_thread(self._create_llm_instances, config)

            await self._update_status(task_id, "running", 10, "正在执行AI选股分析...")

            async def _on_progress(progress: int, step: str):
                await self._update_status(task_id, "running", progress, step)

            # 调用核心分析逻辑
            analysis_result = await self.run_analysis(
                quick_llm, deep_llm, api_cache, on_progress=_on_progress
            )

            # ====== 保存完整结果 ======
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
                "completed_at": _now_cn().isoformat(),
            }

            try:
                db = get_mongo_db()
                serializable_result = make_serializable(result)
                await db.ai_selector_tasks.update_one(
                    {"task_id": task_id},
                    {"$set": {
                        "status": "completed",
                        "progress": 100,
                        "current_step": "分析完成",
                        "result": serializable_result,
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
        finally:
            api_cache.clear()

    def _invoke_llm(self, llm, messages, analyst_name: str = "") -> Any:
        """LLM 调用，对网络/超时类错误指数退避重试（最多3次）。

        Why: 早期版本 retry_if_exception_type(Exception) 会把鉴权/参数错误也重试，
        浪费 token 配额，因此只对临时性异常重试。
        """
        retryable_substrs = (
            "timeout", "timed out", "connection", "rate limit", "429",
            "502", "503", "504", "remote disconnected", "read error",
        )

        def _is_retryable(exc: BaseException) -> bool:
            msg = str(exc).lower()
            return any(s in msg for s in retryable_substrs)

        from tenacity import retry_if_exception  # 已在顶部导入，此处保留以便测试时可注入

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        )
        def _do_invoke():
            return llm.invoke(messages)

        return _do_invoke()

    def _run_analyst(self, llm, analyst_name: str, prompt_template: str,
                     indicators_data: Dict, extra_params: Dict[str, str] = None) -> str:
        """运行单个分析师Agent

        Args:
            extra_params: 额外的模板参数，如 sector_themes、candidate_stocks、recommended_stocks
        """
        logger.info(f"AI选股 [{analyst_name}] 开始分析...")

        indicators_str = compress_json_for_llm(indicators_data)

        format_params = {"indicators_data": indicators_str}
        if extra_params:
            format_params.update(extra_params)

        prompt = prompt_template.format(**format_params)

        logger.debug(
            f"[LLM提示词] [{analyst_name}] 提示词长度={len(prompt)}\n"
            f"{'='*60}\n{prompt}\n{'='*60}"
        )

        try:
            response = self._invoke_llm(llm, [SystemMessage(content=prompt)], analyst_name)
            report = response.content
            logger.info(f"AI选股 [{analyst_name}] 分析完成，报告长度: {len(report)}")
            logger.debug(
                f"[LLM输出] [{analyst_name}] 输出长度={len(report)}\n"
                f"{'='*60}\n{report}\n{'='*60}"
            )
            return report
        except Exception as e:
            logger.error(f"AI选股 [{analyst_name}] LLM调用失败: {e}")
            return f"{analyst_name}分析失败: {str(e)}"

    def _run_decision_analyst(self, llm, market_report, sector_report,
                              force_report, leader_report, risk_report,
                              safe_stocks_info: str = "") -> str:
        """运行决策分析师"""
        logger.info("AI选股 [决策分析师] 开始综合研判...")

        prompt = DECISION_ANALYST_PROMPT.format(
            current_time=datetime.now(_CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            market_report=market_report,
            sector_report=sector_report,
            force_report=force_report,
            leader_report=leader_report,
            risk_report=risk_report,
            safe_stocks_info=safe_stocks_info,
        )

        logger.debug(
            f"[LLM提示词] [决策分析师] 提示词长度={len(prompt)}\n"
            f"{'='*60}\n{prompt}\n{'='*60}"
        )

        try:
            response = self._invoke_llm(llm, [SystemMessage(content=prompt)], "决策分析师")
            report = response.content
            logger.info(f"AI选股 [决策分析师] 综合研判完成，报告长度: {len(report)}")
            logger.debug(
                f"[LLM输出] [决策分析师] 输出长度={len(report)}\n"
                f"{'='*60}\n{report}\n{'='*60}"
            )
            return report
        except Exception as e:
            logger.error(f"AI选股 [决策分析师] LLM调用失败: {e}")
            return f"决策分析师分析失败: {str(e)}"

    # ============================================================
    # 结构化数据提取方法（用于Agent间数据传递）
    # ============================================================

    def _extract_market_sentiment(self, report: str) -> str:
        """从大盘分析师报告中提取市场情绪判断"""
        try:
            data = extract_json_block(report)
            if data and "market_sentiment" in data:
                sentiment = str(data["market_sentiment"])
                if "偏空" in sentiment or "看空" in sentiment:
                    return "偏空"
                elif "偏多" in sentiment or "看多" in sentiment:
                    return "偏多"
                return "中性"
        except Exception:
            pass
        if "偏空" in report or "看空" in report or "弱势" in report:
            return "偏空"
        elif "偏多" in report or "看多" in report or "强势" in report:
            return "偏多"
        return "中性"

    def _extract_sector_themes(self, report: str) -> List[str]:
        """从板块分析师报告中提取主线板块列表"""
        try:
            data = extract_json_block(report)
            if data:
                if data.get("has_main_sector") is False:
                    return []
                if "main_sectors" in data and isinstance(data["main_sectors"], list):
                    return [str(s) for s in data["main_sectors"] if s]
        except Exception:
            pass

        # 限制匹配长度≤20字符，防止把整句话误识别为板块名
        match = re.search(r'主线板块[：:]\s*(.{2,20}?)[\n。，,；;]', report)
        if match:
            sectors = re.split(r'[、/]', match.group(1))
            return [s.strip().strip('「」""''') for s in sectors if s.strip()]
        return []

    def _extract_candidate_stocks(self, report: str) -> List[Dict]:
        """从合力分析师报告中提取候选股票（2-3支）"""
        try:
            data = extract_json_block(report)
            if data and "recommended_stocks" in data:
                stocks = data["recommended_stocks"]
                if isinstance(stocks, list) and len(stocks) > 0:
                    return stocks[:3]
        except Exception:
            pass

        codes = re.findall(r'\b(\d{6})\b', report)
        if codes:
            unique_codes = list(dict.fromkeys(codes))[:3]
            return [{"code": c, "name": f"股票{c}"} for c in unique_codes]
        return []

    def _extract_leading_stocks(self, report: str) -> List[Dict]:
        """从龙头分析师报告中提取龙头股（1-2支）"""
        try:
            data = extract_json_block(report)
            if data and "leading_stocks" in data:
                stocks = data["leading_stocks"]
                if isinstance(stocks, list) and len(stocks) > 0:
                    return stocks[:2]
        except Exception:
            pass

        codes = re.findall(r'\b(\d{6})\b', report)
        if codes:
            unique_codes = list(dict.fromkeys(codes))[:2]
            return [{"code": c, "name": f"股票{c}"} for c in unique_codes]
        return []

    def _extract_risk_level(self, report: str) -> str:
        """从风险分析师报告中提取风险等级"""
        try:
            data = extract_json_block(report)
            if data and "risk_level" in data:
                level = str(data["risk_level"])
                if "高" in level:
                    return "高"
                elif "低" in level:
                    return "低"
                return "中"
        except Exception:
            pass
        if ("高风险" in report or "风险较高" in report or "风险较大" in report
                or ("整体风险评级" in report and "高风险" in report)):
            return "高"
        elif "低风险" in report or "风险较低" in report or "风险可控" in report:
            return "低"
        return "中"

    def _extract_safe_stocks(self, report: str) -> List[Dict]:
        """从风险分析师报告中提取安全标的"""
        try:
            data = extract_json_block(report)
            if data and "safe_stocks" in data:
                stocks = data["safe_stocks"]
                if isinstance(stocks, list) and len(stocks) > 0:
                    return stocks
        except Exception:
            pass
        # 全文 regex 会把 excluded_stocks 里的代码也捞进来，宁可漏选也不错选高风险标的
        logger.warning("_extract_safe_stocks: 无法从风险报告中解析出结构化安全标的，回退返回空列表")
        return []

    def _format_stocks_for_prompt(self, stocks: List[Dict]) -> str:
        """将股票列表格式化为可读文本，用于传入下游Agent提示词"""
        if not stocks:
            return "暂无"
        lines = []
        for s in stocks:
            code = s.get("code", "")
            name = s.get("name", "")
            reason = s.get("reason", "")
            sector = s.get("sector", "")
            # 用 | 分隔字段，避免中文顿号被 LLM 误解为并列事物
            line = f"{code} {name}"
            if sector:
                line += f" | 板块:{sector}"
            if reason:
                line += f" | 理由:{reason}"
            lines.append(line)
        return "\n".join(f"- {line}" for line in lines)

    def _parse_decision(self, decision_report: str) -> Dict[str, Any]:
        """解析决策分析师的结论"""
        try:
            # 取最后一个 JSON 块：避免报告中示例 JSON 在前、真实 JSON 在后时解析错误
            matches = re.findall(r'```json\s*(.*?)\s*```', decision_report, re.DOTALL)
            if matches:
                decision = json.loads(matches[-1])
                return decision
        except Exception as e:
            logger.error(f"解析决策JSON失败: {e}")

        # 回退：从文本中提取信息
        action = "谨慎推荐"
        if "强烈推荐" in decision_report:
            action = "强烈推荐"
        elif "规避" in decision_report:
            action = "规避"
        elif "观望" in decision_report:
            action = "观望"

        codes = re.findall(r'\b(\d{6})\b', decision_report)
        stocks = [{"code": c, "name": f"股票{c}"} for c in list(dict.fromkeys(codes))[:5]]

        return {
            "action": action,
            "stocks": stocks,
            "reasoning": decision_report[:500],
        }

    def _extract_conclusion(self, report: str, analyst_name: str = "") -> str:
        """从分析报告中提取简要结论，优先使用 JSON 结构化数据"""
        try:
            data = extract_json_block(report)
            if data:
                if analyst_name == "大盘分析师":
                    sentiment = str(data.get("market_sentiment", ""))
                    if sentiment:
                        return sentiment
                elif analyst_name == "主线板块分析师":
                    if data.get("has_main_sector") is False:
                        return "无主线板块"
                    sectors = data.get("main_sectors", [])
                    if sectors:
                        return "、".join(str(s) for s in sectors)
                elif analyst_name == "市场合力分析师":
                    direction = data.get("force_direction", "")
                    if direction:
                        return str(direction)
                elif analyst_name == "股票龙头分析师":
                    stocks = data.get("leading_stocks", [])
                    if stocks:
                        names = [f"{s.get('code', '')} {s.get('name', '')}" for s in stocks[:2]]
                        return "、".join(n.strip() for n in names)
                elif analyst_name == "风险分析师":
                    level = data.get("risk_level", "")
                    if level:
                        return f"风险{level}"
        except Exception:
            pass
        # 回退：关键词匹配
        if "偏多" in report or "看多" in report or "强势" in report:
            return "偏多"
        elif "偏空" in report or "看空" in report or "弱势" in report:
            return "偏空"
        elif "正向共振" in report:
            return "正向共振"
        elif "中性" in report or "震荡" in report:
            return "中性"
        return "中性"

    def _get_conclusion_tag_type(self, report: str) -> str:
        """根据结论获取标签类型"""
        if "偏多" in report or "看多" in report or "强势" in report:
            return "success"
        elif "偏空" in report or "看空" in report or "弱势" in report:
            return "danger"
        return "info"

    def _get_risk_conclusion_tag_type(self, report: str) -> str:
        """风险分析师的结论标签"""
        if "高风险" in report or "风险较高" in report or "风险高" in report:
            return "danger"
        elif "低风险" in report or "风险较低" in report or "风险低" in report or "风险可控" in report:
            return "success"
        return "warning"

    async def _update_status(self, task_id: str, status: str, progress: int,
                             current_step: str, error_message: str = None):
        """更新任务状态"""
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

    async def create_schedule(self, user_id: str, cron_expression: str) -> Dict[str, Any]:
        """创建AI选股定时任务"""
        # 验证cron表达式
        try:
            cron = croniter(cron_expression, datetime.now(_CN_TZ))
            # 尝试获取下一次时间，验证表达式有效
            cron.get_next(datetime)
        except Exception as e:
            raise ValueError(f"无效的Cron表达式: {e}")

        try:
            from app.services.scheduler_service import get_scheduler_service
            from apscheduler.triggers.cron import CronTrigger

            scheduler_service = get_scheduler_service()
            scheduler = scheduler_service.scheduler

            job_id = f"ai_selector_schedule_{user_id}"

            # 如果已存在该用户的定时任务，先移除
            existing_job = scheduler.get_job(job_id)
            if existing_job:
                scheduler.remove_job(job_id)

            # 使用 APScheduler 添加 cron 定时任务
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
                self._run_scheduled_task,
                trigger=trigger,
                id=job_id,
                name=f"AI选股定时运行",
                kwargs={"user_id": user_id},
                replace_existing=True,
            )

            # 保存定时配置到 MongoDB
            db = get_mongo_db()
            await db.ai_selector_schedules.update_one(
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

            # 计算下次执行时间
            next_runs = self._get_next_run_times(cron_expression, 1)

            logger.info(f"✅ AI选股定时任务已创建: user={user_id}, cron={cron_expression}")

            return {
                "job_id": job_id,
                "cron_expression": cron_expression,
                "enabled": True,
                "next_run_time": next_runs[0] if next_runs else None,
            }
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"❌ 创建AI选股定时任务失败: {e}")
            raise

    async def get_schedule(self, user_id: str) -> Optional[Dict[str, Any]]:
        """获取用户的AI选股定时任务配置"""
        try:
            db = get_mongo_db()
            schedule = await db.ai_selector_schedules.find_one(
                {"user_id": user_id},
                {"_id": 0}
            )
            if not schedule:
                return None

            # 从 APScheduler 获取下次执行时间
            from app.services.scheduler_service import get_scheduler_service
            scheduler_service = get_scheduler_service()
            job = scheduler_service.scheduler.get_job(schedule.get("job_id", ""))

            result = {
                "cron_expression": schedule.get("cron_expression", ""),
                "enabled": schedule.get("enabled", False),
                "job_id": schedule.get("job_id", ""),
                "next_run_time": job.next_run_time.isoformat() if job and job.next_run_time else None,
            }
            return result
        except Exception as e:
            logger.error(f"❌ 获取AI选股定时任务失败: {e}")
            return None

    async def delete_schedule(self, user_id: str) -> bool:
        """删除用户的AI选股定时任务"""
        try:
            from app.services.scheduler_service import get_scheduler_service

            db = get_mongo_db()
            schedule = await db.ai_selector_schedules.find_one({"user_id": user_id})
            if not schedule:
                return False

            job_id = schedule.get("job_id", "")

            # 从 APScheduler 移除任务
            scheduler_service = get_scheduler_service()
            job = scheduler_service.scheduler.get_job(job_id)
            if job:
                scheduler_service.scheduler.remove_job(job_id)

            # 从 MongoDB 删除记录
            await db.ai_selector_schedules.delete_one({"user_id": user_id})

            logger.info(f"✅ AI选股定时任务已删除: user={user_id}")
            return True
        except Exception as e:
            logger.error(f"❌ 删除AI选股定时任务失败: {e}")
            return False

    async def preview_cron(self, cron_expression: str, count: int = 5) -> Dict[str, Any]:
        """预览Cron表达式的下次执行时间"""
        try:
            next_runs = self._get_next_run_times(cron_expression, count)
            # 生成中文描述
            description = self._describe_cron(cron_expression)
            return {
                "cron_expression": cron_expression,
                "description": description,
                "next_run_times": next_runs,
            }
        except Exception as e:
            raise ValueError(f"无效的Cron表达式: {e}")

    def _get_next_run_times(self, cron_expression: str, count: int = 5) -> List[str]:
        """获取Cron表达式的下次执行时间列表"""
        cron = croniter(cron_expression, datetime.now(_CN_TZ))
        runs = []
        for _ in range(count):
            next_time = cron.get_next(datetime)
            runs.append(next_time.strftime("%Y-%m-%d %H:%M:%S"))
        return runs

    def _describe_cron(self, cron_expression: str) -> str:
        """生成Cron表达式的中文描述"""
        parts = cron_expression.strip().split()
        if len(parts) != 5:
            return cron_expression

        minute, hour, day, month, dow = parts
        desc_parts = []

        # 月份
        if month != "*":
            desc_parts.append(f"{month}月")

        # 日期/星期
        if dow != "*" and day == "*":
            dow_map = {"0": "周日", "1": "周一", "2": "周二", "3": "周三",
                       "4": "周四", "5": "周五", "6": "周六", "7": "周日"}
            # 处理范围如 1-5
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

        # 时间
        if hour != "*" and minute != "*":
            desc_parts.append(f"{hour.zfill(2)}:{minute.zfill(2)}")
        elif hour != "*":
            desc_parts.append(f"{hour}点每分钟")
        elif minute != "*":
            desc_parts.append(f"每小时{minute}分")

        return "".join(desc_parts) if desc_parts else cron_expression

    async def _run_scheduled_task(self, user_id: str):
        """定时任务执行回调"""
        try:
            logger.info(f"🕐 AI选股定时任务触发: user={user_id}")
            result = await self.create_task(user_id)
            task_id = result["task_id"]
            await self.execute_task(task_id, user_id)
            logger.info(f"✅ AI选股定时任务完成: user={user_id}, task={task_id}")
        except Exception as e:
            logger.error(f"❌ AI选股定时任务执行失败: user={user_id}, error={e}", exc_info=True)


# 单例
_ai_selector_service: Optional[AiSelectorService] = None
_ai_selector_service_lock = threading.Lock()

def get_ai_selector_service() -> AiSelectorService:
    global _ai_selector_service
    if _ai_selector_service is None:
        with _ai_selector_service_lock:
            if _ai_selector_service is None:
                _ai_selector_service = AiSelectorService()
    return _ai_selector_service
