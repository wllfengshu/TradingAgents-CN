"""
因子计算核心模块

使用 microsoft Qlib 的 Alpha158 计算 158 个基础因子，并支持自定义因子扩展。

核心流程：
1. 从数据层获取行情和财务数据
2. 使用 Qlib DataHandler 处理数据
3. 计算 Alpha158 因子
4. 存储因子值到 MongoDB
"""

import logging
import pandas as pd
from typing import Dict, List, Optional, Tuple
from datetime import datetime

# 导入 Qlib 工具
from zstock.common.utils.qlib_utils import get_qlib_factor_tools

# 获取因子计算工具
_factor_tools = get_qlib_factor_tools()
D = _factor_tools['D']
Alpha158 = _factor_tools['Alpha158']

logger = logging.getLogger(__name__)


class FactorCalculator:
    """
    因子计算器

    职责：
    - 加载 Qlib 数据
    - 计算 Alpha158 因子
    - 支持自定义因子
    - 因子元数据管理

    属性：
        data_handler: Qlib 数据处理器
        factors: 因子字典 {factor_name: factor_values}
        factor_metadata: 因子元数据 {factor_name: {description, category, ...}}
    """

    def __init__(self):
        """
        初始化因子计算器
        """

        # 因子计算状态
        self.factors = {}  # {因子名: 因子值 DataFrame}
        self.factor_metadata = {}  # 因子元数据
        self.data_handler = None  # Qlib DataHandler（延迟初始化）

        logger.info("✅ FactorCalculator 初始化完成")

    def calculate_alpha158_factors(self,
                                  start_date: str,
                                  end_date: str,
                                  stock_codes: Optional[List[str]] = None) -> pd.DataFrame:
        """
        计算 Alpha158 因子集合

        Alpha158 是 Qlib 提供的 158 个经典量化因子。
        这些因子覆盖了：
        - 动量因子（momentum）
        - 反转因子（reversal）
        - 价值因子（value）
        - 质量因子（quality）
        - 流动性因子（liquidity）
        等多个维度

        Args:
            start_date: 开始日期，格式 'YYYY-MM-DD'
            end_date: 结束日期，格式 'YYYY-MM-DD'
            stock_codes: 股票代码列表（如为None，则使用全部股票）

        Returns:
            pd.DataFrame: 因子值矩阵，行为日期-股票，列为因子
                          形状为 (交易日数 × 股票数, 158)
        """
        try:
            # 检查 Qlib 是否已初始化
            if self.data_handler is None:
                logger.error("❌ Qlib 未初始化，请先调用 initialize_qlib()")
                return pd.DataFrame()

            logger.info(f"📊 开始计算 Alpha158 因子: {start_date} ~ {end_date}")

            # 创建 Alpha158 计算器
            alpha158 = Alpha158(
                start_date=start_date,
                end_date=end_date
            )

            # 计算因子
            factors_df = alpha158.fetch()

            # 按股票代码过滤（如指定）
            if stock_codes:
                requested_codes = {str(code).strip() for code in stock_codes if str(code).strip()}
                if requested_codes:
                    before_rows = len(factors_df)

                    if isinstance(factors_df.index, pd.MultiIndex):
                        index_names = list(factors_df.index.names)
                        preferred_levels = ['instrument', 'stock_code', 'symbol', 'code']
                        matched_level = next((name for name in preferred_levels if name in index_names), None)

                        if matched_level is not None:
                            code_values = factors_df.index.get_level_values(matched_level).astype(str)
                        elif factors_df.index.nlevels >= 2:
                            # Qlib 常见 MultiIndex 为 (datetime, instrument)
                            code_values = factors_df.index.get_level_values(1).astype(str)
                        else:
                            code_values = factors_df.index.astype(str)

                        factors_df = factors_df[code_values.isin(requested_codes)]
                    elif 'stock_code' in factors_df.columns:
                        factors_df = factors_df[
                            factors_df['stock_code'].astype(str).isin(requested_codes)
                        ]
                    else:
                        logger.warning("⚠️ 因子结果中未找到可识别的股票代码字段，跳过 stock_codes 过滤")

                    logger.info(
                        f"🔎 按 stock_codes 过滤完成: {before_rows} -> {len(factors_df)} 行"
                    )

            logger.info(f"✅ Alpha158 计算完成，因子矩阵形状: {factors_df.shape}")

            # 存储到类变量
            self.factors['alpha158'] = factors_df

            # 记录因子元数据
            self._register_factor_metadata('alpha158',
                                          category='alpha158',
                                          description='Qlib 提供的 158 个经典因子')

            return factors_df

        except Exception as e:
            logger.error(f"❌ Alpha158 因子计算失败: {e}")
            return pd.DataFrame()

    def calculate_custom_factors(self,
                                market_data: pd.DataFrame,
                                config: Optional[Dict] = None) -> pd.DataFrame:
        """
        计算自定义因子

        支持根据市场数据计算用户自定义的因子。
        常见的自定义因子包括：
        - 技术面：动量、反转、波动率等
        - 基本面：PE、PB、ROE 等
        - 市场微观：成交量、换手率等

        Args:
            market_data: 市场数据 DataFrame，必须包含:
                - date: 交易日期
                - stock_code: 股票代码
                - close: 收盘价
                - volume: 成交量
                - amount: 成交额
                等字段

            config: 自定义因子配置，例如：
                {
                    'momentum_20d': {
                        'period': 20,
                        'calculation': 'price_change'
                    },
                    'volatility_20d': {
                        'period': 20,
                        'calculation': 'std'
                    }
                }

        Returns:
            pd.DataFrame: 自定义因子值矩阵
        """
        try:
            if market_data.empty:
                logger.error("❌ 市场数据为空")
                return pd.DataFrame()

            if config is None:
                # 默认计算几个常见的因子
                config = {
                    'momentum_20d': {'period': 20},
                    'momentum_5d': {'period': 5},
                    'volatility_20d': {'period': 20},
                }

            logger.info(f"📊 开始计算自定义因子: {list(config.keys())}")

            custom_factors = {}

            # 按股票分组计算
            for factor_name, factor_config in config.items():
                factor_values = []

                for stock_code, group in market_data.groupby('stock_code'):
                    if 'momentum' in factor_name:
                        # 动量因子：近期收益率
                        period = factor_config.get('period', 20)
                        # pct_change() 计算百分比变化
                        momentum = group['close'].pct_change(period)
                        factor_values.append(momentum)

                    elif 'volatility' in factor_name:
                        # 波动率因子：过去N日收益率标准差
                        period = factor_config.get('period', 20)
                        volatility = group['close'].pct_change().rolling(period).std()
                        factor_values.append(volatility)

                # 合并所有股票的因子值
                if factor_values:
                    custom_factors[factor_name] = pd.concat(factor_values)

            # 转换为 DataFrame
            if custom_factors:
                factors_df = pd.DataFrame(custom_factors)
                logger.info(f"✅ 自定义因子计算完成，因子数: {len(custom_factors)}")

                self.factors['custom'] = factors_df
                self._register_factor_metadata('custom',
                                              category='custom',
                                              description='用户自定义因子')

                return factors_df
            else:
                logger.warning("⚠️ 未成功计算任何自定义因子")
                return pd.DataFrame()

        except Exception as e:
            logger.error(f"❌ 自定义因子计算失败: {e}")
            return pd.DataFrame()

    def _register_factor_metadata(self,
                                 factor_name: str,
                                 category: str,
                                 description: str = "",
                                 **kwargs) -> None:
        """
        注册因子元数据

        元数据用于：
        1. 因子分类（Alpha158、自定义等）
        2. 因子描述（便于理解）
        3. 统计指标（IC、衰减等，后续更新）
        4. 版本管理（支持因子演进）

        Args:
            factor_name: 因子名称
            category: 因子分类（alpha158 / custom / ...）
            description: 因子描述
            **kwargs: 其他元数据字段
        """
        self.factor_metadata[factor_name] = {
            'name': factor_name,
            'category': category,
            'description': description,
            'created_at': datetime.utcnow(),
            'status': 'active',
            **kwargs
        }

        logger.info(f"📋 因子元数据已注册: {factor_name}")

    def get_factor_metadata(self, factor_name: Optional[str] = None) -> Dict:
        """
        获取因子元数据

        Args:
            factor_name: 因子名称，如为 None 则返回所有元数据

        Returns:
            Dict: 因子元数据
        """
        if factor_name is None:
            return self.factor_metadata

        return self.factor_metadata.get(factor_name, {})

    def get_factors(self, factor_name: Optional[str] = None) -> pd.DataFrame:
        """
        获取因子值

        Args:
            factor_name: 因子名称，如为 None 则返回所有因子

        Returns:
            pd.DataFrame: 因子值 DataFrame
        """
        if factor_name is None:
            # 返回所有因子拼接后的 DataFrame
            if self.factors:
                return pd.concat(self.factors.values(), axis=1)
            return pd.DataFrame()

        return self.factors.get(factor_name, pd.DataFrame())
