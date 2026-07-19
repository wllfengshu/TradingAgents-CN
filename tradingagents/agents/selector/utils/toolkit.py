from langchain_core.tools import tool
from typing import Annotated, List, Dict

from tradingagents.utils.logging_init import get_logger
from tradingagents.agents.selector.utils.llm_logging import log_tool_input, log_tool_output
logger = get_logger("default")


class SelectorToolkit:
    """AI选股工具包（使用@tool装饰器，与现有Toolkit设计一致）"""

    @staticmethod
    @tool
    def get_market_indicators(
        curr_date: Annotated[str, "当前分析日期，格式 YYYY-MM-DD"]
    ) -> str:
        """
        获取A股大盘指标数据，包括主要指数行情、北向资金流向、涨跌家数统计。

        Args:
            curr_date: 当前分析日期（格式：YYYY-MM-DD）

        Returns:
            str: 格式化的大盘指标报告
        """
        logger.info(f"📊 [大盘指标工具] 获取大盘数据: {curr_date}")
        log_tool_input("get_market_indicators", curr_date=curr_date)

        try:
            import tradingagents.dataflows.selector.market_indicators as market_ind

            report = market_ind.compute_market_indicators(curr_date)
            log_tool_output("get_market_indicators", report)
            logger.info(f"✅ [大盘指标工具] 数据获取成功，报告长度: {len(report)}")
            logger.info(f"📈 [大盘指标工具] 数据获取成功，报告内容: \n{report}")
            return report

        except Exception as e:
            error_msg = f"大盘指标获取失败: {str(e)}"
            logger.error(f"❌ [大盘指标工具] {error_msg}")
            return error_msg

    @staticmethod
    @tool
    def get_sector_indicators(
        curr_date: Annotated[str, "当前分析日期，格式 YYYY-MM-DD"]
    ) -> str:
        """
        获取A股板块指标数据，包括涨幅排名、涨停统计、强势股池、封板比、炸板率。

        用于识别主线板块，筛选2-3个候选板块进入辩论环节。

        Args:
            curr_date: 当前分析日期（格式：YYYY-MM-DD）

        Returns:
            str: 格式化的板块指标报告
        """
        logger.info(f"📊 [板块指标工具] 获取板块数据: {curr_date}")
        log_tool_input("get_sector_indicators", curr_date=curr_date)

        try:
            import tradingagents.dataflows.selector.sector_indicators as sector_ind

            report = sector_ind.compute_sector_indicators(curr_date)
            log_tool_output("get_sector_indicators", report)
            logger.info(f"✅ [板块指标工具] 数据获取成功，报告长度: {len(report)}")
            logger.info(f"📈 [板块指标工具] 数据获取成功，报告内容: \n{report}")
            return report

        except Exception as e:
            error_msg = f"板块指标获取失败: {str(e)}"
            logger.error(f"❌ [板块指标工具] {error_msg}")
            return error_msg

    @staticmethod
    @tool
    def get_force_indicators(
        curr_date: Annotated[str, "当前分析日期，格式 YYYY-MM-DD"],
        confirmed_sectors: Annotated[List[str], "确认的主线板块列表，如：[\"贵金属\", \"半导体\"]"]
    ) -> str:
        """
        获取市场合力指标数据，从确认主线板块中筛选合力股票。

        合力股票特征：主力净流入排名靠前、换手率适中、属于主线板块。

        Args:
            curr_date: 当前分析日期（格式：YYYY-MM-DD）
            confirmed_sectors: 确认的主线板块列表

        Returns:
            str: 格式化的合力指标报告
        """
        logger.info(f"📊 [合力指标工具] 获取合力数据: {curr_date}, 主线板块: {confirmed_sectors}")
        log_tool_input("get_force_indicators", curr_date=curr_date, confirmed_sectors=confirmed_sectors)

        try:
            import tradingagents.dataflows.selector.force_indicators as force_ind

            report = force_ind.compute_force_indicators(curr_date, confirmed_sectors)
            log_tool_output("get_force_indicators", report)
            logger.info(f"✅ [合力指标工具] 数据获取成功，报告长度: {len(report)}")
            logger.info(f"📈 [合力指标工具] 数据获取成功，报告内容: \n{report}")
            return report

        except Exception as e:
            error_msg = f"合力指标获取失败: {str(e)}"
            logger.error(f"❌ [合力指标工具] {error_msg}")
            return error_msg

    @staticmethod
    @tool
    def get_leader_indicators(
        curr_date: Annotated[str, "当前分析日期，格式 YYYY-MM-DD"],
        quality_stocks: Annotated[List[Dict], "优质标的股票列表，每项包含code和name字段"]
    ) -> str:
        """
        获取龙头指标数据，从优质标的中筛选龙头股。

        龙头股特征：连板高度最高、板块内排名靠前、成交量放大。

        Args:
            curr_date: 当前分析日期（格式：YYYY-MM-DD）
            quality_stocks: 优质标的股票列表

        Returns:
            str: 格式化的龙头指标报告
        """
        logger.info(f"📊 [龙头指标工具] 获取龙头数据: {curr_date}")
        log_tool_input("get_leader_indicators", curr_date=curr_date, quality_stocks=quality_stocks)

        try:
            import tradingagents.dataflows.selector.leader_indicators as leader_ind

            report = leader_ind.compute_leader_indicators(curr_date, quality_stocks)
            log_tool_output("get_leader_indicators", report)
            logger.info(f"✅ [龙头指标工具] 数据获取成功，报告长度: {len(report)}")
            logger.info(f"📈 [龙头指标工具] 数据获取成功，报告内容: \n{report}")
            return report

        except Exception as e:
            error_msg = f"龙头指标获取失败: {str(e)}"
            logger.error(f"❌ [龙头指标工具] {error_msg}")
            return error_msg

    @staticmethod
    @tool
    def get_risk_indicators(
        curr_date: Annotated[str, "当前分析日期，格式 YYYY-MM-DD"],
        leading_stocks: Annotated[List[Dict], "龙头股列表，每项包含code和name字段"]
    ) -> str:
        """
        获取风险指标数据，对龙头股进行风险评估。

        风险指标：ST状态、新股上市时间、退市风险、财务状况。

        Args:
            curr_date: 当前分析日期（格式：YYYY-MM-DD）
            leading_stocks: 龙头股列表

        Returns:
            str: 格式化的风险指标报告
        """
        logger.info(f"📊 [风险指标工具] 获取风险数据: {curr_date}")
        log_tool_input("get_risk_indicators", curr_date=curr_date, leading_stocks=leading_stocks)

        try:
            import tradingagents.dataflows.selector.risk_indicators as risk_ind

            report = risk_ind.compute_risk_indicators(curr_date, leading_stocks)
            log_tool_output("get_risk_indicators", report)
            logger.info(f"✅ [风险指标工具] 数据获取成功，报告长度: {len(report)}")
            logger.info(f"📈 [风险指标工具] 数据获取成功，报告内容: \n{report}")
            return report

        except Exception as e:
            error_msg = f"风险指标获取失败: {str(e)}"
            logger.error(f"❌ [风险指标工具] {error_msg}")
            return error_msg
