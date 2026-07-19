"""
因子预处理模块

对计算得到的因子进行数据清洗和标准化处理。

核心处理流程：
1. 去极值（处理离群点）
2. 中性化（去除行业、市值等系统性暴露）
3. 标准化（Z-score 正态化）
4. 质量检验（缺失值、异常值检测）
"""

import logging
import pandas as pd
from typing import Optional, Dict, Tuple, List
from datetime import datetime
from scipy import stats

logger = logging.getLogger(__name__)


class FactorPreprocessor:
    """
    因子预处理器

    职责：
    - 处理缺失值
    - 去除极端值（outlier removal）
    - 行业市值中性化（neutralization）
    - 标准化和排名（normalization）
    - 因子质量检验

    属性：
        processed_factors: 处理后的因子数据
        preprocessing_stats: 处理统计信息
    """

    def __init__(self):
        """
        初始化因子预处理器
        """

        # 处理后的因子数据
        self.processed_factors = {}

        # 处理统计
        self.preprocessing_stats = {
            'missing_values_removed': 0,
            'outliers_removed': 0,
            'neutralized': False,
            'normalized': False,
        }

        logger.info("✅ FactorPreprocessor 初始化完成")

    def handle_missing_values(self,
                             factors_df: pd.DataFrame,
                             method: str = 'forward_fill',
                             threshold: float = 0.8) -> pd.DataFrame:
        """
        处理缺失值

        缺失值处理策略：
        1. forward_fill: 向前填充（使用前一个交易日的值）
        2. backward_fill: 向后填充
        3. drop: 删除含缺失值的行
        4. mean: 使用平均值填充
        5. median: 使用中位数填充

        Args:
            factors_df: 因子 DataFrame
            method: 处理方法
            threshold: 缺失值比例阈值（超过则删除整列）

        Returns:
            pd.DataFrame: 处理后的因子数据

        示例：
            # 如果某个因子有 90% 的缺失值，就删除这个因子
            # 剩余的缺失值则向前填充
        """
        logger.info(f"🧹 开始处理缺失值（方法：{method}）")

        if factors_df.empty:
            logger.warning("⚠️ 因子数据为空")
            return factors_df

        # 统计缺失值
        missing_counts = factors_df.isnull().sum()
        missing_percentages = missing_counts / len(factors_df)

        # 删除缺失值比例过高的列
        cols_to_drop = missing_percentages[missing_percentages > threshold].index.tolist()
        if cols_to_drop:
            logger.warning(f"⚠️ 删除缺失值 > {threshold*100}% 的因子: {cols_to_drop}")
            factors_df = factors_df.drop(columns=cols_to_drop)

        # 处理剩余缺失值
        if method == 'forward_fill':
            # 按股票分组，向前填充
            # 检查是否有 MultiIndex
            if isinstance(factors_df.index, pd.MultiIndex) and 'stock_code' in factors_df.index.names:
                result = factors_df.groupby(level='stock_code').ffill()
                # 处理最初的 NaN
                result = result.groupby(level='stock_code').bfill()
            else:
                # 如果不是 MultiIndex，直接填充
                result = factors_df.ffill().bfill()
        elif method == 'backward_fill':
            if isinstance(factors_df.index, pd.MultiIndex) and 'stock_code' in factors_df.index.names:
                result = factors_df.groupby(level='stock_code').bfill()
            else:
                result = factors_df.bfill()
        elif method == 'drop':
            result = factors_df.dropna()
        elif method == 'mean':
            result = factors_df.fillna(factors_df.mean())
        elif method == 'median':
            result = factors_df.fillna(factors_df.median())
        else:
            logger.error(f"❌ 未知的处理方法: {method}")
            return factors_df

        # 统计处理结果
        removed_count = factors_df.isnull().sum().sum() - result.isnull().sum().sum()
        self.preprocessing_stats['missing_values_removed'] += removed_count

        logger.info(f"✅ 缺失值处理完成，去除 {removed_count} 个 NaN")

        return result

    def remove_outliers(self,
                       factors_df: pd.DataFrame,
                       method: str = 'mad',
                       threshold: float = 3.0) -> pd.DataFrame:
        """
        去除极值（异常值）

        极值处理方法：
        1. 3-sigma 法：使用标准差（鲁棒性较差）
        2. MAD 法：使用中位数绝对偏差（更稳健，推荐）
        3. Percentile 法：基于百分位数

        Args:
            factors_df: 因子 DataFrame
            method: 处理方法（3sigma / mad / percentile）
            threshold: 阈值倍数（对于 3sigma 和 mad 方法）

        Returns:
            pd.DataFrame: 处理后的因子数据

        示例：
            # MAD 方法：median ± threshold * MAD
            # 将超过这个范围的值替换为 NaN（或边界值）
            # 这样可以保留所有样本，但极值被"截断"
        """
        logger.info(f"✂️ 开始去除极值（方法：{method}）")

        if factors_df.empty:
            logger.warning("⚠️ 因子数据为空")
            return factors_df

        result = factors_df.copy()
        outlier_count = 0

        if method == 'mad':
            # MAD（中位数绝对偏差）法 - 更稳健
            for col in result.columns:
                median = result[col].median()
                # MAD = median(|x - median|)
                mad = (result[col] - median).abs().median()

                if mad > 0:
                    # 确定上下界
                    upper = median + threshold * mad
                    lower = median - threshold * mad

                    # 替换为边界值（winsorization）而不是删除
                    mask = result[col] > upper
                    result.loc[mask, col] = upper
                    outlier_count += mask.sum()

                    mask = result[col] < lower
                    result.loc[mask, col] = lower
                    outlier_count += mask.sum()

        elif method == '3sigma':
            # 3-sigma 法
            for col in result.columns:
                mean = result[col].mean()
                std = result[col].std()

                if std > 0:
                    upper = mean + threshold * std
                    lower = mean - threshold * std

                    mask = result[col] > upper
                    result.loc[mask, col] = upper
                    outlier_count += mask.sum()

                    mask = result[col] < lower
                    result.loc[mask, col] = lower
                    outlier_count += mask.sum()

        elif method == 'percentile':
            # 百分位法: threshold 表示尾部百分比（如 3.0 表示上下各 3%）
            lower_pct = threshold / 100       # 0.03
            upper_pct = 1 - threshold / 100   # 0.97

            for col in result.columns:
                lower_bound = result[col].quantile(lower_pct)
                upper_bound = result[col].quantile(upper_pct)

                mask = result[col] > upper_bound
                result.loc[mask, col] = upper_bound
                outlier_count += mask.sum()

                mask = result[col] < lower_bound
                result.loc[mask, col] = lower_bound
                outlier_count += mask.sum()

        self.preprocessing_stats['outliers_removed'] += outlier_count
        logger.info(f"✅ 极值去除完成，处理 {outlier_count} 个值")

        return result

    def neutralize_factors(self,
                          factors_df: pd.DataFrame,
                          industry_data: Optional[pd.DataFrame] = None,
                          market_cap_data: Optional[pd.DataFrame] = None) -> pd.DataFrame:
        """
        因子中性化

        去除行业、市值等系统性因子的影响，使因子更纯粹。

        中性化逻辑：
        对每个因子，按行业和市值分组：
        1. 计算每组的平均值
        2. 用因子值 - 组平均值 得到中性化后的值
        这样就消除了行业和市值的系统性偏差

        Args:
            factors_df: 原始因子 DataFrame
            industry_data: 行业数据 DataFrame（可选）
            market_cap_data: 市值数据 DataFrame（可选）

        Returns:
            pd.DataFrame: 中性化后的因子数据

        示例：
            原始因子：[10, 15, 12, 18]
            行业：['金融', '金融', '科技', '科技']
            组平均：金融=12.5, 科技=15
            中性化：[10-12.5, 15-12.5, 12-15, 18-15]
                 = [-2.5, 2.5, -3, 3]
        """
        logger.info("⚖️ 开始因子中性化")

        if factors_df.empty:
            logger.warning("⚠️ 因子数据为空")
            return factors_df

        result = factors_df.copy()

        # 如果提供了行业数据，进行行业中性化
        if industry_data is not None and not industry_data.empty:
            logger.info("📊 进行行业中性化")

            try:
                for col in result.columns:
                    # 按行业分组计算均值
                    group_means = result.groupby(industry_data)[col].transform('mean')
                    # 因子值 - 行业均值
                    result[col] = result[col] - group_means

                self.preprocessing_stats['neutralized'] = True
            except Exception as e:
                logger.error(f"❌ 行业中性化失败: {e}")

        # 如果提供了市值数据，进行市值中性化
        if market_cap_data is not None and not market_cap_data.empty:
            logger.info("💰 进行市值中性化")

            try:
                # 按市值分组（如 5 分位）
                market_cap_quantiles = pd.qcut(market_cap_data, q=5, labels=False, duplicates='drop')

                for col in result.columns:
                    # 按市值分位分组计算均值
                    group_means = result.groupby(market_cap_quantiles)[col].transform('mean')
                    # 因子值 - 市值组均值
                    result[col] = result[col] - group_means

                self.preprocessing_stats['neutralized'] = True
            except Exception as e:
                logger.error(f"❌ 市值中性化失败: {e}")

        logger.info("✅ 因子中性化完成")
        return result

    def standardize_factors(self,
                           factors_df: pd.DataFrame,
                           method: str = 'zscore',
                           group_by_date: bool = True) -> pd.DataFrame:
        """
        因子标准化

        使用 Z-score 标准化或其他方法将因子缩放到可比的尺度。

        标准化方法：
        1. Z-score: (x - mean) / std，最常用
        2. Min-Max: (x - min) / (max - min)，缩放到 [0,1]
        3. Rank: 按大小排序后的排名

        Args:
            factors_df: 因子 DataFrame
            method: 标准化方法（zscore / minmax / rank）
            group_by_date: 是否按日期分组标准化
                          True 表示每日分别标准化（推荐）
                          False 表示全局标准化

        Returns:
            pd.DataFrame: 标准化后的因子数据

        示例（Z-score）：
            原始：[10, 12, 14, 16, 18]
            均值：14，标准差：2.83
            标准化：[-1.41, -0.71, 0, 0.71, 1.41]
        """
        logger.info(f"📏 开始因子标准化（方法：{method}）")

        if factors_df.empty:
            logger.warning("⚠️ 因子数据为空")
            return factors_df

        result = factors_df.copy()

        # 如果索引是 MultiIndex（日期, 股票），按日期分组
        if group_by_date and isinstance(result.index, pd.MultiIndex):
            logger.info("📅 按日期分组进行标准化")

            if method == 'zscore':
                # Z-score 标准化：(x - mean) / std
                result = result.groupby(level=0).apply(
                    lambda x: (x - x.mean()) / (x.std() + 1e-8)
                )

            elif method == 'minmax':
                # Min-Max 标准化：(x - min) / (max - min)
                result = result.groupby(level=0).apply(
                    lambda x: (x - x.min()) / (x.max() - x.min() + 1e-8)
                )

            elif method == 'rank':
                # 排名标准化：排名转换为 [-1, 1]
                result = result.groupby(level=0).apply(
                    lambda x: x.rank(pct=True) * 2 - 1
                )

        else:
            # 全局标准化
            if method == 'zscore':
                result = (result - result.mean()) / (result.std() + 1e-8)

            elif method == 'minmax':
                result = (result - result.min()) / (result.max() - result.min() + 1e-8)

            elif method == 'rank':
                for col in result.columns:
                    result[col] = result[col].rank(pct=True) * 2 - 1

        self.preprocessing_stats['normalized'] = True
        logger.info(f"✅ 因子标准化完成")

        return result

    def quality_check(self,
                     factors_df: pd.DataFrame) -> Dict:
        """
        因子质量检验

        检查项：
        1. 缺失值比例
        2. 极值比例
        3. 方差（0 方差因子无用）
        4. 相关性（防止多重共线性）

        Args:
            factors_df: 因子 DataFrame

        Returns:
            Dict: 质量检验报告

        示例返回：
            {
                'factor_quality': {
                    'momentum_20d': {'missing_pct': 0.01, 'variance': 0.05, 'quality': 'good'},
                    'pe_ratio': {'missing_pct': 0.15, 'variance': 0.00, 'quality': 'bad'},
                },
                'correlations': {
                    ('momentum_20d', 'momentum_5d'): 0.92  # 高相关
                }
            }
        """
        logger.info("🔍 开始因子质量检验")

        if factors_df.empty:
            logger.warning("⚠️ 因子数据为空")
            return {}

        quality_report = {
            'factor_quality': {},
            'correlations': {},
            'overall_quality': 'unknown'
        }

        # 检查每个因子
        for col in factors_df.columns:
            data = factors_df[col]

            # 计算指标
            missing_pct = data.isnull().sum() / len(data)
            variance = data.var()
            skewness = stats.skew(data.dropna())
            kurtosis = stats.kurtosis(data.dropna())

            # 判断质量
            if missing_pct > 0.5:
                quality = 'very_bad'  # 缺失过多
            elif variance < 1e-6:
                quality = 'bad'  # 方差太小
            elif missing_pct > 0.2:
                quality = 'poor'  # 缺失较多
            elif abs(skewness) > 3 or abs(kurtosis) > 5:
                quality = 'fair'  # 分布异常
            else:
                quality = 'good'

            quality_report['factor_quality'][col] = {
                'missing_pct': float(missing_pct),
                'variance': float(variance),
                'skewness': float(skewness),
                'kurtosis': float(kurtosis),
                'quality': quality
            }

        # 计算因子间相关性
        corr_matrix = factors_df.corr()
        high_corr_pairs = []

        for i in range(len(corr_matrix.columns)):
            for j in range(i + 1, len(corr_matrix.columns)):
                corr_value = corr_matrix.iloc[i, j]
                if abs(corr_value) > 0.9:  # 高相关阈值
                    pair = (corr_matrix.columns[i], corr_matrix.columns[j])
                    high_corr_pairs.append((pair, float(corr_value)))

        quality_report['correlations'] = dict(high_corr_pairs)

        # 整体质量评分
        good_count = sum(1 for q in quality_report['factor_quality'].values()
                        if q['quality'] == 'good')
        total_count = len(quality_report['factor_quality'])

        if good_count >= total_count * 0.8:
            quality_report['overall_quality'] = 'good'
        elif good_count >= total_count * 0.5:
            quality_report['overall_quality'] = 'fair'
        else:
            quality_report['overall_quality'] = 'poor'

        logger.info(f"✅ 质量检验完成: {quality_report['overall_quality']}")
        logger.info(f"   - 优质因子: {good_count}/{total_count}")
        logger.info(f"   - 高相关因子对: {len(high_corr_pairs)}")

        return quality_report

    def process_pipeline(self,
                        factors_df: pd.DataFrame,
                        config: Optional[Dict] = None) -> Tuple[pd.DataFrame, Dict]:
        """
        完整的因子预处理流程

        按顺序执行：
        1. 缺失值处理
        2. 极值去除
        3. 因子中性化
        4. 因子标准化
        5. 质量检验

        Args:
            factors_df: 原始因子 DataFrame
            config: 配置字典，例如：
                {
                    'handle_missing': {'method': 'forward_fill'},
                    'remove_outliers': {'method': 'mad', 'threshold': 3.0},
                    'neutralize': {'enable': True},
                    'standardize': {'method': 'zscore'},
                }

        Returns:
            Tuple[pd.DataFrame, Dict]: 处理后的因子和处理报告
        """
        logger.info("🔄 开始完整的因子预处理流程")

        if config is None:
            config = {
                'handle_missing': {'method': 'forward_fill'},
                'remove_outliers': {'method': 'mad', 'threshold': 3.0},
                'neutralize': {'enable': False},
                'standardize': {'method': 'zscore'},
            }

        result = factors_df.copy()

        # 1. 缺失值处理
        if 'handle_missing' in config and config['handle_missing'].get('enable', True):
            result = self.handle_missing_values(
                result,
                **{k: v for k, v in config['handle_missing'].items() if k != 'enable'}
            )

        # 2. 极值去除
        if 'remove_outliers' in config and config['remove_outliers'].get('enable', True):
            result = self.remove_outliers(
                result,
                **{k: v for k, v in config['remove_outliers'].items() if k != 'enable'}
            )

        # 3. 因子中性化
        if config.get('neutralize', {}).get('enable', False):
            result = self.neutralize_factors(result)

        # 4. 因子标准化
        if 'standardize' in config and config['standardize'].get('enable', True):
            result = self.standardize_factors(
                result,
                **{k: v for k, v in config['standardize'].items() if k != 'enable'}
            )

        # 5. 质量检验
        quality_report = self.quality_check(result)

        report = {
            'preprocessing_stats': self.preprocessing_stats,
            'quality_report': quality_report,
            'processed_at': datetime.utcnow().isoformat(),
        }

        logger.info("✅ 因子预处理流程完成")

        return result, report
