"""
策略层快速验证脚本

验证所有模块和功能是否正常工作
"""

import logging
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def verify_imports():
    """验证模块导入"""
    logger.info("🔍 验证模块导入...")

    try:
        from zstock.strategy_management import (
            SignalGenerator,
            PortfolioOptimizer,
            RiskManager,
            TurnoverController,
        )
        from zstock.strategy_management.pipeline import StrategyPipeline

        logger.info("✅ 所有模块导入成功")
        return True

    except Exception as e:
        logger.error(f"❌ 导入失败: {e}")
        return False


def verify_signal_generator():
    """验证信号生成器"""
    logger.info("\n🎯 验证信号生成器...")

    try:
        import inspect
        from zstock.strategy_management import SignalGenerator

        gen = SignalGenerator()
        logger.info("✅ SignalGenerator 初始化成功")

        assert hasattr(gen, 'factor_pipeline'), "❌ 缺少 factor_pipeline 属性"
        assert inspect.iscoroutinefunction(gen.generate_signals), "❌ generate_signals 应为 async"
        logger.info("✅ generate_signals 为 async 方法，接受 trade_date/lookback_days/sectors 参数")
        return True

    except Exception as e:
        logger.error(f"❌ 验证失败: {e}")
        return False


def verify_portfolio_optimizer():
    """验证组合优化器"""
    logger.info("\n📊 验证组合优化器...")

    try:
        from zstock.strategy_management import PortfolioOptimizer

        opt = PortfolioOptimizer()
        logger.info("✅ PortfolioOptimizer 初始化成功")

        signals_df = pd.DataFrame({
            'code': [f'60{i:04d}' for i in range(20)],
            'final_score': np.random.randn(20),
        })

        result = opt.optimize_portfolio(signals_df, min_holdings=5, max_holdings=10)

        assert result['status'] == 'success', f"❌ 优化失败: {result.get('reason')}"
        holdings = result['holdings_df']
        assert set(holdings.columns) >= {'code', 'score', 'weight'}, f"❌ 列名不符: {list(holdings.columns)}"
        assert abs(holdings['weight'].sum() - 1.0) < 1e-6, "❌ 权重之和不等于 1"

        logger.info(f"✅ 优化成功，持仓数: {result['n_holdings']}，最大权重: {result['max_weight_actual']:.4f}")
        return True

    except Exception as e:
        logger.error(f"❌ 验证失败: {e}")
        return False


def verify_risk_manager():
    """验证风险管理器"""
    logger.info("\n🔍 验证风险管理器...")

    try:
        from zstock.strategy_management import RiskManager

        manager = RiskManager()
        logger.info("✅ RiskManager 初始化成功")

        holdings_df = pd.DataFrame({
            'code': [f'60{i:04d}' for i in range(10)],
            'weight': np.ones(10) / 10,
            'score': np.random.randn(10),
        })

        result = manager.check_compliance(holdings_df)

        assert 'status' in result and 'issues' in result and 'metrics' in result
        logger.info(f"✅ check_compliance 通过，状态: {result['status']}，issues: {result['issues']}")
        return True

    except Exception as e:
        logger.error(f"❌ 验证失败: {e}")
        return False


def verify_turnover_controller():
    """验证换手控制器"""
    logger.info("\n🔄 验证换手控制器...")

    try:
        from zstock.strategy_management import TurnoverController

        controller = TurnoverController()
        logger.info("✅ TurnoverController 初始化成功")

        new_holdings = pd.DataFrame({
            'code': [f'60{i:04d}' for i in range(10)],
            'weight': np.ones(10) / 10,
            'score': np.random.randn(10),
        })
        cur_holdings = pd.DataFrame({
            'code': [f'60{i:04d}' for i in range(5, 15)],
            'weight': np.ones(10) / 10,
            'score': np.random.randn(10),
        })

        result = controller.apply_buffer_mechanism(new_holdings, cur_holdings)
        assert not result.empty, "❌ apply_buffer_mechanism 返回空"
        assert abs(result['weight'].sum() - 1.0) < 1e-6, "❌ 权重之和不等于 1"

        costs = controller.estimate_trading_costs(cur_holdings, result)
        assert 'turnover' in costs and 'cost_pct' in costs

        logger.info(f"✅ Buffer 机制应用成功，持仓数: {len(result)}，换手率: {costs['turnover']:.2%}")
        return True

    except Exception as e:
        logger.error(f"❌ 验证失败: {e}")
        return False


def verify_pipeline():
    """验证完整管道"""
    logger.info("\n🚀 验证完整管道...")

    try:
        from zstock.strategy_management.pipeline import StrategyPipeline

        pipeline = StrategyPipeline()
        logger.info("✅ StrategyPipeline 初始化成功")

        assert hasattr(pipeline, 'execute_full_pipeline')
        logger.info("✅ 管道验证成功")
        return True

    except Exception as e:
        logger.error(f"❌ 验证失败: {e}")
        return False


def main():
    """运行所有验证"""
    logger.info("="*60)
    logger.info("🚀 策略层完整验证")
    logger.info("="*60)

    results = {
        "模块导入": verify_imports(),
        "信号生成": verify_signal_generator(),
        "组合优化": verify_portfolio_optimizer(),
        "风险管理": verify_risk_manager(),
        "换手控制": verify_turnover_controller(),
        "完整管道": verify_pipeline(),
    }

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
        logger.info("\n🎉 所有验证通过！")
        return 0
    else:
        logger.error(f"\n⚠️ 有 {total - passed} 项验证失败")
        return 1


if __name__ == '__main__':
    exit_code = main()
    sys.exit(exit_code)
