"""
mock测试！

zstock.strategy_management 集成测试（截面因子方案）

包含：
  1. 离线确定性测试：mock factor_pipeline.run_pipeline 的输出，
     端到端验证 StrategyPipeline + Backtester；

直接 python 跑：
    .venv/Scripts/python.exe zstock/strategy_management/test/test_strategy.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import logging
# 强制把根 logger 设到 INFO（被其他模块抢先初始化时也能生效）
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


# ============================================================
# 离线确定性测试
# ============================================================

def _build_fake_signals(trade_date: str) -> list:
    """构造 7 只 fake 候选，模拟 factor_pipeline.run_pipeline 的输出。

    字段与真实管道（ForceFactors）保持一致：
      sector_score  板块分（由板块排名映射，第1名=100, 第2名=70, 第3名=40）
      dragon_score  龙头分（M3 综合）
      coop_score    合力综合（M4+M5）
      coop1_norm    净值比归一
      coop2_norm    主散比归一
      coop3_norm    持续天数归一
      coop4_norm    换手质量归一
      final_score   最终合成分
    """
    # (code, sector, sector_score, dragon_score, coop_score, c1, c2, c3, c4)
    base = [
        ('600000', 'S1', 100, 90.0, 75.0, 90.0, 80.0, 60.0, 50.0),
        ('600519', 'S1', 100, 80.0, 60.0, 70.0, 50.0, 80.0, 40.0),
        ('000001', 'S2',  70, 78.0, 80.0, 85.0, 70.0, 90.0, 60.0),
        ('000002', 'S2',  70, 65.0, 55.0, 60.0, 60.0, 50.0, 30.0),
        ('600036', 'S1', 100, 60.0, 45.0, 40.0, 30.0, 70.0, 20.0),
        ('600519', 'S1', 100, 80.0, 60.0, 70.0, 50.0, 80.0, 40.0),  # 重复，容错验证
        ('601318', 'S3',  40, 50.0, 35.0, 30.0, 20.0, 40.0, 10.0),
    ]
    day_offset = int(trade_date.replace('-', '')) % 7
    W_SECTOR, W_DRAGON, W_COOP = 0.4, 0.35, 0.25
    out = []
    seen = set()
    for code, sector, ss, ds, cs, c1, c2, c3, c4 in base:
        if code in seen:
            continue
        seen.add(code)
        fs = (W_SECTOR * ss + W_DRAGON * ds + W_COOP * cs) / (W_SECTOR + W_DRAGON + W_COOP)
        out.append({
            'code':         code,
            'sector_code':  sector,
            'sector_score': ss,
            'dragon_score': ds,
            'coop_score':   cs,
            'coop1_norm':   c1,
            'coop2_norm':   c2,
            'coop3_norm':   c3,
            'coop4_norm':   c4,
            'final_score':  fs + 0.001 * day_offset,
        })
    return out


def _build_synthetic_ohlcv(codes, start_date: str, end_date: str, seed: int = 7) -> dict:
    """构造合成 OHLCV，每只股票随机游走。"""
    rng = np.random.default_rng(seed)
    start = datetime.strptime(start_date, '%Y-%m-%d')
    end = datetime.strptime(end_date, '%Y-%m-%d')
    dates = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            dates.append(d.strftime('%Y-%m-%d'))
        d += timedelta(days=1)
    out = {}
    for code in codes:
        # 每只票一个独立的漂移率，制造收益差异
        drift = rng.normal(0.0005, 0.0002)
        sigma = 0.015
        rets = rng.normal(drift, sigma, size=len(dates))
        prices = 10.0 * np.cumprod(1 + rets)
        df = pd.DataFrame({
            'trade_date': dates,
            'open': prices * (1 - rng.uniform(0, 0.005, size=len(dates))),
            'high': prices * (1 + rng.uniform(0, 0.01, size=len(dates))),
            'low':  prices * (1 - rng.uniform(0, 0.01, size=len(dates))),
            'close': prices,
            'volume': rng.integers(1e6, 5e7, size=len(dates)),
            'amount': prices * rng.integers(1e6, 5e7, size=len(dates)),
        })
        out[code] = df
    return out


def test_offline():
    assert asyncio.run(_test_offline())


async def _test_offline() -> bool:
    print("\n" + "=" * 70)
    print("📋 测试 1：离线确定性测试（mock factor pipeline）")
    print("=" * 70 + "\n")

    from zstock.strategy_management import (
        StrategyPipeline,
        Backtester,
    )
    from zstock.strategy_management.script.backtester import make_ohlcv_provider_from_dict
    from zstock.factor_management.pipeline import CrossSectionStrategyPipeline

    # ── 上游因子管道输出（fake signals）────────────────────────────
    fake_sigs_preview = _build_fake_signals('2024-02-01')
    SEP = '─' * 80
    print(f"\n{SEP}")
    print(f"  【上游因子管道输出 (fake signals)】  共 {len(fake_sigs_preview)} 只")
    print(f"{SEP}")
    print(f"  {'代码':<12}  {'板块':<6}  {'最终分':>8}  {'板块分':>8}  {'龙头分':>8}  "
          f"{'合力综合':>8}  {'净值归一':>8}  {'主散归一':>8}  {'持续归一':>8}  {'换手归一':>8}")
    print(f"  {'-'*96}")
    for s in fake_sigs_preview:
        def _f(k): return f"{s[k]:>8.2f}" if k in s else f"{'n/a':>8}"
        print(f"  {s['code']:<12}  {s['sector_code']:<6}  {s['final_score']:>8.4f}  "
              f"{_f('sector_score')}  {s['dragon_score']:>8.4f}  "
              f"{_f('coop_score')}  {_f('coop1_norm')}  {_f('coop2_norm')}  "
              f"{_f('coop3_norm')}  {_f('coop4_norm')}")

    # 1. mock factor pipeline 的 run_pipeline_with_real_data，避免触发数据库/xtquant
    class _FakeFactorPipeline(CrossSectionStrategyPipeline):
        async def load_real_data(self, trade_date=None, **kwargs):  # type: ignore[override]
            td = trade_date or datetime.now().strftime('%Y-%m-%d')
            # 返回最小化的 run_pipeline 入参；这里我们再 override run_pipeline，
            # 所以 load_real_data 的内容不被使用。
            return {'trade_date': td, '_offline_mock': True}

        async def run_pipeline(self, **kwargs):  # type: ignore[override]
            td = kwargs.get('trade_date') or datetime.now().strftime('%Y-%m-%d')
            return _build_fake_signals(td)

    # 2. 把假管道注入 StrategyPipeline
    from zstock.strategy_management.signal_generator import SignalGenerator
    fake_factor = _FakeFactorPipeline()
    sig_gen = SignalGenerator(factor_pipeline=fake_factor)
    sp = StrategyPipeline(signal_generator=sig_gen)

    # 3. 跑一次单日 strategy pipeline，校验各阶段非空
    today = '2024-02-01'
    res = await sp.execute_full_pipeline(trade_date=today, total_capital=1e7)
    assert res['status'] == 'success', f"strategy pipeline 失败: {res}"
    sigs = res['results']['signals']
    opt = res['results']['optimization']
    final_holdings = res['results']['final_holdings']
    risk = res['results']['risk_check']

    # ── 单日策略管道结果 ─────────────────────────────────────────
    print(f"\n{SEP}")
    print(f"  【单日策略管道结果】  trade_date={today}")
    print(f"{SEP}")
    print(f"  信号列表 ({len(sigs)} 只):")
    print(f"  {'代码':<12}  {'板块':<6}  {'最终分':>8}  {'龙头分':>8}")
    print(f"  {'-'*40}")
    for _, s in sigs.iterrows():
        print(f"  {s.get('code',''):<12}  {s.get('sector_code',''):<6}  "
              f"{s.get('final_score', 0):>8.4f}  {s.get('dragon_score', 0):>8.4f}")

    print(f"\n  优化持仓 ({len(opt['holdings_df'])} 只, max_weight={opt['max_weight_actual']:.3f}):")
    hdf = opt['holdings_df']
    print(f"  {'代码':<12}  {'权重':>8}  {'得分':>8}")
    print(f"  {'-'*32}")
    for _, row in hdf.iterrows():
        print(f"  {row.get('code',''):<12}  {float(row.get('weight', 0)):>8.4f}  "
              f"{float(row.get('score', row.get('final_score', 0))):>8.4f}")

    print(f"\n  最终持仓 ({len(final_holdings)} 只，风控后):")
    print(f"  {'代码':<12}  {'权重':>8}")
    print(f"  {'-'*24}")
    for _, row in final_holdings.iterrows():
        print(f"  {row.get('code',''):<12}  {float(row.get('weight', 0)):>8.4f}")
    print(f"  权重合计: {float(final_holdings['weight'].sum()):.6f}")

    print(f"\n  风控结果: status={risk['status']}  issues={risk['issues']}")

    print(f"  ✅ 信号数: {len(sigs)}")
    print(f"  ✅ 优化持仓数: {len(opt['holdings_df'])}, max weight={opt['max_weight_actual']:.3f}")
    print(f"  ✅ 风控: {risk['status']}, issues={risk['issues']}")
    print(f"  ✅ 最终持仓数: {len(final_holdings)}")
    assert not final_holdings.empty
    assert abs(final_holdings['weight'].sum() - 1.0) < 1e-6, "权重和应为 1"

    # ── 合成 OHLCV 预览 ──────────────────────────────────────────
    all_codes = [s['code'] for s in _build_fake_signals('2024-01-01')]
    ohlcv = _build_synthetic_ohlcv(all_codes, '2023-12-01', '2024-03-31', seed=42)
    print(f"\n{SEP}")
    print(f"  【合成 OHLCV】  {len(ohlcv)} 只  区间=2023-12-01~2024-03-31  每只 {len(next(iter(ohlcv.values())))} 行")
    print(f"{SEP}")
    print(f"  {'代码':<12}  {'首日close':>10}  {'末日close':>10}  {'区间收益':>9}  "
          f"{'min':>8}  {'max':>8}  {'avg_vol(万)':>12}")
    print(f"  {'-'*72}")
    for code, df in ohlcv.items():
        c0 = float(df['close'].iloc[0])
        c1 = float(df['close'].iloc[-1])
        ret = c1 / c0 - 1.0
        vol_avg = float(df['volume'].mean()) / 1e4
        print(f"  {code:<12}  {c0:>10.3f}  {c1:>10.3f}  {ret:>+9.2%}  "
              f"  {float(df['close'].min()):>7.3f}  {float(df['close'].max()):>7.3f}  "
              f"  {vol_avg:>10.1f}万")

    print(f"\n  ▶ 进入回测 ...")
    bt = Backtester(strategy_pipeline=sp, fee_rate=0.0015, initial_capital=1e7)
    result = await bt.run(
        start_date='2024-01-02',
        end_date='2024-03-29',
        ohlcv_provider=make_ohlcv_provider_from_dict(ohlcv),
        rebalance_freq=5,  # 周频再平衡
        strategy_config={
            'portfolio_optimization': {'min_holdings': 3, 'max_holdings': 6, 'max_weight_per_stock': 0.4, 'weighting': 'score'},
        },
        verbose=True,
    )

    # 画图 + 导出
    output_dir = Path(__file__).resolve().parent / 'output'
    output_dir.mkdir(parents=True, exist_ok=True)
    chart_path = result.plot(
        output_path=str(output_dir / 'backtest_offline.png'),
        title='离线确定性回测 - 净值曲线',
    )
    csv_paths = result.export_csv(str(output_dir))
    print(f"\n  📊 图表已保存: {chart_path}")
    print(f"  📁 数据已导出: {csv_paths}")

    # ── 回测持仓快照 & 交易记录 ─────────────────────────────────
    print(f"\n{SEP}")
    print(f"  【回测持仓快照】  共 {len(result.holdings_log)} 次再平衡")
    print(f"{SEP}")
    for snap in result.holdings_log:
        td_s = snap['trade_date']
        holdings_list = snap.get('holdings', [])
        print(f"  {td_s}  持仓{snap['n_holdings']}只  换手={snap['turnover']:.2%}  成本={snap['cost_pct']:.3%}")
        for h in holdings_list:
            print(f"    {h.get('code',''):<12}  weight={float(h.get('weight', 0)):>7.4f}  "
                  f"score={float(h.get('score', h.get('final_score', 0))):>7.4f}")

    print(f"\n{SEP}")
    print(f"  【回测交易记录】  共 {len(result.trades)} 条")
    print(f"{SEP}")
    print(f"  {'日期':<12}  {'持仓数':>5}  {'换手率':>7}  {'成本%':>7}  "
          f"{'首重股':<12}  {'首重%':>7}  {'风控':>6}")
    print(f"  {'-'*70}")
    for t in result.trades:
        print(f"  {t['trade_date']:<12}  {t['n_holdings']:>5}  {t['turnover']:>7.2%}  "
              f"{t['cost_pct']:>7.3%}  {str(t.get('top_holding','')):<12}  "
              f"{t.get('top_weight', 0):>7.3%}  {t.get('risk_status',''):>6}")

    assert len(result.equity_curve) > 0
    assert result.metrics['rebalance_count'] >= 1
    assert 0.0 <= result.metrics.get('win_rate', 0.0) <= 1.0
    diff_from_1 = float(abs(result.equity_curve.iloc[-1] - 1.0))
    assert diff_from_1 > 1e-6, "回测应产生非零收益"
    print(f"  ✅ 净值变动 = {diff_from_1:.4%}（合成数据，方向不约束）")
    return True


# ============================================================
# 入口
# ============================================================

async def main() -> int:
    ok = True
    try:
        ok = await _test_offline() and ok
    except Exception as e:
        ok = False
        import traceback
        traceback.print_exc()
        print(f"❌ 离线测试失败: {e}")

    print("\n" + "=" * 70)
    print(f"🎉 总体结果: {'PASS' if ok else 'FAIL'}")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
