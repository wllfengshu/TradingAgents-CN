"""
mock测试！

截面日频策略 M1(市场情绪) → M2(板块黑名单+板块层) → M3(主板+个股黑名单+布林+龙头层) → M4+M5(合力+最终合成) → M6(最终选股) 全流程验证
规模：10 板块 / 50 只股票，覆盖全部 edge case

关键修正（P0）：
  1. M3 候选池 = 板块成分股 ∩ M3.1/M3.2/M3.3 通过股（主板+个股黑名单+布林全部前置）
  2. 个股黑名单（M3.2）前置于布林过滤（M3.3），保证黑名单票不占用 top K 名额

覆盖 case 清单：
  M1  : 市场情绪评估（绿/黄/红等级，红灯终止整个流程）
  M2.1: 房地产(直接匹配)、光伏设备(fnmatch *光伏*)、贵金属矿业(fnmatch *贵金属*)
  M3.1: 主板沪、主板深(000/001/002/003)、创业板300、科创板688、北交所830、ST、*ST、退市
  M3.2: 个股黑名单实际生效（600001.SH 在主板池且在黑名单）
  M3.3: 布林上升票 vs 中轨下行/价在中轨下
  M3  : 板块取交集（成分股 ∩ M3.1/M3.2/M3.3通过股）；4因子区分度
  M4+M5: main_flow<0、净值比<3%、净值比≥3%；换手率冷盘/优质/过热；散户同向/背离
"""
import asyncio
import fnmatch
import sys
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.common.utils.common_utils import is_main_board, is_st
from zstock.factor_management.prefilters import PreFilters
from zstock.factor_management.sector_factors import SectorFactors
from zstock.factor_management.dragon_factors import DragonFactors, _W_F31, _W_F32, _W_F33, _W_F34, _W_F35
from zstock.factor_management.force_factors import ForceFactors
from zstock.factor_management.market_factors import MarketFactors


# ===================== Mock 数据生成 =====================

def _make_ohlcv(days: int = 30, seed_offset: int = 0) -> pd.DataFrame:
    rng = np.random.RandomState(seed_offset)
    dates = pd.date_range('2024-01-01', periods=days)
    close = np.abs(rng.randn(days).cumsum()) + 50
    volume = rng.randint(1_000_000, 10_000_000, days).astype(float)
    return pd.DataFrame({
        'trade_date': [d.strftime('%Y-%m-%d') for d in dates],
        'close': close, 'high': close * 1.01,
        'low': close * 0.99, 'open': close * 1.005, 'volume': volume,
        'amount': close * volume,
    })


def _make_sectors() -> List[Dict]:
    """10 个板块，3 个命中黑名单"""
    return [
        {'sector_code': 'SC001', 'sector_name': '电子'},
        {'sector_code': 'SC002', 'sector_name': '医药'},
        {'sector_code': 'SC003', 'sector_name': '消费'},
        {'sector_code': 'SC004', 'sector_name': '房地产'},          # 黑名单
        {'sector_code': 'SC005', 'sector_name': '银行'},
        {'sector_code': 'SC006', 'sector_name': '光伏设备'},        # 黑名单(*光伏*)
        {'sector_code': 'SC007', 'sector_name': '新能源'},
        {'sector_code': 'SC008', 'sector_name': '军工'},
        {'sector_code': 'SC009', 'sector_name': '贵金属矿业'},      # 黑名单(*贵金属*)
        {'sector_code': 'SC010', 'sector_name': '化工'},
    ]


def _make_sector_stocks() -> Dict[str, List[str]]:
    """
    板块成分股配置（含 ST/创业板等 invalid 票，用于验证 M3 取交集）：
    SC001 电子 故意包含 ST 票 600036.SH 和创业板 300001.SZ → 应在 M3 阶段被剔除
    """
    return {
        'SC001': ['600001.SH', '600002.SH', '600003.SH', '600004.SH', '600005.SH',
                  '600006.SH', '600007.SH', '600036.SH', '300001.SZ'],   # 含黑名单/ST/创业板
        'SC002': [f'6000{i:02d}.SH' for i in range(8, 13)],
        'SC003': [f'6000{i:02d}.SH' for i in range(13, 19)],
        'SC004': [f'6000{i:02d}.SH' for i in range(19, 23)],
        'SC005': [f'6000{i:02d}.SH' for i in range(23, 28)],
        'SC006': [f'6000{i:02d}.SH' for i in range(28, 31)],
        'SC007': [f'6000{i:02d}.SH' for i in range(31, 34)],
        'SC008': ['600034.SH', '600035.SH', '000101.SZ', '001101.SZ'],
        'SC009': ['002101.SZ', '002102.SZ'],
        'SC010': ['003101.SZ', '003102.SZ'],
    }


def _make_all_stocks_pool() -> List[str]:
    """52 只股票，覆盖所有 M3.1 过滤场景（含退市票）"""
    pool = []
    pool += [f'6000{i:02d}.SH' for i in range(1, 36)]
    pool += ['000101.SZ', '001101.SZ', '002101.SZ',
             '002102.SZ', '003101.SZ', '003102.SZ']
    pool += ['600036.SH', '000201.SZ', '600099.SH']                # ST / *ST / 退市
    pool += ['300001.SZ', '300002.SZ', '300003.SZ']
    pool += ['688001.SH', '688002.SH']
    pool += ['830001.BJ', '830002.BJ']
    return pool


def _make_stock_infos(all_stocks: List[str]) -> Dict[str, Dict]:
    infos = {code: {'name': code} for code in all_stocks}
    infos['600036.SH'] = {'name': 'ST格科'}
    infos['000201.SZ'] = {'name': '*ST数据'}
    infos['600099.SH'] = {'name': '退市方科'}   # 退市票
    return infos


def _make_stock_capital_flow(all_stocks: List[str]) -> Dict[str, Dict]:
    """
    覆盖 5 种 M4+M5 + 主散比场景（按 index % 5）：
      0: 主力极小 (~0.5%净值比) → M4门槛失败
      1: 主力为负 → M4直接失败
      2: 主力高 + 散户同向(retail>0) → 跟风型
      3: 主力高 + 散户背离(retail<0) → 主力接盘型（高质量）
      4: 主力高 + 散户=0 → 纯机构行为
    """
    rng = np.random.RandomState(77)
    result = {}
    for i, code in enumerate(all_stocks):
        total = float(rng.uniform(20e7, 50e7))
        case = i % 5
        if case == 0:
            main = float(rng.uniform(0.05e7, 0.4e7))
            retail = float(rng.uniform(0, 3e7))
        elif case == 1:
            main = -float(rng.uniform(1e7, 5e7))
            retail = float(rng.uniform(-2e7, 2e7))
        elif case == 2:
            main = float(rng.uniform(6e7, 12e7))
            retail = float(rng.uniform(3e7, 8e7))                 # 同向
        elif case == 3:
            main = float(rng.uniform(6e7, 12e7))
            retail = -float(rng.uniform(2e7, 5e7))                # 背离（主力接盘）
        else:
            main = float(rng.uniform(6e7, 12e7))
            retail = 0.0                                           # 无散户
        result[code] = {'main_flow': main, 'retail_flow': retail, 'total_volume': total}
    return result


def _make_stock_5d_net_flow(all_stocks: List[str]) -> Dict[str, float]:
    rng = np.random.RandomState(88)
    return {code: float(rng.uniform(-8e7, 15e7)) for code in all_stocks}


def _make_stock_main_flow_days(all_stocks: List[str]) -> Dict[str, int]:
    """近5日主力净流入>0的天数，覆盖 0/2/4/5"""
    persist_map = [0, 2, 4, 5]
    return {code: persist_map[i % 4] for i, code in enumerate(all_stocks)}


def _make_stock_turnover_rate(all_stocks: List[str]) -> Dict[str, float]:
    """换手率覆盖 1.5%(冷盘) / 10%(优质) / 32%(过热)"""
    rate_map = [0.015, 0.10, 0.32]
    return {code: rate_map[i % 3] for i, code in enumerate(all_stocks)}


# ===================== 辅助 =====================

def _stock_type_label(code: str, stock_infos: Dict) -> str:
    num = code.split('.')[0]
    name = stock_infos.get(code, {}).get('name', '')
    name_up = name.upper()
    if name_up.startswith('ST') or name_up.startswith('*ST'):
        return 'ST'
    if name.startswith('退市'):
        return '退市'
    if code.endswith('.BJ'):
        return '北交所'
    if num.startswith('300'):
        return '创业板'
    if num.startswith('688'):
        return '科创板'
    if num.startswith('60'):
        return '主板沪'
    if num[:3] in ('000', '001', '002', '003'):
        return '主板深'
    return '其他'


def _find_blacklist_pattern(sector_name: str, blacklist: set) -> str:
    for pattern in blacklist:
        if fnmatch.fnmatch(sector_name, pattern):
            return pattern
    return ''


def _collect_sector_raw_factors(sectors, sector_stocks, sector_ohlcv, sector_capital_flow,
                                all_stocks_limit_up, all_stocks_consecutive_boards):
    sector_codes  = {s['sector_code'] for s in sectors}
    rps_raw       = SectorFactors._collect_sector_rps(sector_ohlcv, sector_codes=sector_codes)
    flow_raw      = SectorFactors._collect_sector_capital_flow(sector_capital_flow)
    limit_up_raw  = SectorFactors._collect_limit_up_densities(sectors, sector_stocks, all_stocks_limit_up)
    boards_raw    = SectorFactors._collect_consecutive_boards_max(sectors, sector_stocks, all_stocks_consecutive_boards)
    vol_slope_raw = SectorFactors._collect_volume_ratio_slope(sector_ohlcv, sector_codes=sector_codes)
    rps_norm       = SectorFactors._minmax_normalize(rps_raw)
    flow_norm      = SectorFactors._minmax_normalize(flow_raw)
    limit_up_norm  = SectorFactors._minmax_normalize(limit_up_raw)
    boards_norm    = SectorFactors._minmax_normalize(boards_raw)
    vol_slope_norm = SectorFactors._minmax_normalize(vol_slope_raw)
    all_codes = set(rps_raw) | set(flow_raw) | set(limit_up_raw) | set(boards_raw) | set(vol_slope_raw)
    return {sc: {
        'F1.1_rps_raw':        rps_raw.get(sc, float('nan')),
        'F1.1_rps_norm':       rps_norm.get(sc, 50),
        'F1.2_flow_raw':       flow_raw.get(sc, float('nan')),
        'F1.2_flow_norm':      flow_norm.get(sc, 50),
        'F1.3_limit_up_raw':   limit_up_raw.get(sc, float('nan')),
        'F1.3_limit_up_norm':  limit_up_norm.get(sc, 50),
        'F1.4_boards_raw':     boards_raw.get(sc, float('nan')),
        'F1.4_boards_norm':    boards_norm.get(sc, 50),
        'F1.5_vol_slope_raw':  vol_slope_raw.get(sc, float('nan')),
        'F1.5_vol_slope_norm': vol_slope_norm.get(sc, 50),
    } for sc in all_codes}


def _collect_dragon_raw_factors(sector_stock_list, stock_5d_returns, stock_daily_volumes,
                                stock_consecutive_boards, stock_ohlcv):
    f31_raw = DragonFactors._calculate_leading_performance_raw(
        {s: stock_5d_returns[s] for s in sector_stock_list if s in stock_5d_returns})
    f32_raw = DragonFactors._calculate_popularity_raw(
        {s: stock_daily_volumes[s] for s in sector_stock_list if s in stock_daily_volumes})
    f33_raw = DragonFactors._calculate_height_raw(
        {s: stock_consecutive_boards.get(s, 0) for s in sector_stock_list})
    f34_raw = DragonFactors._calculate_volume_price_resonance_raw(sector_stock_list, stock_ohlcv)
    f35_raw = DragonFactors._compute_bollinger_trend(stock_ohlcv, sector_stock_list)
    f31_norm = DragonFactors._minmax_normalize(f31_raw)
    f32_norm = DragonFactors._minmax_normalize(f32_raw)
    f33_norm = DragonFactors._minmax_normalize(f33_raw)
    f34_norm = DragonFactors._minmax_normalize(f34_raw)
    f35_norm = DragonFactors._minmax_normalize(f35_raw)
    return {code: {
        'F3.1_raw': f31_raw.get(code, float('nan')), 'F3.1_norm': f31_norm.get(code, 50),
        'F3.2_raw': f32_raw.get(code, float('nan')), 'F3.2_norm': f32_norm.get(code, 50),
        'F3.3_raw': f33_raw.get(code, float('nan')), 'F3.3_norm': f33_norm.get(code, 50),
        'F3.4_raw': f34_raw.get(code, float('nan')), 'F3.4_norm': f34_norm.get(code, 50),
        'F3.5_raw': f35_raw.get(code, float('nan')), 'F3.5_norm': f35_norm.get(code, 50),
    } for code in set(f31_raw) | set(f32_raw) | set(f33_raw) | set(f34_raw) | set(f35_raw)}


# ===================== 主测试 =====================

def test_architecture():
    np.random.seed(42)

    TOP_SECTORS    = 3
    TOP_PER_SECTOR = 2
    TOP_FINAL      = 3
    M4_THRESHOLD   = 0.03
    W_SECTOR, W_DRAGON, W_COOP = 0.4, 0.35, 0.25
    EXPECTED_BL_SECTORS = 3

    sectors           = _make_sectors()
    sector_stocks_raw = _make_sector_stocks()
    sector_name_map   = {s['sector_code']: s['sector_name'] for s in sectors}
    # ★ 用 dict.fromkeys 保序去重，避免 set 不确定顺序导致 mock 数据每次运行不同
    main_board_stocks = list(dict.fromkeys(
        code for codes in sector_stocks_raw.values() for code in codes
    ))
    all_stocks_pool   = _make_all_stocks_pool()
    stock_infos       = _make_stock_infos(all_stocks_pool)

    # 所有股票都生成 OHLCV（M3.3布林 + F3.4 量价共振用，保序去重）
    universe_for_ohlcv = list(dict.fromkeys(all_stocks_pool + main_board_stocks))
    stock_ohlcv_all = {code: _make_ohlcv(days=30, seed_offset=i)
                       for i, code in enumerate(universe_for_ohlcv)}

    stock_limit_up           = {code: bool(np.random.rand() > 0.75) for code in main_board_stocks}
    stock_consecutive_boards = {code: int(np.random.randint(0, 6)) for code in main_board_stocks}
    sector_ohlcv             = {s['sector_code']: _make_ohlcv(days=30, seed_offset=200 + i)
                                for i, s in enumerate(sectors)}
    sector_capital_flow      = {
        sc: {
            'main_flow':    float(np.random.uniform(-8e7, 8e7)),
            'retail_flow':  float(np.random.uniform(-4e7, 4e7)),
            'total_volume': float(np.random.uniform(20e7, 80e7)),
        }
        for sc in sector_stocks_raw
    }
    stock_5d_returns    = {code: float(np.random.uniform(-0.12, 0.12)) for code in main_board_stocks}
    stock_daily_volumes = {code: float(np.random.uniform(5e7, 60e7)) for code in main_board_stocks}
    stock_capital_flow  = _make_stock_capital_flow(main_board_stocks)
    stock_5d_net_flow   = _make_stock_5d_net_flow(main_board_stocks)
    stock_main_flow_days = _make_stock_main_flow_days(main_board_stocks)
    stock_turnover_rate  = _make_stock_turnover_rate(main_board_stocks)

    prefilters = PreFilters()

    SEP = '─' * 100
    W = 130
    print(f"\n{'='*100}")
    print(f"  截面日频策略全流程验证  [{len(all_stocks_pool)}只 / {len(sectors)}板块]  "
          f"M1→M2(黑名单+板块层)→M3(主板+个股黑名单+布林+龙头层)→M4+M5→M6")
    print(f"{'='*100}")
    pool_by_type: Dict[str, int] = {}
    for code in all_stocks_pool:
        t = _stock_type_label(code, stock_infos)
        pool_by_type[t] = pool_by_type.get(t, 0) + 1
    for t, n in sorted(pool_by_type.items()):
        print(f"    {t:<10}: {n:>3} 只")

    # ── 原始股票数据全量 ──────────────────────────────────────────
    print(f"\n{'='*W}")
    print(f"  【原始股票数据（全量）】  all_stocks_pool={len(all_stocks_pool)}只  "
          f"main_board_stocks={len(main_board_stocks)}只（板块成分股，有完整资金流数据）")
    print(f"{'='*W}")
    print(f"  {'代码':<14}  {'名称':<10}  {'类型':<6}  "
          f"{'主力净流(亿)':>12}  {'散户净流(亿)':>12}  {'总成交(亿)':>10}  {'净值比':>6}  "
          f"{'连板':>4}  {'涨停':>4}  {'5日收益':>8}  {'日成交(亿)':>10}  "
          f"{'换手率':>7}  {'主力天':>6}  {'5日净流(亿)':>12}")
    print(f"  {'-'*W}")
    for code in all_stocks_pool:
        name  = stock_infos.get(code, {}).get('name', code)
        ttype = _stock_type_label(code, stock_infos)
        fl    = stock_capital_flow.get(code)
        if fl is not None:
            main_f  = fl['main_flow']
            retail_f = fl['retail_flow']
            total_v  = fl['total_volume']
            nr       = main_f / total_v if total_v > 0 else 0.0
            boards   = stock_consecutive_boards.get(code, 0)
            lu       = '✓' if stock_limit_up.get(code, False) else '✗'
            ret5     = stock_5d_returns.get(code, float('nan'))
            dvol     = stock_daily_volumes.get(code, 0.0)
            tr       = stock_turnover_rate.get(code, 0.0)
            mfd      = stock_main_flow_days.get(code, 0)
            nf5      = stock_5d_net_flow.get(code, 0.0)
            print(f"  {code:<14}  {name:<10}  {ttype:<6}  "
                  f"  {main_f/1e7:>+10.2f}亿  {retail_f/1e7:>+10.2f}亿  {total_v/1e7:>9.1f}亿  {nr:>6.3f}  "
                  f"  {boards:>3}天  {lu:>4}  {ret5:>+8.4f}  {dvol/1e7:>9.1f}亿  "
                  f"  {tr*100:>6.1f}%  {mfd:>5}天  {nf5/1e7:>+10.2f}亿")
        else:
            print(f"  {code:<14}  {name:<10}  {ttype:<6}  （不在板块成分股，无资金流/因子数据）")
    print(f"{'='*W}")


    # ── M1 市场情绪（★ 新增门控：红灯终止整个流程）────────────────
    print(f"\n{SEP}")
    print(f"  【M1 市场情绪】  大盘状态评估  ★ 红灯=禁止开新仓，流程终止")
    print(f"{SEP}")

    index_ohlcv      = _make_ohlcv(days=60, seed_offset=999)
    market_sentiment = MarketFactors.calculate_market_sentiment(index_ohlcv, index_name='沪深300(mock)')

    grade_label = {'green': '✅ 绿灯(正常开仓)', 'yellow': '⚠️ 黄灯(减半仓)', 'red': '🔴 红灯(禁止开仓)'}
    print(f"  市场综合得分:  {market_sentiment['market_score']:.1f} / 100")
    print(f"  风险等级:      {market_sentiment['market_grade'].upper()}  {grade_label.get(market_sentiment['market_grade'], '')}")
    print(f"  仓位缩放系数:  {market_sentiment['position_scale']}")
    print(f"  允许开新仓:    {'是' if market_sentiment['allow_new_open'] else '否'}")
    print(f"  子因子得分:")
    print(f"    MF1 趋势强度(30%):   {market_sentiment['mf1_trend']:>6.1f}  "
          f"斜率={market_sentiment['detail'].get('mf1_slope_pct', float('nan')):.6f}")
    print(f"    MF2 布林带位置(25%):  {market_sentiment['mf2_boll']:>6.1f}  "
          f"布林位={market_sentiment['detail'].get('mf2_boll_pct', float('nan')):.4f}")
    print(f"    MF3 量能状态(20%):   {market_sentiment['mf3_volume']:>6.1f}  "
          f"量比={market_sentiment['detail'].get('mf3_vol_ratio', float('nan')):.4f}")
    print(f"    MF4 近期动量(15%):   {market_sentiment['mf4_momentum']:>6.1f}  "
          f"5日涨跌={market_sentiment['detail'].get('mf4_momentum_5d', float('nan')):.4f}")
    print(f"    MF5 波动率压制(10%):  {market_sentiment['mf5_volatility']:>6.1f}  "
          f"ATR比={market_sentiment['detail'].get('mf5_atr_ratio_inv', float('nan')):.6f}")

    if not market_sentiment['allow_new_open']:
        print(f"\n  🔴 M1 市场等级=red，当日禁止开新仓，流程终止（与 pipeline.run_pipeline 行为一致）")
        print(f"\n{'='*100}")
        print(f"  数量递减验证（M1红灯提前退出）")
        print(f"{'='*100}")
        print(f"  M1 市场情绪: 得分={market_sentiment['market_score']:.1f}  等级=RED  → 流程终止，无后续步骤")
        print(f"{'='*100}\n")
        assert market_sentiment['market_grade'] == 'red'
        assert not market_sentiment['allow_new_open']
        print("✅ 所有断言通过（M1红灯提前退出逻辑验证成功）")
        return


    # ── M2.1 板块黑名单 ──────────────────────────────────────────
    filtered_sectors      = prefilters.filter_sectors(sectors)
    filtered_sector_codes = {s['sector_code'] for s in filtered_sectors}

    print(f"\n{SEP}")
    print(f"  【M2.1 板块黑名单】  {len(sectors)} 个  →  {len(filtered_sectors)} 个")
    print(f"  黑名单模式: {sorted(prefilters.sector_blacklist)}")
    print(f"  个股黑名单: {sorted(prefilters.stock_blacklist.keys())}")
    print(f"{SEP}")
    print(f"  {'板块代码':<8}  {'板块名称':<14}  {'状态':<10}  命中模式")
    for s in sectors:
        sc, name = s['sector_code'], s['sector_name']
        passed = sc in filtered_sector_codes
        pat    = _find_blacklist_pattern(name, prefilters.sector_blacklist)
        status = "✓ 通过" if passed else "✗ 黑名单"
        print(f"  {sc:<8}  {name:<14}  {status:<10}  {pat if pat else '—'}")


    # ── M2 板块层 ─────────────────────────────────────────────────
    print(f"\n{SEP}")
    print(f"  【M2 板块层】  {len(filtered_sectors)} 个活跃板块  →  选 top {TOP_SECTORS}")
    print(f"  5因子等权: F1.1=20日RPS  F1.2=资金净流入  F1.3=涨停浓度  F1.4=最高连板  F1.5=成交占比斜率")
    print(f"{SEP}")

    raw_factors = _collect_sector_raw_factors(
        filtered_sectors, sector_stocks_raw, sector_ohlcv, sector_capital_flow,
        stock_limit_up, stock_consecutive_boards
    )
    m2_scores = SectorFactors.calculate_all_sector_factors(
        filtered_sectors, sector_stocks_raw, stock_ohlcv_all,
    )
    top_sectors      = sorted(m2_scores.items(), key=lambda x: x[1], reverse=True)[:TOP_SECTORS]
    top_sector_codes = {code for code, _ in top_sectors}

    print(f"\n  {'板块':<16}  {'M2综合':>6}  "
          f"{'F1.1RPS':>10}{'归一':>6}  {'F1.2(亿)':>10}{'归一':>6}  "
          f"{'F1.3涨停%':>9}{'归一':>6}  {'F1.4连板':>9}{'归一':>6}  "
          f"{'F1.5斜率':>10}{'归一':>6}  状态")
    for rank, (sc, score) in enumerate(sorted(m2_scores.items(), key=lambda x: x[1], reverse=True), 1):
        flag = "★ 入选" if sc in top_sector_codes else "  排除"
        f    = raw_factors.get(sc, {})
        lp   = f.get('F1.3_limit_up_raw', float('nan'))
        lp_s = f"{lp*100:.1f}%" if not np.isnan(lp) else "  nan%"
        print(f"  #{rank:<2} {sc}({sector_name_map.get(sc,'?'):<6})  {score:>6.2f}  "
              f"  {f.get('F1.1_rps_raw', float('nan')):>+9.4f}{f.get('F1.1_rps_norm', 50):>6.1f}  "
              f"  {f.get('F1.2_flow_raw', 0)/1e7:>8.1f}亿{f.get('F1.2_flow_norm', 50):>6.1f}  "
              f"  {lp_s:>9}{f.get('F1.3_limit_up_norm', 50):>6.1f}  "
              f"  {f.get('F1.4_boards_raw', float('nan')):>8.0f}天{f.get('F1.4_boards_norm', 50):>6.1f}  "
              f"  {f.get('F1.5_vol_slope_raw', float('nan')):>10.6f}{f.get('F1.5_vol_slope_norm', 50):>6.1f}  "
              f"  {flag}")
    print(f"\n  → 选出: {[f'{c}({sector_name_map[c]}, {s:.2f})' for c, s in top_sectors]}")


    # ── M3.1 主板过滤 ─────────────────────────────────────────────
    m31_passed     = asyncio.run(prefilters.apply_main_board_filter(all_stocks_pool, stock_infos))
    m31_passed_set = set(m31_passed)

    print(f"\n{SEP}")
    print(f"  【M3.1 主板过滤】  {len(all_stocks_pool)} 只  →  {len(m31_passed)} 只")
    print(f"{SEP}")
    print(f"  汇总：", end="")
    for t in ['主板沪', '主板深', 'ST', '退市', '创业板', '科创板', '北交所']:
        n_pass = sum(1 for c in all_stocks_pool if _stock_type_label(c, stock_infos) == t and c in m31_passed_set)
        n_all  = pool_by_type.get(t, 0)
        if n_all:
            print(f"{t} {n_pass}/{n_all}  ", end="")
    print()
    print(f"  被过滤的票:")
    for code in all_stocks_pool:
        if code in m31_passed_set:
            continue
        ttype = _stock_type_label(code, stock_infos)
        name  = stock_infos.get(code, {}).get('name', code)
        print(f"    ✗ {code:<14} ({ttype:<6}) {name}")


    # ── M3.2 个股黑名单 ───────────────────────────────────────────
    stock_blacklist_set = set(prefilters.stock_blacklist.keys())
    m32_passed_set      = {c for c in m31_passed_set if c not in stock_blacklist_set}
    m32_filtered_codes  = m31_passed_set - m32_passed_set

    print(f"\n{SEP}")
    print(f"  【M3.2 个股黑名单】  {len(m31_passed)} 只  →  {len(m32_passed_set)} 只")
    print(f"  个股黑名单: {sorted(stock_blacklist_set)}")
    print(f"{SEP}")
    if m32_filtered_codes:
        for code in sorted(m32_filtered_codes):
            name = stock_infos.get(code, {}).get('name', code)
            print(f"    ✗ {code:<14} {name}  (个股黑名单)")
    else:
        print(f"  （黑名单中的票均不在主板通过集，无额外过滤）")


    # 布林过滤已移至 DragonFactors 的 F3.5 因子（软评分，不再硬过滤）
    m33_passed_set = m32_passed_set  # 保持变量名兼容
    print(f"\n{SEP}")
    print(f"  【M3.3 布林过滤】  已移至 F3.5 因子（软评分），不再硬过滤")
    print(f"  通过 M3.2 的 {len(m33_passed_set)} 只全部进入龙头层")
    print(f"{SEP}")


    # ── M3 龙头层（★ 候选池 = 板块成分 ∩ M3.1/M3.2通过股）──
    print(f"\n{SEP}")
    print(f"  【M3 龙头层】  {len(top_sectors)} 板块 × top {TOP_PER_SECTOR}/板块  =  预计 ≤{TOP_SECTORS*TOP_PER_SECTOR} 只")
    print(f"  5因子权重: F3.1超额={_W_F31}  F3.2成交={_W_F32}  F3.3连板={_W_F33}  F3.4量价={_W_F34}  F3.5布林={_W_F35}")
    print(f"  ★ 候选池 = 板块成分 ∩ M3.1主板通过 ∩ M3.2个股黑名单通过")
    print(f"{SEP}")

    all_candidates = []
    m3_pool_filtered_total = 0

    for sector_code, sector_m2_score in top_sectors:
        full_list      = sector_stocks_raw.get(sector_code, [])
        candidate_pool = [s for s in full_list if s in m33_passed_set]
        m3_pool_filtered_total += (len(full_list) - len(candidate_pool))

        m3_scores  = DragonFactors.calculate_all_dragon_factors_in_sector(
            candidate_pool, stock_ohlcv_all
        )
        dragon_raw = _collect_dragon_raw_factors(
            candidate_pool, stock_5d_returns, stock_daily_volumes,
            stock_consecutive_boards, stock_ohlcv_all
        )
        selected_codes = {c for c, _ in sorted(m3_scores.items(), key=lambda x: x[1], reverse=True)[:TOP_PER_SECTOR]}

        rejected = [s for s in full_list if s not in candidate_pool]
        print(f"\n  板块 {sector_code}({sector_name_map[sector_code]})  M2={sector_m2_score:.2f}  "
              f"成分股{len(full_list)}只 → 候选池{len(candidate_pool)}只 (剔除{len(rejected)}只)")
        if rejected:
            reasons = []
            for r in rejected:
                if r not in m31_passed_set:
                    reasons.append(f"{r}(M3.1-{_stock_type_label(r, stock_infos)})")
                elif r in stock_blacklist_set:
                    reasons.append(f"{r}(M3.2-个股黑名单)")
                else:
                    reasons.append(f"{r}(不在候选池)")
            print(f"    剔除原因: {', '.join(reasons)}")

        if not m3_scores:
            print(f"    ⚠️ 候选池为空，跳过")
            continue

        print(f"  {'代码':<14}  {'M3综合':>7}  "
              f"{'F3.1超额':>10}{'归一':>6}  {'F3.2成交(亿)':>11}{'归一':>6}  "
              f"{'F3.3连板':>8}{'归一':>6}  {'F3.4VPR':>8}{'归一':>6}  状态")
        for code, score in sorted(m3_scores.items(), key=lambda x: x[1], reverse=True):
            tag = "▶ 入选" if code in selected_codes else "  排除"
            dr  = dragon_raw.get(code, {})
            print(f"  {code:<14}  {score:>7.2f}  "
                  f"  {dr.get('F3.1_raw', 0):>+9.4f}{dr.get('F3.1_norm', 50):>6.1f}  "
                  f"  {dr.get('F3.2_raw', 0)/1e7:>9.1f}亿{dr.get('F3.2_norm', 50):>6.1f}  "
                  f"  {dr.get('F3.3_raw', 0):>7.0f}天{dr.get('F3.3_norm', 50):>6.1f}  "
                  f"  {dr.get('F3.4_raw', 0):>7.2f}{dr.get('F3.4_norm', 50):>6.1f}  "
                  f"  {tag}")

        for code, m3_score in sorted(m3_scores.items(), key=lambda x: x[1], reverse=True)[:TOP_PER_SECTOR]:
            fl = stock_capital_flow.get(code, {})
            all_candidates.append({
                'code': code, 'sector_code': sector_code, 'dragon_score': m3_score,
                'main_flow':      fl.get('main_flow', 0),
                'retail_flow':    fl.get('retail_flow', 0),
                'total_volume':   fl.get('total_volume', 0),
                'net_flow_5d':    stock_5d_net_flow.get(code, 0),
                'main_flow_days': stock_main_flow_days.get(code, 0),
                'turnover_rate':  stock_turnover_rate.get(code, 0.0),
            })

    print(f"\n  → M3 共选出 {len(all_candidates)} 只龙头候选")

    # 验证：候选票必须全部通过 M3.1/M3.2
    candidate_codes = [c['code'] for c in all_candidates]
    assert all(c not in stock_blacklist_set for c in candidate_codes), \
        f"✗ 个股黑名单泄漏: {set(candidate_codes) & stock_blacklist_set}"
    assert all(c in m33_passed_set for c in candidate_codes), \
        f"✗ M3.1/M3.2 过滤泄漏: {set(candidate_codes) - m33_passed_set}"
    print(f"  ✓ 验证通过：候选池不含个股黑名单/非M3.1/M3.2通过票")


    # ── M4+M5 合力+最终合成 ──────────────────────────────────────
    print(f"\n{SEP}")
    print(f"  【M4+M5 合力+最终合成】  门槛: 主力净流入/总成交 >= {M4_THRESHOLD*100:.0f}%")
    print(f"  4因子: coop1=净值比(35%) coop2=主散比(25%) coop3=持续天数(25%) coop4=换手质量(15%)")
    print(f"  主散比修正: 散户同向→比值<1(跟风) | 散户=0→1.0(纯机构) | 散户背离→>1(主力接盘,高质量)")
    print(f"{SEP}")
    print(f"  {'代码':<14}  {'板块':<8}  {'主力(亿)':>9}  {'散户(亿)':>9}  "
          f"{'净值比':>7}  {'主散比':>7}  {'类型':<10}  {'持续天':>5}  {'换手率':>7}  状态")
    for c in all_candidates:
        main, retail, total = c['main_flow'], c['retail_flow'], c['total_volume']
        nr = main / total if total > 0 and main > 0 else 0
        passed = main > 0 and nr >= M4_THRESHOLD
        status = "✓ 通过" if passed else "✗ 过滤"
        if main <= 0:
            mr = 0.0
            kind = "主力流出"
        elif retail >= 0:
            mr = main / (main + retail + 1e-6)
            kind = "散户同向" if retail > 0 else "纯机构"
        else:
            bonus = min(main / max(abs(retail), main * 0.1), 3.0)
            mr = 1.0 + bonus
            kind = "主力接盘"
        sc = c['sector_code']
        print(f"  {c['code']:<14}  {sector_name_map[sc]:<8}  "
              f"{main/1e7:>+8.2f}  {retail/1e7:>+8.2f}  "
              f"{nr:>7.3f}  {mr:>7.2f}  {kind:<10}  {c['main_flow_days']:>4}天  "
              f"{c['turnover_rate']*100:>6.1f}%  {status}")

    # 注意：第二个参数传 m2_scores（板块分），而非 m3_scores（龙头分）
    final_ranked = ForceFactors.apply_cooperative_force_and_score(
        all_candidates, top_sectors,
        m4_threshold=M4_THRESHOLD,
        w_sector=W_SECTOR, w_dragon=W_DRAGON, w_coop=W_COOP,
        stock_flow_recent={},
        stock_ohlcv=stock_ohlcv_all,
    )
    sector_rank_scores = {code: max(0, 100 - idx * 30) for idx, (code, _) in enumerate(top_sectors)}

    print(f"\n{SEP}")
    print(f"  【M5 最终得分汇总】  {len(final_ranked)} 只通过合力过滤")
    print(f"  M5 = {W_SECTOR}×板块分 + {W_DRAGON}×龙头分 + {W_COOP}×合力综合")
    print(f"{SEP}")
    print(f"  {'排名':<4}  {'代码':<14}  {'板块':<8}  {'最终分':>7}  "
          f"{'板块分':>6}  {'龙头分':>7}  {'合力综合':>8}  "
          f"{'净值比归一':>10}  {'主散比归一':>10}  {'持续归一':>9}  {'换手归一':>9}")
    for rank, c in enumerate(final_ranked, 1):
        sc = c['sector_code']
        print(f"  #{rank:<3}  {c['code']:<14}  {sector_name_map[sc]:<8}  "
              f"{c['final_score']:>7.2f}  {sector_rank_scores.get(sc, 0):>6.1f}  "
              f"{c['dragon_score']:>7.2f}  {c['coop_score']:>8.2f}  "
              f"{c['coop1_norm']:>10.1f}  {c['coop2_norm']:>10.1f}  "
              f"{c['coop3_norm']:>9.1f}  {c['coop4_norm']:>9.1f}")


    # ── M6 最终信号 ──────────────────────────────────────────────
    top_final = final_ranked[:TOP_FINAL]
    print(f"\n{SEP}")
    print(f"  【M6 最终信号】  top {TOP_FINAL}")
    print(f"{SEP}")
    for rank, c in enumerate(top_final, 1):
        sc = c['sector_code']
        print(f"  #{rank}  {c['code']}({sector_name_map[sc]})  "
              f"最终得分={c['final_score']:.2f}  板块分={sector_rank_scores.get(sc, 0):.1f}  "
              f"龙头分={c['dragon_score']:.2f}  合力={c['coop_score']:.2f}")


    # ── 数量递减汇总 ──────────────────────────────────────────────
    m21_filtered = len(sectors) - len(filtered_sectors)
    m31_filtered = len(all_stocks_pool) - len(m31_passed)
    m32_filtered = len(m31_passed_set) - len(m32_passed_set)
    m45_filtered = len(all_candidates) - len(final_ranked)

    print(f"\n{'='*100}")
    print(f"  数量递减验证")
    print(f"{'='*100}")
    print(f"  原始股票池:                  {len(all_stocks_pool):>4} 只")
    print(f"  M1  市场情绪:                得分={market_sentiment['market_score']:.1f}  "
          f"等级={market_sentiment['market_grade'].upper()}  "
          f"仓位系数={market_sentiment['position_scale']}")
    print(f"  M2.1 板块黑名单:              {len(filtered_sectors):>4} 个  (剔除 {m21_filtered} 个: 房地产/光伏/贵金属)")
    print(f"  M2   主线板块:                {len(top_sectors):>4} / {len(filtered_sectors)} 个")
    print(f"  M3.1 主板过滤:                {len(m31_passed):>4} 只  (剔除 {m31_filtered} 只: 创业板/科创板/北交/ST/退市)")
    print(f"  M3.2 个股黑名单:              {len(m32_passed_set):>4} 只  (剔除 {m32_filtered} 只)")
    print(f"  M3.3 布林:                    已移至 F3.5 因子（软评分）")
    print(f"  M3   龙头候选(交集):           {len(all_candidates):>3} 只  (从板块成分中剔除 {m3_pool_filtered_total} 只)")
    print(f"  M4+M5 合力通过:               {len(final_ranked):>4} 只  (剔除 {m45_filtered} 只)")
    print(f"  M6   最终信号:                {len(top_final):>4} 只")
    print(f"{'='*100}\n")


    # ── 断言 ──────────────────────────────────────────────────────
    # M1
    assert isinstance(market_sentiment['market_score'], float)
    assert market_sentiment['market_grade'] in ('green', 'yellow', 'red')
    assert market_sentiment['allow_new_open'], "已到达此处，market_grade 不应为 red"

    # M2.1
    assert len(filtered_sectors) == len(sectors) - EXPECTED_BL_SECTORS
    bl_names = [s['sector_name'] for s in sectors if s['sector_code'] not in filtered_sector_codes]
    assert '房地产' in bl_names and '光伏设备' in bl_names and '贵金属矿业' in bl_names

    # M2
    assert len(top_sectors) == TOP_SECTORS
    assert all(sc not in {'SC004', 'SC006', 'SC009'} for sc, _ in top_sectors)

    # M3.1：保留 = 主板沪/主板深（剔除 ST/*ST/退市/创业板/科创板/北交所）
    # 用 _stock_type_label 分类，与下方泄漏断言口径一致；勿用 is_st(code)（代码不含ST会恒真）
    m31_expected = sum(1 for c in all_stocks_pool
                       if _stock_type_label(c, stock_infos) in ('主板沪', '主板深'))
    assert len(m31_passed) == m31_expected, f"M3.1通过数应为{m31_expected}，实际{len(m31_passed)}"
    for ttype in ['ST', '退市', '创业板', '科创板', '北交所']:
        leaked = [c for c in all_stocks_pool
                  if _stock_type_label(c, stock_infos) == ttype and c in m31_passed_set]
        assert not leaked, f"M3.1 泄漏 {ttype}: {leaked}"

    # M3.2
    assert not any(c in stock_blacklist_set for c in m32_passed_set), "M3.2 个股黑名单泄漏"

    # M3.3 布林已移至 F3.5 因子，m33_passed_set 现在等于 m32_passed_set
    assert len(m33_passed_set) == len(m32_passed_set)
    assert len(m33_passed_set) > 0

    # M3（关键断言）
    assert all(c['code'] in m33_passed_set for c in all_candidates), \
        "M3 候选必须全部通过 M3.1/M3.2"
    assert not any(c['code'] in stock_blacklist_set for c in all_candidates), \
        "M3 候选不能含个股黑名单票"
    assert len(all_candidates) <= TOP_SECTORS * TOP_PER_SECTOR

    # M4+M5
    assert len(final_ranked) <= len(all_candidates)
    assert len(final_ranked) > 0
    # 通过的候选必须满足净值比门槛
    for c in final_ranked:
        nr = c['main_flow'] / c['total_volume']
        assert nr >= M4_THRESHOLD, f"{c['code']} 净值比 {nr:.3f} 不应通过门槛"

    # 主散比单测：散户背离应有更高分（>1.0），散户同向<1.0
    sample_backflow = next((c for c in final_ranked if c.get('retail_flow', 0) < 0), None)
    sample_sameflow = next((c for c in final_ranked if c.get('retail_flow', 0) > 0), None)
    if sample_backflow and sample_sameflow:
        bf = sample_backflow['main_flow'] / max(abs(sample_backflow['retail_flow']),
                                                 sample_backflow['main_flow'] * 0.1)
        bf_score = 1.0 + min(bf, 3.0)
        sf_score = sample_sameflow['main_flow'] / (sample_sameflow['main_flow'] + sample_sameflow['retail_flow'] + 1e-6)
        assert bf_score > sf_score, f"散户背离主散比({bf_score:.2f})应高于散户同向({sf_score:.2f})"
        print(f"  ✓ 主散比语义验证: 散户背离={bf_score:.2f} > 散户同向={sf_score:.2f}")

    # M6
    assert len(top_final) == min(TOP_FINAL, len(final_ranked))

    print("✅ 所有断言通过")


if __name__ == '__main__':
    test_architecture()
