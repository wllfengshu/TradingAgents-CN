"""
按照阿木教程解析通达信 pkg 资金流数据
第一步：盲拆验证 56B 假设
"""
import struct
from pathlib import Path

# 测试三个文件
files = {
    'sh': r'E:\02Learn\09\TradingAgents-CN\zstock\data_management\script\tdx_capital_flow\shexday.pkg',
    'sz': r'E:\02Learn\09\TradingAgents-CN\zstock\data_management\script\tdx_capital_flow\szexday.pkg',
    'bj': r'E:\02Learn\09\TradingAgents-CN\zstock\data_management\script\tdx_capital_flow\bjexday.pkg',
}

for market, pkg_path in files.items():
    print(f'\n{"="*80}')
    print(f'Market: {market.upper()}')
    print(f'File: {pkg_path}')

    with open(pkg_path, 'rb') as f:
        buf = f.read()

    file_size = len(buf)
    print(f'文件大小: {file_size:,} 字节')

    # 假设 56B/条
    REC_SZ = 56
    n_rec = file_size // REC_SZ
    remainder = file_size % REC_SZ
    print(f'按 {REC_SZ}B/条: {n_rec:,} 条, 余数 {remainder} 字节')

    # 盲拆前 20 条
    print(f'\n前 20 条记录:')
    print(f'{"#":>3} {"offset":>8} {"date":>10} {"tradenum":>12}  f0-f11 (12 floats)')

    dates = []
    for i in range(min(20, n_rec)):
        off = i * REC_SZ
        # 前 8 字节: date (uint32) + tradenum (float)
        date_val = struct.unpack_from('<I', buf, off)[0]
        tradenum = struct.unpack_from('<f', buf, off + 4)[0]
        # 后 48 字节: 12 floats
        floats = struct.unpack_from('<12f', buf, off + 8)

        dates.append(date_val)

        # 格式化输出
        float_str = ' '.join(f'{x:>8.1f}' for x in floats)
        print(f'{i:>3} {off:>8} {date_val:>10} {tradenum:>12.1f}  {float_str}')

    # 检查日期是否合理
    print(f'\n日期范围: {min(dates)} ~ {max(dates)}')

    # 查找股票边界（日期回退的位置）
    print(f'\n查找股票边界...')
    switch_points = []
    for i in range(1, min(n_rec, 10000)):
        t_prev = struct.unpack_from('<I', buf, (i-1)*REC_SZ)[0]
        t_curr = struct.unpack_from('<I', buf, i*REC_SZ)[0]
        if t_curr < t_prev:  # 日期回退 = 换股票
            switch_points.append((i, t_prev, t_curr))
            if len(switch_points) <= 5:
                prev_switch = switch_points[-2][0] if len(switch_points) >= 2 else 0
                block_size = i - prev_switch
                print(f'  边界 #{len(switch_points)} @{i}条: {t_prev} → {t_curr}, 块长≈{block_size}条')

    print(f'\n共找到 {len(switch_points)} 个切换点')
    if switch_points:
        first_block = switch_points[0][0]
        print(f'第一块长度: {first_block} 条（应为交易日数）')
