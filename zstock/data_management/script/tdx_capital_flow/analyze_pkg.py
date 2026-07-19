"""
重新解析通达信 pkg 文件 - 从头开始
根据之前的分析，文件结构是：
- 12字节头：num_stocks, num_days, data_offset
- data_offset 后是实际数据
"""
import struct
from pathlib import Path

def analyze_pkg(file_path: str):
    print(f'\n{"="*80}')
    print(f'File: {file_path}')

    with open(file_path, 'rb') as f:
        # 读取文件头
        header = f.read(12)
        num_stocks, num_days, data_offset = struct.unpack('<III', header)

        print(f'Header:')
        print(f'  num_stocks: {num_stocks}')
        print(f'  num_days: {num_days}')
        print(f'  data_offset: {data_offset}')

        # 跳转到 data_offset
        f.seek(data_offset)
        file_size = f.seek(0, 2)
        f.seek(data_offset)

        print(f'File size: {file_size}')
        print(f'Data region: {data_offset} ~ {file_size} ({file_size - data_offset} bytes)')

        # 扫描非零数据
        print(f'\n扫描非零数据区域...')
        chunk_size = 1024 * 1024  # 1MB
        offset = data_offset
        first_nonzero = None
        nonzero_regions = []

        while offset < file_size:
            f.seek(offset)
            chunk = f.read(min(chunk_size, file_size - offset))
            if not chunk:
                break

            # 查找非零字节
            for i, byte in enumerate(chunk):
                if byte != 0:
                    if first_nonzero is None:
                        first_nonzero = offset + i
                    nonzero_regions.append(offset + i)
                    break

            offset += len(chunk)

            # 找到前100个非零区域后停止
            if len(nonzero_regions) >= 100:
                break

        if first_nonzero is None:
            print('未找到非零数据')
            return

        print(f'\n第一个非零数据位置: {first_nonzero} (offset {first_nonzero - data_offset} from data_offset)')

        # 从第一个非零数据开始解析
        print(f'\n从位置 {first_nonzero} 开始尝试解析...')
        f.seek(first_nonzero)
        sample = f.read(512)

        print(f'\n前 512 字节 hex:')
        for i in range(0, min(256, len(sample)), 32):
            hex_str = sample[i:i+32].hex()
            print(f'  {i:3d}: {hex_str}')

        # 尝试不同的记录大小
        print(f'\n尝试不同记录大小...')
        for rec_sz in [40, 48, 56, 64, 80, 96]:
            f.seek(first_nonzero)
            data = f.read(rec_sz * 5)

            # 尝试解析为 date + tradenum + floats
            try:
                date_val = struct.unpack_from('<I', data, 0)[0]
                tradenum = struct.unpack_from('<f', data, 4)[0]
                floats = struct.unpack_from(f'<{(rec_sz-8)//4}f', data, 8)

                # 检查日期是否合理 (20200101 ~ 20261231)
                if 20000000 <= date_val <= 20261231:
                    print(f'\n  REC_SZ={rec_sz}: date={date_val}, tradenum={tradenum:.1f}')
                    print(f'    floats: {floats[:6]}')
                else:
                    print(f'  REC_SZ={rec_sz}: date={date_val} (不合理)')
            except:
                pass

        # 查找股票代码
        print(f'\n查找股票代码...')
        f.seek(max(0, first_nonzero - 1000))
        search_data = f.read(2000)

        # 查找 6 位数字 ASCII
        stock_codes = []
        for i in range(len(search_data) - 6):
            try:
                code = search_data[i:i+6].decode('ascii', errors='ignore')
                if code.isdigit() and len(code) == 6:
                    if code.startswith('6') or code.startswith('0') or code.startswith('3'):
                        stock_codes.append((max(0, first_nonzero - 1000) + i, code))
            except:
                pass

        if stock_codes:
            print(f'找到 {len(stock_codes)} 个可能的股票代码:')
            for offset, code in stock_codes[:20]:
                print(f'  offset {offset}: {code}')
        else:
            print('未找到股票代码')

for pkg_file in [
    r'E:\02Learn\09\TradingAgents-CN\zstock\data_management\script\tdx_capital_flow\shexday.pkg',
    r'E:\02Learn\09\TradingAgents-CN\zstock\data_management\script\tdx_capital_flow\szexday.pkg',
]:
    analyze_pkg(pkg_file)
