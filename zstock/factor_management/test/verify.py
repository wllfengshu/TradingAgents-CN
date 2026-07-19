"""
Factor Management 模块验证脚本

验证 Phase 3 开发的所有核心功能是否可用
"""

import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def verify_imports():
    """验证所有模块可以正常导入"""
    logger.info("🔍 验证模块导入...")

    try:
        from zstock.factor_management import (
            FactorCalculator,
            FactorPreprocessor,
            ModelTrainer,
            BacktestEngine,
        )
        logger.info("✅ 核心模块导入成功")

        from zstock.factor_management.llm_strategy.pipeline import FactorManagementPipeline
        logger.info("✅ 集成管道导入成功")

        return True
    except Exception as e:
        logger.error(f"❌ 导入失败: {e}")
        return False


def verify_calculator():
    """验证因子计算器"""
    logger.info("\n🧮 验证因子计算器...")

    try:
        from zstock.factor_management import FactorCalculator
        import pandas as pd
        import numpy as np

        calc = FactorCalculator()
        logger.info("✅ FactorCalculator 初始化成功")

        # 测试自定义因子
        market_data = pd.DataFrame({
            'stock_code': ['SH600000'] * 50,
            'date': pd.date_range('2023-01-01', periods=50),
            'close': 100 + np.random.randn(50).cumsum(),
            'volume': np.random.randint(1000000, 10000000, 50),
        })

        factors = calc.calculate_custom_factors(market_data)
        logger.info(f"✅ 因子计算成功，形状: {factors.shape}")

        return True
    except Exception as e:
        logger.error(f"❌ 因子计算验证失败: {e}")
        return False


def verify_preprocessor():
    """验证因子预处理器"""
    logger.info("\n🧹 验证因子预处理器...")

    try:
        from zstock.factor_management import FactorPreprocessor
        import pandas as pd
        import numpy as np

        prep = FactorPreprocessor()
        logger.info("✅ FactorPreprocessor 初始化成功")

        # 创建测试数据
        factors = pd.DataFrame(
            np.random.randn(100, 5),
            columns=[f'f_{i}' for i in range(5)]
        )

        # 测试完整流程
        processed, report = prep.process_pipeline(factors)
        logger.info(f"✅ 预处理成功，质量: {report['quality_report']['overall_quality']}")

        return True
    except Exception as e:
        logger.error(f"❌ 预处理验证失败: {e}")
        return False


def verify_trainer():
    """验证模型训练器"""
    logger.info("\n🤖 验证模型训练器...")

    try:
        from zstock.factor_management import ModelTrainer
        import numpy as np

        trainer = ModelTrainer()
        logger.info("✅ ModelTrainer 初始化成功")

        # 创建测试数据
        X = np.random.randn(50, 10)
        y = np.random.randn(50) * 0.1

        # 测试线性模型
        model = trainer.train_linear_model(X, y)
        logger.info("✅ 线性模型训练成功")

        # 测试评估
        metrics = trainer.evaluate_model(model, X[:10], y[:10])
        logger.info(f"✅ 模型评估成功，R²={metrics['r2']:.4f}")

        return True
    except Exception as e:
        logger.error(f"❌ 训练器验证失败: {e}")
        return False


def verify_backtest():
    """验证回测引擎"""
    logger.info("\n📈 验证回测引擎...")

    try:
        from zstock.factor_management import BacktestEngine

        engine = BacktestEngine()
        logger.info("✅ BacktestEngine 初始化成功")

        # 测试性能指标计算
        nav_curve = [1.0, 1.01, 1.02, 1.03, 1.04]
        import pandas as pd
        dates = pd.date_range('2023-01-01', periods=5)

        metrics = engine.calculate_performance_metrics(nav_curve, dates)
        logger.info(f"✅ 性能指标计算成功，Sharpe={metrics['sharpe_ratio']:.2f}")

        return True
    except Exception as e:
        logger.error(f"❌ 回测引擎验证失败: {e}")
        return False


def verify_pipeline():
    """验证完整管道"""
    logger.info("\n🔄 验证完整管道...")

    try:
        from zstock.factor_management.llm_strategy.pipeline import FactorManagementPipeline

        pipeline = FactorManagementPipeline()
        logger.info("✅ FactorManagementPipeline 初始化成功")

        logger.info("✅ 管道验证成功")

        return True
    except Exception as e:
        logger.error(f"❌ 管道验证失败: {e}")
        return False


def main():
    """执行所有验证"""
    logger.info("="*60)
    logger.info("🚀 Factor Management 模块验证")
    logger.info("="*60)

    results = {
        "导入验证": verify_imports(),
        "因子计算": verify_calculator(),
        "因子预处理": verify_preprocessor(),
        "模型训练": verify_trainer(),
        "回测引擎": verify_backtest(),
        "完整管道": verify_pipeline(),
    }

    # 总结
    logger.info("\n" + "="*60)
    logger.info("📋 验证总结")
    logger.info("="*60)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, result in results.items():
        status = "✅" if result else "❌"
        logger.info(f"{status} {name}")

    logger.info(f"\n通过率: {passed}/{total} ({passed*100//total}%)")

    if passed == total:
        logger.info("\n🎉 所有验证通过！Phase 3 开发完成！")
        return 0
    else:
        logger.error(f"\n⚠️ 有 {total - passed} 项验证失败")
        return 1


if __name__ == '__main__':
    exit_code = main()
    sys.exit(exit_code)
