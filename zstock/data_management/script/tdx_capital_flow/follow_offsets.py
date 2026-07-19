"""
根据索引偏移量查找实际数据
"""
import struct

def follow_offsets(file_path: str):
    print(f'\n{"="*60}')
    print(f'File: {file_path}')

    with open(file_path, 'rb') as f:
        # 读取文件头
        header = f.read(12)
        num_stocks, num_days, data_offset = struct.unpack('<III', header)
        print(f'Header: {num_stocks} stocks, {num_days} days, data_offset={data_offset}')

        # 读取第一个股票记录 (从 86012 或 73724 开始)
        if 'shexday' in file_path:
            stock_offset = 86012
        else:
            stock_offset = 73724

        f.seek(stock_offset)
        record = f.read(256)

        # 解析股票代码
        code = record[4:10].decode('ascii', errors='ignore')
        print(f'\n股票代码: {code}')

        # 读取偏移量数组 (从 offset 28 开始)
        print(f'\n偏移量数组:')
        offsets = []
        for i in range(28, min(128, len(record)), 4):
            offset_val = struct.unpack('<I', record[i:i+4])[0]
            if offset_val > 0:
                offsets.append(offset_val)
                print(f'  [{i}] = {offset_val}')
            if len(offsets) >= 10:
                break

        # 尝试跳转到第一个偏移量看看数据
        if offsets:
            first_offset = offsets[0]
            print(f'\n跳转到 offset {first_offset}...')
            f.seek(first_offset)
            data = f.read(256)

            print(f'数据 hex: {data[:64].hex()}')

            # 尝试解读为日期 + 资金流数据
            print(f'\n尝试解读:')
            date_val = struct.unpack('<I', data[0:4])[0]
            print(f'  [0:4] uint32: {date_val}')
            if 20000000 <= date_val <= 20261231:
                print(f'    -> 日期: {date_val}')

            tradenum = struct.unpack('<f', data[4:8])[0]
            print(f'  [4:8] float: {tradenum:.1f}')

            # 尝试读取 12 个 float
            if len(data) >= 56:
                floats = struct.unpack('<12f', data[8:56])
                print(f'  [8:56] 12 floats:')
                for i in range(0, 12, 3):
                    print(f'    [{i:2d}:{i+3:2d}]: {floats[i:i+3]}')

            # 看看第二个偏移量
            if len(offsets) >= 2:
                second_offset = offsets[1]
                print(f'\n跳转到第二个 offset {second_offset}...')
                f.seek(second_offset)
                data2 = f.read(256)
                print(f'数据 hex: {data2[:64].hex()}')

                date_val2 = struct.unpack('<I', data2[0:4])[0]
                print(f'  日期: {date_val2}')
                if 20000000 <= date_val2 <= 20261231:
                    print(f'    -> 格式化: {date_val2}')

# 测试两个文件
follow_offsets(r'E:\02Learn\09\TradingAgents-CN\zstock\data_management\script\tdx_capital_flow\shexday.pkg')
follow_offsets(r'E:\02Learn\09\TradingAgents-CN\zstock\data_management\script\tdx_capital_flow\szexday.pkg')
