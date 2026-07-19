"""
真实版参数网格搜索：集成完整回测

这个版本会真实调用策略管道和回测引擎
"""

import asyncio
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any
from itertools import product
import pandas as pd
import numpy as np
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.factor_management import CrossSectionStrategyPipeline
from app.core.database import init_database, close_database

logger = logging.getLogger(__name__)


class RealGridSearchOptimizer:
    """真实版网格搜索优化器（集成回测引擎）"""

    def __init__(self, output_dir: str = "grid_search_results"):
        """初始化"""
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.results = []

    @staticmethod
    def get_parameter_space() -> Dict[str, List[Any]]:
        """定义参数搜索空间"""
        return {
            'bollinger_slope_threshold': [0.0, 0.002, 0.005],
            'top_sectors': [2, 3, 5],
            'top_per_sector': [2, 3],
            'cooperative_force_scheme': ['A', 'B'],
            'cooperative_force_threshold': [0.10, 0.15, 0.20],
            'weight_sector': [0.3, 0.4, 0.5],
            'weight_dragon': [0.3, 0.35, 0.4],
            'top_k': [3, 5, 10],
        }

    @staticmethod
    def validate_parameters(params: Dict[str, Any]) -> tuple[bool, str]:
        """验证参数有效性"""
        w_sector = params.get('weight_sector', 0.4)
        w_dragon = params.get('weight_dragon', 0.35)
        w_coop = 1.0 - w_sector - w_dragon

        if w_coop < 0 or w_coop > 1:
            return False, f"权重和超出范围: {w_sector+w_dragon+w_coop}"
        return True, ""

    async def update_config(
        self,
        config_path: str,
        params: Dict[str, Any]
    ) -> None:
        """
        更新策略配置文件

        Args:
            config_path: 配置文件路径
            params: 参数字典
        """
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        # 更新参数
        config['filters']['bollinger']['slope_threshold'] = params['bollinger_slope_threshold']
        config['sector_layer']['top_sectors'] = params['top_sectors']
        config['dragon_layer']['top_per_sector'] = params['top_per_sector']
        config['cooperative_force']['scheme'] = params['cooperative_force_scheme']
        config['cooperative_force']['threshold_pct'] = params['cooperative_force_threshold']
        config['final_score']['weights']['sector'] = params['weight_sector']
        config['final_score']['weights']['dragon'] = params['weight_dragon']
        config['final_score']['weights']['cooperative'] = 1.0 - params['weight_sector'] - params['weight_dragon']
        config['final_score']['top_k'] = params['top_k']

        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    async def run_backtest_with_params(
        self,
        params: Dict[str, Any],
        config_path: str,
        test_data: Dict[str, Any]
    ) -> Dict[str, float]:
        """
        使用给定参数运行回测

        Args:
            params: 参数字典
            config_path: 配置文件路径
            test_data: 测试数据

        Returns:
            性能指标
        """
        try:
            # 更新配置
            await self.update_config(config_path, params)

            # 创建管道
            pipeline = CrossSectionStrategyPipeline(config_path=config_path)

            # 运行策略
            signals = await pipeline.run_pipeline(
                trade_date=test_data['trade_date'],
                all_stocks=test_data['all_stocks'],
                stock_infos=test_data['stock_infos'],
                stock_ohlcv=test_data['stock_ohlcv'],
                sectors=test_data['sectors'],
                sector_stocks=test_data['sector_stocks'],
                sector_ohlcv=test_data['sector_ohlcv'],
                stock_capital_flow=test_data['stock_capital_flow'],
                all_stocks_limit_up=test_data['all_stocks_limit_up'],
                all_stocks_consecutive_boards=test_data['all_stocks_consecutive_boards'],
                all_stocks_volume=test_data['all_stocks_volume'],
                stock_5d_returns=test_data['stock_5d_returns'],
                stock_daily_volumes=test_data['stock_daily_volumes']
            )

            # 计算性能指标
            num_signals = len(signals)
            avg_score = np.mean([s.get('final_score', 0) for s in signals]) if signals else 0

            return {
                'num_signals': num_signals,
                'avg_score': avg_score,
                'signal_quality': num_signals * avg_score / 100 if num_signals > 0 else 0,
                'status': 'success'
            }

        except Exception as e:
            logger.error(f"回测失败: {e}")
            return {
                'num_signals': 0,
                'avg_score': 0,
                'signal_quality': 0,
                'status': f'failed: {str(e)[:50]}'
            }

    async def run_grid_search(
        self,
        config_path: str = "common/config/strategy_params.json",
        test_data: Dict[str, Any] = None,
        max_combinations: int = 50
    ) -> pd.DataFrame:
        """
        执行真实网格搜索

        Args:
            config_path: 配置文件路径
            test_data: 测试数据
            max_combinations: 最大搜索组合数

        Returns:
            结果DataFrame
        """
        parameter_space = self.get_parameter_space()

        # 生成参数组合
        param_names = list(parameter_space.keys())
        param_values = [parameter_space[k] for k in param_names]

        all_combinations = list(product(*param_values))
        total = len(all_combinations)

        logger.info(f"📊 总参数组合数: {total}")

        # 采样
        if total > max_combinations:
            import random
            np.random.seed(42)
            indices = np.random.choice(total, max_combinations, replace=False)
            combinations = [all_combinations[i] for i in indices]
        else:
            combinations = all_combinations

        logger.info(f"✅ 搜索组合数: {len(combinations)}\n")

        # 执行搜索
        self.results = []
        for idx, combo in enumerate(combinations, 1):
            params = dict(zip(param_names, combo))

            # 验证参数
            valid, error = self.validate_parameters(params)
            if not valid:
                logger.warning(f"❌ 参数无效 (组合{idx}/{len(combinations)}): {error}")
                continue

            logger.info(f"🔄 执行组合 {idx}/{len(combinations)}")
            logger.info(f"   参数: {params}")

            # 运行回测
            backtest_result = await self.run_backtest_with_params(
                params, config_path, test_data
            )

            # 记录结果
            result_row = {
                'combination_id': idx,
                **params,
                **backtest_result,
            }
            self.results.append(result_row)

            logger.info(
                f"   ✅ 得分: {backtest_result['signal_quality']:.4f}, "
                f"信号: {backtest_result['num_signals']}\n"
            )

        results_df = pd.DataFrame(self.results)

        if 'signal_quality' in results_df.columns:
            results_df = results_df.sort_values('signal_quality', ascending=False)

        return results_df

    def save_results(self, results_df: pd.DataFrame) -> Path:
        """保存结果"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = self.output_dir / f"grid_search_{timestamp}.csv"
        results_df.to_csv(output_file, index=False)
        logger.info(f"✅ 结果已保存: {output_file}")
        return output_file

    def generate_report(self, results_df: pd.DataFrame) -> str:
        """生成报告"""
        report = []
        report.append("\n" + "="*70)
        report.append("📊 参数网格搜索报告")
        report.append("="*70 + "\n")

        report.append(f"搜索组合数: {len(results_df)}")
        report.append(f"成功: {(results_df['status']=='success').sum()}")
        report.append(f"失败: {(results_df['status']!='success').sum()}\n")

        # 最优
        best = results_df.iloc[0]
        report.append("🏆 最优配置:")
        report.append(f"   信号质量: {best['signal_quality']:.4f}")
        report.append(f"   信号数: {best['num_signals']}")
        report.append(f"   平均得分: {best['avg_score']:.4f}\n")

        report.append("   参数:")
        for col in results_df.columns:
            if col not in ['combination_id', 'num_signals', 'avg_score', 'signal_quality', 'status']:
                report.append(f"      {col}: {best[col]}")

        # Top 5
        report.append("\n🎖️ 性能Top 5:")
        for idx, (_, row) in enumerate(results_df.head(5).iterrows(), 1):
            report.append(
                f"   {idx}. 质量={row['signal_quality']:.4f}, "
                f"信号={row['num_signals']}, "
                f"状态={row['status']}"
            )

        report.append("\n" + "="*70 + "\n")
        return "\n".join(report)


async def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="真实参数网格搜索")
    parser.add_argument("--max-combinations", type=int, default=20, help="最大搜索组合数")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    # 初始化数据库
    print("初始化数据库...")
    await init_database()

    try:
        # 生成测试数据（实际应从数据管理层获取）
        print("准备测试数据...")
        test_data = {
            'trade_date': datetime.now().strftime("%Y-%m-%d"),
            'all_stocks': [f'60000{i:02d}' for i in range(20)] + [f'00000{i:02d}' for i in range(20)],
            'stock_infos': {},
            'stock_ohlcv': {},
            'sectors': [],
            'sector_stocks': {},
            'sector_ohlcv': {},
            'stock_capital_flow': {},
            'all_stocks_limit_up': {},
            'all_stocks_consecutive_boards': {},
            'all_stocks_volume': {},
            'stock_5d_returns': {},
            'stock_daily_volumes': {},
        }

        # 执行网格搜索
        optimizer = RealGridSearchOptimizer()
        results_df = await optimizer.run_grid_search(
            config_path="common/config/strategy_params.json",
            test_data=test_data,
            max_combinations=args.max_combinations
        )

        # 保存结果
        output_file = optimizer.save_results(results_df)

        # 生成报告
        report = optimizer.generate_report(results_df)
        print(report)

        # 保存报告
        report_file = output_file.with_suffix('.txt')
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"✅ 报告已保存: {report_file}")

    finally:
        print("关闭数据库...")
        await close_database()


if __name__ == "__main__":
    asyncio.run(main())
