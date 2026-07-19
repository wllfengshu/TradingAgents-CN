"""
参数网格搜索：自动找到最优策略参数

用途：
- 遍历所有参数组合
- 对每个参数组合运行回测
- 找出最优配置
- 生成性能对比报告

使用示例：
    python grid_search.py --output results/grid_search_2026_06_28.csv
"""

import asyncio
import json
import csv
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Any
from itertools import product
import pandas as pd
import numpy as np
from concurrent.futures import ProcessPoolExecutor, as_completed

logger = logging.getLogger(__name__)


class GridSearchOptimizer:
    """参数网格搜索优化器"""

    def __init__(self, output_file: str = None):
        """
        初始化优化器

        Args:
            output_file: 结果输出文件路径
        """
        self.output_file = output_file or f"grid_search_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        self.results = []

    # ===================== 参数搜索空间定义 =====================

    @staticmethod
    def get_parameter_space() -> Dict[str, List[Any]]:
        """
        定义参数搜索空间

        Returns:
            参数名 → 可选值列表的字典
        """
        return {
            # M0.5 布林过滤
            'bollinger_slope_threshold': [
                0.0,      # 严格：必须slope>0
                0.002,    # 中等
                0.005,    # 宽松
            ],

            # M1 板块选择
            'top_sectors': [
                2,        # 保守：只看top2
                3,        # 标准
                5,        # 激进：看top5
            ],

            # M2 龙头选择
            'top_per_sector': [
                2,        # 保守
                3,        # 标准
                5,        # 激进
            ],

            # M3 合力验证
            'cooperative_force_scheme': [
                'A',      # 简单：主力>0 & 散户>=0
                'B',      # 复杂：占比>X%
            ],
            'cooperative_force_threshold': [
                0.10,     # 宽松
                0.15,     # 标准
                0.20,     # 严格
            ],

            # M4 最终权重（和为1）
            'weight_sector': [
                0.3,
                0.4,      # 标准
                0.5,
            ],
            'weight_dragon': [
                0.3,      # 标准
                0.35,
                0.4,
            ],
            # weight_coop 由其他两个推导：1 - sector - dragon

            # M4 最终选择
            'top_k': [
                3,        # 保守
                5,        # 标准
                10,       # 激进
            ],

            # 无主线日检测
            'rps_std_threshold': [
                0.10,
                0.15,     # 标准
                0.20,
            ],
        }

    # ===================== 参数组合生成 =====================

    @staticmethod
    def generate_parameter_combinations(
        parameter_space: Dict[str, List[Any]],
        max_combinations: int = None
    ) -> List[Dict[str, Any]]:
        """
        生成所有参数组合

        Args:
            parameter_space: 参数搜索空间
            max_combinations: 最大组合数（超过则采样）

        Returns:
            参数字典列表
        """
        # 计算总组合数
        param_names = list(parameter_space.keys())
        param_values = [parameter_space[k] for k in param_names]

        total_combinations = 1
        for values in param_values:
            total_combinations *= len(values)

        logger.info(f"📊 参数空间总规模: {total_combinations} 个组合")

        # 如果超过限制，进行采样
        if max_combinations and total_combinations > max_combinations:
            logger.warning(
                f"⚠️ 组合数过多（{total_combinations}），"
                f"将采样 {max_combinations} 个组合"
            )
            # 随机采样
            import random
            all_combinations = list(product(*param_values))
            sampled = random.sample(all_combinations, max_combinations)
            combinations = [dict(zip(param_names, combo)) for combo in sampled]
        else:
            # 生成全量组合
            combinations = [
                dict(zip(param_names, combo))
                for combo in product(*param_values)
            ]

        logger.info(f"✅ 实际搜索组合数: {len(combinations)}")
        return combinations

    # ===================== 参数验证 =====================

    @staticmethod
    def validate_parameters(params: Dict[str, Any]) -> Tuple[bool, str]:
        """
        验证参数有效性

        Returns:
            (是否有效, 错误信息)
        """
        # 检查权重和
        w_sector = params.get('weight_sector', 0.4)
        w_dragon = params.get('weight_dragon', 0.35)
        w_coop = 1.0 - w_sector - w_dragon

        if w_coop < 0 or w_coop > 1:
            return False, f"权重和超出范围: sector={w_sector}, dragon={w_dragon}, coop={w_coop}"

        if w_sector < 0.1 or w_sector > 0.7:
            return False, f"板块权重超出范围: {w_sector}"

        if w_dragon < 0.1 or w_dragon > 0.7:
            return False, f"龙头权重超出范围: {w_dragon}"

        # 检查阈值
        if params.get('bollinger_slope_threshold', 0) < 0:
            return False, "布林斜率阈值不能为负"

        if params.get('cooperative_force_threshold', 0.15) <= 0:
            return False, "合力阈值必须为正"

        return True, ""

    # ===================== 模拟回测（简化版）=====================

    @staticmethod
    async def mock_backtest(params: Dict[str, Any]) -> Dict[str, float]:
        """
        模拟回测（使用生成的假数据）

        注意：这是演示版本，真实场景需要完整的回测引擎

        Args:
            params: 参数字典

        Returns:
            性能指标字典
        """
        # 生成假数据（模拟回测结果）
        np.random.seed(hash(str(params)) % 2**32)

        # 基础年化收益率（基准10%）
        base_annual_return = 0.10

        # 参数影响
        bonus = 0
        if params['cooperative_force_scheme'] == 'B':
            bonus += 0.02
        bonus += (params['top_sectors'] - 3) * 0.01  # top5比top3好
        bonus -= (params['top_k'] - 5) * 0.02  # 选太多反而差

        annual_return = base_annual_return + bonus + np.random.randn() * 0.02

        # 夏普比率（基准1.0）
        sharpe = 1.0 + params['top_sectors'] * 0.1 - abs(params['weight_dragon'] - 0.35) * 0.5

        # 最大回撤（基准-15%）
        max_drawdown = -0.15 - (5 - params['top_k']) * 0.01

        # 胜率（50-70%）
        win_rate = 0.50 + params['top_sectors'] * 0.05

        return {
            'annual_return': annual_return,
            'sharpe_ratio': sharpe,
            'max_drawdown': max_drawdown,
            'win_rate': win_rate,
            # 综合得分（加权平均）
            'score': (annual_return * 0.4 + max(sharpe, 0) * 0.3 + win_rate * 0.3),
        }

    # ===================== 网格搜索执行 =====================

    async def run_grid_search(
        self,
        parameter_space: Dict[str, List[Any]] = None,
        max_combinations: int = 100,
        max_workers: int = 4
    ) -> pd.DataFrame:
        """
        执行网格搜索

        Args:
            parameter_space: 参数搜索空间
            max_combinations: 最大搜索组合数
            max_workers: 并发进程数

        Returns:
            结果DataFrame
        """
        if parameter_space is None:
            parameter_space = self.get_parameter_space()

        # 生成参数组合
        combinations = self.generate_parameter_combinations(
            parameter_space, max_combinations
        )

        logger.info(f"\n{'='*70}")
        logger.info(f"🚀 开始网格搜索")
        logger.info(f"{'='*70}\n")

        self.results = []
        completed = 0

        # 执行搜索
        for idx, params in enumerate(combinations, 1):
            # 验证参数
            valid, error = self.validate_parameters(params)
            if not valid:
                logger.warning(f"  ❌ 参数无效 (组合{idx}/{len(combinations)}): {error}")
                continue

            # 执行回测
            try:
                backtest_result = await self.mock_backtest(params)

                # 记录结果
                result_row = {
                    **params,
                    'combination_id': idx,
                    **backtest_result,
                }
                self.results.append(result_row)

                completed += 1

                # 每10个组合打印进度
                if completed % 10 == 0 or completed == len(combinations):
                    logger.info(
                        f"✅ 已完成 {completed}/{len(combinations)} | "
                        f"得分: {backtest_result['score']:.4f} | "
                        f"年化: {backtest_result['annual_return']*100:.2f}% | "
                        f"夏普: {backtest_result['sharpe_ratio']:.2f}"
                    )

            except Exception as e:
                logger.error(f"  ❌ 回测失败 (组合{idx}): {e}")
                continue

        # 转换为DataFrame
        results_df = pd.DataFrame(self.results)

        # 按综合得分排序
        if 'score' in results_df.columns:
            results_df = results_df.sort_values('score', ascending=False)

        logger.info(f"\n{'='*70}")
        logger.info(f"✅ 网格搜索完成")
        logger.info(f"{'='*70}\n")

        return results_df

    # ===================== 结果分析 =====================

    @staticmethod
    def analyze_results(results_df: pd.DataFrame) -> Dict[str, Any]:
        """
        分析搜索结果

        Args:
            results_df: 结果DataFrame

        Returns:
            分析报告
        """
        if results_df.empty:
            return {"error": "No results"}

        best_params = results_df.iloc[0]

        analysis = {
            'total_combinations': len(results_df),
            'best_score': best_params['score'],
            'best_annual_return': best_params['annual_return'],
            'best_sharpe_ratio': best_params['sharpe_ratio'],
            'best_max_drawdown': best_params['max_drawdown'],
            'best_params': {k: v for k, v in best_params.items()
                           if k not in ['score', 'annual_return', 'sharpe_ratio',
                                       'max_drawdown', 'win_rate', 'combination_id']},
            'average_annual_return': results_df['annual_return'].mean(),
            'average_sharpe_ratio': results_df['sharpe_ratio'].mean(),
            'std_annual_return': results_df['annual_return'].std(),
        }

        return analysis

    def save_results(self, results_df: pd.DataFrame, output_file: str = None):
        """
        保存结果到CSV

        Args:
            results_df: 结果DataFrame
            output_file: 输出文件路径
        """
        file_path = Path(output_file or self.output_file)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        results_df.to_csv(file_path, index=False)
        logger.info(f"✅ 结果已保存: {file_path}")

        return file_path

    def generate_report(
        self,
        results_df: pd.DataFrame,
        analysis: Dict[str, Any]
    ) -> str:
        """
        生成详细报告

        Args:
            results_df: 结果DataFrame
            analysis: 分析报告

        Returns:
            报告文本
        """
        report = []
        report.append("\n" + "="*70)
        report.append("📊 参数网格搜索 - 完整报告")
        report.append("="*70 + "\n")

        # 总体统计
        report.append("📈 总体统计:")
        report.append(f"  - 搜索组合数: {analysis['total_combinations']}")
        report.append(f"  - 平均年化收益: {analysis['average_annual_return']*100:.2f}%")
        report.append(f"  - 平均夏普比率: {analysis['average_sharpe_ratio']:.2f}")
        report.append(f"  - 收益波动率: {analysis['std_annual_return']*100:.2f}%")

        # 最优参数
        report.append("\n🏆 最优参数组合:")
        report.append(f"  - 综合得分: {analysis['best_score']:.4f}")
        report.append(f"  - 年化收益: {analysis['best_annual_return']*100:.2f}%")
        report.append(f"  - 夏普比率: {analysis['best_sharpe_ratio']:.2f}")
        report.append(f"  - 最大回撤: {analysis['best_max_drawdown']*100:.2f}%")

        report.append("\n  参数配置:")
        for param_name, param_value in analysis['best_params'].items():
            report.append(f"    - {param_name}: {param_value}")

        # top 5
        report.append("\n🎖️ 性能排行 (Top 5):")
        top_5 = results_df.head(5)
        for idx, (_, row) in enumerate(top_5.iterrows(), 1):
            report.append(
                f"  {idx}. 得分={row['score']:.4f}, "
                f"年化={row['annual_return']*100:.2f}%, "
                f"夏普={row['sharpe_ratio']:.2f}"
            )

        report.append("\n" + "="*70 + "\n")

        return "\n".join(report)


# ===================== 命令行入口 =====================

async def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(
        description="截面日频策略参数网格搜索"
    )
    parser.add_argument(
        "--output",
        default=None,
        help="结果输出文件路径"
    )
    parser.add_argument(
        "--max-combinations",
        type=int,
        default=100,
        help="最大搜索组合数（默认100）"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="并发工作进程数（默认4）"
    )

    args = parser.parse_args()

    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # 执行网格搜索
    optimizer = GridSearchOptimizer(output_file=args.output)

    results_df = await optimizer.run_grid_search(
        max_combinations=args.max_combinations,
        max_workers=args.workers
    )

    # 分析结果
    analysis = optimizer.analyze_results(results_df)

    # 保存结果
    output_path = optimizer.save_results(results_df)

    # 生成报告
    report = optimizer.generate_report(results_df, analysis)
    print(report)

    # 保存报告
    report_path = output_path.with_suffix('.txt')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    logger.info(f"✅ 报告已保存: {report_path}")

    return analysis


if __name__ == "__main__":
    asyncio.run(main())
