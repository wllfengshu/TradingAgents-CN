"""
因子管理系统集成示例

演示如何使用因子管理模块的完整工作流：
1. 因子计算
2. 因子预处理
3. 模型训练
4. 回测验证
"""

import logging
import pandas as pd
import numpy as np

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 导入模块
# 优先使用包内相对导入；当文件被直接执行时回退到同目录导入。
try:
    from zstock.factor_management.llm_strategy.factor_calculator import FactorCalculator
    from zstock.factor_management.llm_strategy.factor_preprocessor import FactorPreprocessor
    from zstock.factor_management.llm_strategy.model_trainer import ModelTrainer
    from zstock.factor_management.llm_strategy.backtest_engine import BacktestEngine
    from zstock.common.utils.qlib_utils import initialize_qlib
except ImportError:
    from zstock.factor_management.llm_strategy.factor_calculator import FactorCalculator
    from zstock.factor_management.llm_strategy.factor_preprocessor import FactorPreprocessor
    from zstock.factor_management.llm_strategy.model_trainer import ModelTrainer
    from zstock.factor_management.llm_strategy.backtest_engine import BacktestEngine
    from zstock.common.utils.qlib_utils import initialize_qlib


class FactorManagementPipeline:
    """
    因子管理完整流程

    这是一个集成类，协调各个模块完成从因子计算到回测的整个研究层流程。
    """

    def __init__(self):
        """
        初始化因子管理管道
        """

        # 初始化各个模块
        self.factor_calculator = FactorCalculator()

        self.factor_preprocessor = FactorPreprocessor()

        self.model_trainer = ModelTrainer()

        self.backtest_engine = BacktestEngine()

        logger.info("✅ FactorManagementPipeline 初始化完成")

    def execute_full_pipeline(self,
                             start_date: str,
                             end_date: str,
                             config: dict = None) -> dict:
        """
        执行完整的因子管理流程

        执行步骤：
        1. 🧮 因子计算：计算 Alpha158 + 自定义因子
        2. 🧹 因子预处理：去极值、标准化、中性化
        3. 🤖 模型训练：使用预处理后的因子训练模型
        4. 📈 回测验证：使用模型进行回测

        Args:
            start_date: 开始日期 'YYYY-MM-DD'
            end_date: 结束日期 'YYYY-MM-DD'
            config: 配置字典

        Returns:
            dict: 完整流程的结果
        """
        logger.info("="*60)
        logger.info("🚀 启动因子管理完整流程")
        logger.info("="*60)

        # ⚠️ 关键：初始化 Qlib 环境（必须在回测前完成）
        logger.info("🔧 初始化 Qlib 环境...")
        try:
            initialize_qlib()
            logger.info("✅ Qlib 初始化成功")
        except Exception as e:
            logger.error(f"❌ Qlib 初始化失败: {e}")
            logger.warning("⚠️ 跳过回测，因为 Qlib 不可用")

        default_config = {
            'factor_calculation': {
                'use_alpha158': True,
                'use_custom': True,
            },
            'preprocessing': {
                'handle_missing': {'method': 'forward_fill'},
                'remove_outliers': {'method': 'mad', 'threshold': 3.0},
                'standardize': {'method': 'zscore'},
            },
            'model_training': {
                'model_type': 'lightgbm',
                'train_window': 252,
            },
            'backtest': {
                'initial_cash': 1000000,
                'rebalance_frequency': 5,
                'top_n': 20,
            }
        }

        if config is None:
            config = default_config
        else:
            merged_config = dict(default_config)
            for section, values in config.items():
                if isinstance(values, dict) and isinstance(default_config.get(section), dict):
                    merged_section = dict(default_config[section])
                    merged_section.update(values)
                    merged_config[section] = merged_section
                else:
                    merged_config[section] = values
            config = merged_config

        results = {}

        try:
            # ============================================================
            # 第一步：因子计算
            # ============================================================
            logger.info("\n" + "="*60)
            logger.info("📊 第一步：因子计算")
            logger.info("="*60)

            if config['factor_calculation'].get('use_alpha158', True):
                logger.info("📈 计算 Alpha158 因子...")
                # 注意：这里假设已从数据层获取市场数据
                # 实际使用时需要从 data_source_manager 获取

                alpha158_factors = self.factor_calculator.calculate_alpha158_factors(
                    start_date=start_date,
                    end_date=end_date,
                )
                results['alpha158_factors'] = alpha158_factors

            if config['factor_calculation'].get('use_custom', True):
                logger.info("📈 计算自定义因子...")
                # 获取市场数据用于自定义因子计算
                # 这里使用示例数据
                mock_market_data = pd.DataFrame({
                    'stock_code': ['SH600000', 'SH600001'] * 100,
                    'date': pd.date_range(start_date, periods=200),
                    'close': np.random.randn(200).cumsum() + 10,
                    'volume': np.random.randint(1000000, 10000000, 200),
                })

                custom_factors = self.factor_calculator.calculate_custom_factors(
                    market_data=mock_market_data,
                    config={
                        'momentum_20d': {'period': 20},
                        'volatility_20d': {'period': 20},
                    }
                )
                results['custom_factors'] = custom_factors

            # ============================================================
            # 第二步：因子预处理
            # ============================================================
            logger.info("\n" + "="*60)
            logger.info("🧹 第二步：因子预处理")
            logger.info("="*60)

            # 合并所有因子
            if 'alpha158_factors' in results and 'custom_factors' in results:
                all_factors = pd.concat([
                    results['alpha158_factors'],
                    results['custom_factors']
                ], axis=1)
            elif 'custom_factors' in results:
                all_factors = results['custom_factors']
            else:
                # 使用示例数据
                all_factors = pd.DataFrame(
                    np.random.randn(100, 10),
                    columns=[f'factor_{i}' for i in range(10)]
                )

            logger.info(f"💾 原始因子形状: {all_factors.shape}")

            # 执行预处理流程
            preprocessed_factors, preprocess_report = self.factor_preprocessor.process_pipeline(
                all_factors,
                config=config['preprocessing']
            )

            results['preprocessed_factors'] = preprocessed_factors
            results['preprocess_report'] = preprocess_report

            logger.info(f"✅ 预处理后因子形状: {preprocessed_factors.shape}")

            # ============================================================
            # 第三步：模型训练
            # ============================================================
            logger.info("\n" + "="*60)
            logger.info("🤖 第三步：模型训练")
            logger.info("="*60)

            # 准备训练数据
            market_data_for_training = pd.DataFrame({
                'close': np.random.randn(100).cumsum() + 100,
            })

            X_train, y_train = self.model_trainer.prepare_training_data(
                factors_df=preprocessed_factors.iloc[:80],
                market_data=market_data_for_training.iloc[:80],
                lookforward_period=5
            )

            logger.info(f"💾 训练数据形状: X={X_train.shape}, y={y_train.shape}")

            # 训练模型
            model_type = config['model_training'].get('model_type', 'lightgbm')

            if model_type == 'lightgbm':
                model = self.model_trainer.train_lightgbm(X_train, y_train)
            elif model_type == 'linear':
                model = self.model_trainer.train_linear_model(X_train, y_train)
            else:
                model = self.model_trainer.train_linear_model(X_train, y_train)

            if model is None:
                logger.error("❌ 模型训练失败")
                return results

            # 保存模型
            model_name = f"{model_type}_v1"
            self.model_trainer.save_model_to_mongodb(
                model=model,
                model_name=model_name,
                model_type=model_type,
                metadata={
                    'factors_count': preprocessed_factors.shape[1],
                    'training_period': f"{start_date} ~ {end_date}",
                }
            )

            results['model'] = model
            results['model_name'] = model_name

            logger.info(f"✅ 模型训练并保存完成: {model_name}")

            # ============================================================
            # 第四步：回测验证
            # ============================================================
            logger.info("\n" + "="*60)
            logger.info("📈 第四步：回测验证")
            logger.info("="*60)

            # 生成示例市场数据用于回测
            backtest_dates = pd.date_range(start_date, end_date, freq='D')
            backtest_market_data = pd.DataFrame({
                'trade_date': backtest_dates,
                'stock_code': ['SH600000'] * len(backtest_dates),
                'close': np.random.randn(len(backtest_dates)).cumsum() + 100,
                'is_st': False,
                'is_paused': False,
                'is_limit_up': False,
                'is_limit_down': False,
            })

            # 设置回测宇宙
            backtest_universe = self.backtest_engine.setup_backtest_universe(
                backtest_market_data
            )

            # 运行回测
            try:
                backtest_results = self.backtest_engine.run_backtest(
                    model=model,
                    factors_df=preprocessed_factors,
                    market_data=backtest_universe,
                    start_date=start_date,
                    end_date=end_date,
                    initial_cash=config['backtest']['initial_cash'],
                    config={
                        'rebalance_frequency': config['backtest'].get('rebalance_frequency', 5),
                        'top_n': config['backtest'].get('top_n', 20),
                    }
                )

                results['backtest_results'] = backtest_results

                # 计算性能指标
                if backtest_results.get('nav_curve'):
                    performance_metrics = self.backtest_engine.calculate_performance_metrics(
                        indicator_dict=backtest_results
                    )
                    results['performance_metrics'] = performance_metrics

                    logger.info(f"✅ 回测完成")
            except Exception as e:
                logger.warning(f"⚠️ 回测跳过: {e}")

        except Exception as e:
            logger.error(f"❌ 流程执行失败: {e}")
            import traceback
            traceback.print_exc()

        # ============================================================
        # 总结
        # ============================================================
        logger.info("\n" + "="*60)
        logger.info("✅ 因子管理完整流程执行完毕")
        logger.info("="*60)

        logger.info(f"📋 结果摘要:")
        if 'preprocessed_factors' in results:
            logger.info(f"   - 因子数量: {results['preprocessed_factors'].shape[1]}")
        if 'preprocess_report' in results:
            logger.info(f"   - 因子质量: {results['preprocess_report']['quality_report'].get('overall_quality', 'unknown')}")
        if 'performance_metrics' in results:
            metrics = results['performance_metrics']
            logger.info(f"   - 年化收益: {metrics.get('annualized_return', 0)*100:.2f}%")
            logger.info(f"   - 夏普比率: {metrics.get('sharpe_ratio', 0):.2f}")
            logger.info(f"   - 最大回撤: {metrics.get('max_drawdown', 0)*100:.2f}%")

        return results


# ============================================================
# 快速启动函数
# ============================================================

def quick_start_example():
    """
    快速启动示例

    演示如何快速使用因子管理系统。
    """
    logger.info("🎬 启动快速示例")

    # 创建管道
    pipeline = FactorManagementPipeline()

    # 执行完整流程
    results = pipeline.execute_full_pipeline(
        start_date='2023-01-01',
        end_date='2024-01-01',
        config={
            'factor_calculation': {
                'use_alpha158': False,  # 跳过 Alpha158（需要完整的 Qlib 配置）
                'use_custom': True,
            },
            'model_training': {
                'model_type': 'linear',  # 使用线性模型（快速）
            },
        }
    )

    return results


if __name__ == '__main__':
    # 运行示例
    results = quick_start_example()
    logger.info(f"✅ 示例执行完成，获得 {len(results)} 个结果")
