"""
真实数据测试！

zstock.strategy_management 集成测试（截面因子方案）

包含：
真实数据冒烟测试：从 query_service 拉真数据跑一次 strategy pipeline，
     验证连通性（需要 miniQMT + MongoDB 在线）。

直接 python 跑：
    .venv/Scripts/python.exe zstock/strategy_management/test/test_real_strategy.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import logging
#强制把根 logger 设到 INFO（被其他模块抢先初始化时也能生效）
for h in list(logging.getLogger().handlers):
    logging.getLogger().removeHandler(h)
logging.basicConfig(level=logging.INFO, format='%(message)s')
logging.getLogger('zstock.data_management').setLevel(logging.INFO)
logging.getLogger('zstock.factor_management').setLevel(logging.INFO)
logging.getLogger('zstock.strategy_management.pipeline').setLevel(logging.INFO)
logging.getLogger('zstock.strategy_management.signal_generator').setLevel(logging.INFO)
logging.getLogger('zstock.strategy_management.portfolio_optimizer').setLevel(logging.INFO)
logging.getLogger('zstock.strategy_management.risk_manager').setLevel(logging.INFO)
logging.getLogger('zstock.strategy_management.turnover_controller').setLevel(logging.INFO)
logging.getLogger('zstock.strategy_management.backtester').setLevel(logging.INFO)
logging.getLogger('app').setLevel(logging.WARNING)


# ─────────────────── 可调参数 ───────────────────
# 单日冒烟：start_date / end_date 都留 None → 跑今天
# 区间回测：指定 start_date / end_date，如 '2025-01-01' / '2025-06-30'
# START_DATE: str | None = None   # 'YYYY-MM-DD'，含
# END_DATE:   str | None = None   # 'YYYY-MM-DD'，含
START_DATE= '2026-05-01'   # 'YYYY-MM-DD'，含
END_DATE='2026-06-30'   # 'YYYY-MM-DD'，含
LOOKBACK_DAYS = 60
TOTAL_CAPITAL = 1000000
RISK_FREE_RATE = 0.03  # 年化无风险利率（用于计算夏普比率）
# ────────────────────────────────────────────────


def _trading_dates(start: str, end: str) -> list[str]:
    """生成 [start, end] 内的交易日（周一~周五，不含周末；节假日暂不剔除）。"""
    try:
        sd = datetime.strptime(start, '%Y-%m-%d')
        ed = datetime.strptime(end, '%Y-%m-%d')
    except ValueError as e:
        raise ValueError(f"日期格式错误或不存在: start={start}, end={end} — {e}")
    if sd > ed:
        raise ValueError(f"START_DATE({start}) 不能晚于 END_DATE({end})")
    dates = []
    d = sd
    while d <= ed:
        if d.weekday() < 5:          # 0=Mon … 4=Fri
            dates.append(d.strftime('%Y-%m-%d'))
        d += timedelta(days=1)
    return dates


def _calculate_performance_metrics(daily_records: List[Dict[str, Any]], initial_capital: float) -> Dict[str, Any]:
    """计算回测绩效指标"""
    if not daily_records:
        return {}

    # 提取每日净值
    nav_series = [r['portfolio_value'] for r in daily_records]

    # 计算每日收益率
    daily_returns = []
    for i in range(1, len(nav_series)):
        if nav_series[i-1] > 0:
            daily_returns.append((nav_series[i] - nav_series[i-1]) / nav_series[i-1])
        else:
            daily_returns.append(0.0)

    # 累计收益率
    final_value = nav_series[-1] if nav_series else initial_capital
    total_return = (final_value - initial_capital) / initial_capital

    # 年化收益率
    trading_days = len(daily_records)
    years = trading_days / 252  # 假设每年252个交易日
    annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0

    # 年化波动率
    import numpy as np
    if daily_returns:
        volatility = np.std(daily_returns) * np.sqrt(252)
    else:
        volatility = 0

    # 夏普比率
    if volatility > 0:
        sharpe_ratio = (annual_return - RISK_FREE_RATE) / volatility
    else:
        sharpe_ratio = 0

    # 最大回撤
    peak = nav_series[0]
    max_drawdown = 0
    for nav in nav_series:
        if nav > peak:
            peak = nav
        drawdown = (peak - nav) / peak
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    # 胜率
    winning_days = sum(1 for r in daily_returns if r > 0)
    win_rate = winning_days / len(daily_returns) if daily_returns else 0

    # 盈亏比
    wins = [r for r in daily_returns if r > 0]
    losses = [abs(r) for r in daily_returns if r < 0]
    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0
    profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0

    return {
        'total_return': total_return,
        'annual_return': annual_return,
        'volatility': volatility,
        'sharpe_ratio': sharpe_ratio,
        'max_drawdown': max_drawdown,
        'win_rate': win_rate,
        'profit_loss_ratio': profit_loss_ratio,
        'trading_days': trading_days,
        'final_value': final_value,
    }


async def _get_holding_prices(trade_date: str, holdings: List[str]) -> Dict[str, float]:
    """获取持仓股票的收盘价"""
    try:
        from zstock.data_management import get_data_query_service
        qs = get_data_query_service()

        prices = {}
        for code in holdings:
            try:
                # 获取当天的收盘价
                df = await qs.get_ohlcv(code, trade_date, trade_date)
                if df is not None and not df.empty:
                    prices[code] = float(df.iloc[-1]['close'])
            except Exception as e:
                # 如果获取失败，使用上一个已知价格或标记为0
                prices[code] = 0.0

        return prices
    except Exception as e:
        print(f"  [警告] 获取持仓价格失败: {e}")
        return {}


# ============================================================
# 真实数据冒烟测试（依赖 miniQMT + MongoDB + Redis）
# ============================================================

async def _test_real_smoke() -> bool:
    is_range = START_DATE is not None or END_DATE is not None

    if is_range:
        start = START_DATE or datetime.now().strftime('%Y-%m-%d')
        end   = END_DATE or datetime.now().strftime('%Y-%m-%d')
        trade_dates = _trading_dates(start, end)
        label = f"区间回测 {start} → {end}（{len(trade_dates)} 个交易日）"
    else:
        trade_dates = [datetime.now().strftime('%Y-%m-%d')]
        label = f"单日冒烟 trade_date={trade_dates[0]}"

    print("\n" + "=" * 70)
    print(f"[测试] {label}")
    print("=" * 70 + "\n")

    try:
        from app.core.database import init_database, close_database
    except Exception as e:
        print(f"  [跳过] 无法导入数据库初始化模块 ({e})")
        return True

    try:
        await init_database()
    except Exception as e:
        print(f"  [跳过] MongoDB/Redis 不可用 ({e})")
        return True

    try:
        from zstock.strategy_management import StrategyPipeline
        sp = StrategyPipeline()

        success_count = 0
        skip_count    = 0
        fail_count    = 0

        # 跨天持仓状态（传给下一天的 current_positions）
        current_positions = None
        daily_records = []   # [{trade_date, holdings_count, turnover, cost_pct, portfolio_value, daily_return}, ...]

        # 初始化投资组合
        portfolio_value = TOTAL_CAPITAL
        initial_capital = TOTAL_CAPITAL

        for i, td in enumerate(trade_dates, 1):
            print(f"\n── [{i}/{len(trade_dates)}] trade_date={td} ──")

            # 获取昨日持仓详情
            prev_holdings = []
            if current_positions is not None:
                prev_holdings = current_positions['code'].tolist()
                print(f"  昨日持仓 {len(prev_holdings)} 只: {prev_holdings[:5]}{'...' if len(prev_holdings) > 5 else ''}")

            try:
                res = await sp.execute_full_pipeline(
                    trade_date=td,
                    lookback_days=LOOKBACK_DAYS,
                    total_capital=portfolio_value,  # 使用当前组合价值
                    current_positions=current_positions
                )
                status = res.get('status')
                if status == 'success':
                    stats = res['statistics']
                    results = res['results']

                    # 获取今日持仓详情
                    final_holdings = results.get('final_holdings')
                    current_holdings = []
                    if final_holdings is not None:
                        current_holdings = final_holdings['code'].tolist()

                    # 计算调仓情况
                    bought = [c for c in current_holdings if c not in prev_holdings]
                    sold = [c for c in prev_holdings if c not in current_holdings]
                    held = [c for c in current_holdings if c in prev_holdings]

                    turnover  = stats.get('turnover', 0)
                    cost_pct  = stats.get('cost_pct', 0)

                    # 获取持仓价格并计算组合价值
                    if current_holdings:
                        prices = await _get_holding_prices(td, current_holdings)
                        # 计算组合价值（假设等权重分配）
                        if final_holdings is not None and 'weight' in final_holdings.columns:
                            portfolio_value = 0
                            for _, row in final_holdings.iterrows():
                                code = row['code']
                                weight = row['weight']
                                price = prices.get(code, 0)
                                if price > 0:
                                    shares = (portfolio_value * weight) / price
                                    portfolio_value += shares * price
                        else:
                            # 如果没有权重信息，假设等权重
                            portfolio_value = TOTAL_CAPITAL  # 简化处理

                    # 计算今日收益率
                    prev_value = daily_records[-1]['portfolio_value'] if daily_records else initial_capital
                    daily_return = (portfolio_value - prev_value) / prev_value if prev_value > 0 else 0

                    print(f"  ✓ 信号 {stats['signals_count']}, 持仓 {stats['final_holdings']}")
                    print(f"     调仓: 买入{len(bought)}只{bought[:3]}, 卖出{len(sold)}只{sold[:3]}, 持有{len(held)}只")
                    print(f"     换手 {turnover:.1%}, 成本 {cost_pct:.3%}, 风控 {stats['risk_status']}")
                    print(f"     组合价值: ¥{portfolio_value:,.0f}, 日收益: {daily_return:.2%}")

                    daily_records.append({
                        'trade_date': td,
                        'holdings': stats['final_holdings'],
                        'turnover': turnover,
                        'cost_pct': cost_pct,
                        'portfolio_value': portfolio_value,
                        'daily_return': daily_return,
                        'bought': bought,
                        'sold': sold,
                        'held': held,
                    })
                    success_count += 1
                else:
                    print(f"  ⊘ 无有效信号: status={status}（持仓不变）")
                    # 即使没有信号，也要记录今天的组合价值（使用昨天的价值）
                    daily_records.append({
                        'trade_date': td,
                        'holdings': 0,
                        'turnover': 0,
                        'cost_pct': 0,
                        'portfolio_value': portfolio_value,
                        'daily_return': 0,
                        'bought': [],
                        'sold': [],
                        'held': [],
                    })
                    skip_count += 1
            except Exception as e:
                print(f"  ✗ 失败: {e}")
                fail_count += 1

        # 打印完整回测报告
        print(f"\n{'=' * 70}")
        print(f"回测完成: 成功={success_count}, 跳过={skip_count}, 失败={fail_count} / 共{len(trade_dates)}天")
        print(f"{'=' * 70}")

        if daily_records:
            # 计算绩效指标
            metrics = _calculate_performance_metrics(daily_records, initial_capital)

            print(f"\n📊 绩效指标:")
            print(f"  初始资金:     ¥{initial_capital:>12,.0f}")
            print(f"  最终价值:     ¥{metrics['final_value']:>12,.0f}")
            print(f"  累计收益率:   {metrics['total_return']:>12.2%}")
            print(f"  年化收益率:   {metrics['annual_return']:>12.2%}")
            print(f"  年化波动率:   {metrics['volatility']:>12.2%}")
            print(f"  夏普比率:     {metrics['sharpe_ratio']:>12.2f}")
            print(f"  最大回撤:     {metrics['max_drawdown']:>12.2%}")
            print(f"  胜率:         {metrics['win_rate']:>12.2%}")
            print(f"  盈亏比:       {metrics['profit_loss_ratio']:>12.2f}")
            print(f"  交易天数:     {metrics['trading_days']:>12d}")

            # 打印每日调仓明细
            print(f"\n📋 每日调仓明细:")
            print(f"{'日期':<12} {'持仓数':>6} {'买入':>6} {'卖出':>6} {'换手率':>8} {'日收益':>8} {'累计价值':>12}")
            print("-" * 70)
            for record in daily_records:
                print(f"{record['trade_date']:<12} "
                      f"{record['holdings']:>6} "
                      f"{len(record['bought']):>6} "
                      f"{len(record['sold']):>6} "
                      f"{record['turnover']:>8.1%} "
                      f"{record['daily_return']:>8.2%} "
                      f"¥{record['portfolio_value']:>11,.0f}")

            # 打印调仓详情（买入/卖出的股票）
            print(f"\n🔄 调仓详情（前10个交易日）:")
            for record in daily_records[:10]:
                if record['bought'] or record['sold']:
                    print(f"  {record['trade_date']}:")
                    if record['bought']:
                        print(f"    买入: {', '.join(record['bought'][:5])}")
                    if record['sold']:
                        print(f"    卖出: {', '.join(record['sold'][:5])}")

        print(f"{'=' * 70}")
        return fail_count == 0

    except Exception as e:
        print(f"  [失败] 真实数据冒烟失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        try:
            await close_database()
        except Exception:
            pass


# ============================================================
# 入口
# ============================================================

async def main() -> int:
    ok = True

    try:
        ok = await _test_real_smoke() and ok
    except Exception as e:
        # 真实数据测试失败不阻塞整体
        print(f"[警告] 真实数据测试异常（跳过）: {e}")

    print("\n" + "=" * 70)
    print(f"[完成] 总体结果: {'PASS' if ok else 'FAIL'}")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
