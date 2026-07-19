"""
# 因子管理模块单元测试---针对`llm_strategy`策略

测试范围：
1. 因子计算 (FactorCalculator)
2. 因子预处理 (FactorPreprocessor)
3. 模型训练 (ModelTrainer)
4. 回测引擎 (BacktestEngine)
"""
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path
import unittest
import numpy as np
import pandas as pd
import logging

# 配置日志
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 导入模块
from zstock.factor_management.llm_strategy.factor_calculator import FactorCalculator
from zstock.factor_management.llm_strategy.factor_preprocessor import FactorPreprocessor
from zstock.factor_management.llm_strategy.model_trainer import ModelTrainer
from zstock.factor_management.llm_strategy.backtest_engine import BacktestEngine


class TestFactorCalculator(unittest.TestCase):
    """测试因子计算模块"""

    def setUp(self):
        """测试前准备"""
        self.calculator = FactorCalculator()

        # 创建示例数据
        self.market_data = pd.DataFrame({
            'stock_code': ['SH600000', 'SH600001'] * 50,
            'date': pd.date_range('2023-01-01', periods=100),
            'close': np.concatenate([
                np.random.randn(50).cumsum() + 10,
                np.random.randn(50).cumsum() + 15,
            ]),
            'volume': np.random.randint(1000000, 10000000, 100),
            'amount': np.random.randint(10000000, 100000000, 100),
        })

    def test_initialization(self):
        """测试初始化"""
        self.assertIsNotNone(self.calculator)
        self.assertEqual(len(self.calculator.factors), 0)
        logger.info("✅ 初始化测试通过")

    def test_calculate_custom_factors(self):
        """测试自定义因子计算"""
        factors = self.calculator.calculate_custom_factors(
            market_data=self.market_data,
            config={
                'momentum_20d': {'period': 20},
                'volatility_20d': {'period': 20},
            }
        )

        self.assertFalse(factors.empty)
        self.assertIn('momentum_20d', self.calculator.factors)
        logger.info("✅ 自定义因子计算测试通过")

    def test_factor_metadata(self):
        """测试因子元数据"""
        self.calculator.calculate_custom_factors(self.market_data)
        metadata = self.calculator.get_factor_metadata('custom')

        self.assertIsNotNone(metadata)
        self.assertEqual(metadata['category'], 'custom')
        logger.info("✅ 因子元数据测试通过")


class TestFactorPreprocessor(unittest.TestCase):
    """测试因子预处理模块"""

    def setUp(self):
        """测试前准备"""
        self.preprocessor = FactorPreprocessor()

        # 创建示例因子数据（包含缺失值和极值）
        np.random.seed(42)
        data = np.random.randn(100, 10)
        data[5, 2] = np.nan  # 添加缺失值
        data[10, 3] = 100  # 添加极值
        data[15, 4] = -100

        self.factors_df = pd.DataFrame(
            data,
            columns=[f'factor_{i}' for i in range(10)]
        )

    def test_handle_missing_values(self):
        """测试缺失值处理"""
        result = self.preprocessor.handle_missing_values(
            self.factors_df,
            method='forward_fill'
        )

        # 检查缺失值是否处理
        self.assertEqual(result.isnull().sum().sum(), 0)
        logger.info("✅ 缺失值处理测试通过")

    def test_remove_outliers(self):
        """测试异常值去除"""
        result = self.preprocessor.remove_outliers(
            self.factors_df,
            method='mad',
            threshold=3.0
        )

        # 检查极值是否被处理
        self.assertTrue((result.values < 50).all())
        self.assertTrue((result.values > -50).all())
        logger.info("✅ 异常值去除测试通过")

    def test_standardize_factors(self):
        """测试因子标准化"""
        result = self.preprocessor.standardize_factors(
            self.factors_df,
            method='zscore'
        )

        # 检查标准化结果
        # Z-score 标准化后，均值应接近 0，标准差应接近 1
        for col in result.columns:
            mean = result[col].mean()
            std = result[col].std()
            self.assertAlmostEqual(mean, 0, places=1)
            self.assertAlmostEqual(std, 1, places=1)

        logger.info("✅ 因子标准化测试通过")

    def test_quality_check(self):
        """测试质量检验"""
        report = self.preprocessor.quality_check(self.factors_df)

        self.assertIn('factor_quality', report)
        self.assertIn('correlations', report)
        self.assertIn('overall_quality', report)
        logger.info("✅ 质量检验测试通过")

    def test_process_pipeline(self):
        """测试完整预处理流程"""
        result, report = self.preprocessor.process_pipeline(self.factors_df)

        self.assertFalse(result.empty)
        self.assertIsNotNone(report)
        self.assertIn('preprocessing_stats', report)
        logger.info("✅ 完整预处理流程测试通过")


class TestModelTrainer(unittest.TestCase):
    """测试模型训练模块"""

    def setUp(self):
        """测试前准备"""
        self.trainer = ModelTrainer()

        # 创建示例数据
        np.random.seed(42)
        self.X_train = np.random.randn(100, 10)
        self.y_train = self.X_train @ np.random.randn(10) + np.random.randn(100) * 0.1

    def test_prepare_training_data(self):
        """测试训练数据准备"""
        factors_df = pd.DataFrame(self.X_train, columns=[f'f_{i}' for i in range(10)])
        market_data = pd.DataFrame({
            'close': 100 + np.random.randn(100).cumsum(),
        })

        X, y = self.trainer.prepare_training_data(
            factors_df=factors_df,
            market_data=market_data,
            lookforward_period=5
        )

        self.assertEqual(X.shape[0], y.shape[0])
        logger.info("✅ 训练数据准备测试通过")

    def test_train_linear_model(self):
        """测试线性模型训练"""
        model = self.trainer.train_linear_model(
            X=self.X_train,
            y=self.y_train,
            model_type='ridge'
        )

        self.assertIsNotNone(model)
        logger.info("✅ 线性模型训练测试通过")

    def test_evaluate_model(self):
        """测试模型评估"""
        model = self.trainer.train_linear_model(
            X=self.X_train,
            y=self.y_train,
            model_type='ols'
        )

        metrics = self.trainer.evaluate_model(
            model=model,
            X_test=self.X_train[:20],
            y_test=self.y_train[:20]
        )

        self.assertIn('r2', metrics)
        self.assertIn('rmse', metrics)
        self.assertIn('ic', metrics)
        logger.info("✅ 模型评估测试通过")


class TestBacktestEngine(unittest.TestCase):
    """测试回测引擎"""

    def setUp(self):
        """测试前准备"""
        self.engine = BacktestEngine()

        # 创建示例市场数据
        dates = pd.date_range('2023-01-01', periods=100)
        self.market_data = pd.DataFrame({
            'trade_date': dates,
            'stock_code': np.random.choice(['SH600000', 'SH600001', 'SH600002'], 100),
            'close': 100 + np.random.randn(100).cumsum(),
            'is_st': False,
            'is_paused': False,
            'is_limit_up': False,
            'is_limit_down': False,
            'list_days': 500,
            'avg_amount_20d': 50000000,
        })

    def test_setup_backtest_universe(self):
        """测试回测宇宙设置"""
        universe = self.engine.setup_backtest_universe(
            market_data=self.market_data
        )

        # 应该过滤出符合条件的股票
        self.assertFalse(universe.empty)
        logger.info("✅ 回测宇宙设置测试通过")

    def test_calculate_trading_costs(self):
        """测试交易成本计算"""
        positions = pd.DataFrame({
            'stock_code': ['SH600000', 'SH600001'],
            'weight': [0.5, 0.5],
        })

        costs = self.engine.calculate_trading_costs(
            positions=positions,
            config={
                'commission_rate': 0.0003,
                'stamp_tax_rate': 0.001,
            }
        )

        self.assertIn('total_cost', costs)
        self.assertGreaterEqual(costs['total_cost'], 0)
        logger.info("✅ 交易成本计算测试通过")

    def test_calculate_performance_metrics(self):
        """测试性能指标计算"""
        nav_curve = [1.0, 1.01, 1.02, 1.03, 1.04, 1.05]
        dates = pd.date_range('2023-01-01', periods=6)

        metrics = self.engine.calculate_performance_metrics(
            nav_curve=nav_curve,
            dates=dates
        )

        self.assertIn('total_return', metrics)
        self.assertIn('sharpe_ratio', metrics)
        self.assertIn('max_drawdown', metrics)
        self.assertGreater(metrics['total_return'], 0)
        logger.info("✅ 性能指标计算测试通过")


class TestIntegration(unittest.TestCase):
    """集成测试"""

    def test_complete_workflow(self):
        """测试完整工作流"""
        logger.info("开始集成测试...")

        # 1. 因子计算
        calc = FactorCalculator()
        market_data = pd.DataFrame({
            'stock_code': ['SH600000'] * 100,
            'date': pd.date_range('2023-01-01', periods=100),
            'close': 100 + np.random.randn(100).cumsum(),
            'volume': np.random.randint(1000000, 10000000, 100),
        })

        factors = calc.calculate_custom_factors(market_data)
        self.assertFalse(factors.empty)
        logger.info("✅ 第1步：因子计算完成")

        # 2. 因子预处理
        preprocessor = FactorPreprocessor()
        processed, report = preprocessor.process_pipeline(factors)
        self.assertFalse(processed.empty)
        logger.info("✅ 第2步：因子预处理完成")

        # 3. 模型训练
        trainer = ModelTrainer()
        X = processed.values
        y = np.random.randn(len(X)) * 0.1

        model = trainer.train_linear_model(X, y)
        self.assertIsNotNone(model)
        logger.info("✅ 第3步：模型训练完成")

        # 4. 回测
        engine = BacktestEngine()
        metrics = engine.calculate_performance_metrics(
            nav_curve=[1.0, 1.01, 1.02, 1.03],
            dates=pd.date_range('2023-01-01', periods=4)
        )
        self.assertIn('sharpe_ratio', metrics)
        logger.info("✅ 第4步：回测完成")

        logger.info("✅ 集成测试通过")


def run_tests():
    """运行所有测试"""
    # 创建测试套件
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # 添加测试
    suite.addTests(loader.loadTestsFromTestCase(TestFactorCalculator))
    suite.addTests(loader.loadTestsFromTestCase(TestFactorPreprocessor))
    suite.addTests(loader.loadTestsFromTestCase(TestModelTrainer))
    suite.addTests(loader.loadTestsFromTestCase(TestBacktestEngine))
    suite.addTests(loader.loadTestsFromTestCase(TestIntegration))

    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # 打印总结
    logger.info("\n" + "="*60)
    logger.info("📊 测试总结")
    logger.info("="*60)
    logger.info(f"运行测试数: {result.testsRun}")
    logger.info(f"✅ 成功: {result.testsRun - len(result.failures) - len(result.errors)}")
    logger.info(f"❌ 失败: {len(result.failures)}")
    logger.info(f"❌ 错误: {len(result.errors)}")

    return result


if __name__ == '__main__':
    result = run_tests()
    exit(0 if result.wasSuccessful() else 1)
