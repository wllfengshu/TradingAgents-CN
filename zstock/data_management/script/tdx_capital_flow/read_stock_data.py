"""
假设索引记录后紧跟实际数据
[24] = 18 可能表示有 18 个数据记录
"""
import struct

def read_stock_data(file_path: str, stock_offset: int):
    print(f'\n{"="*60}')
    print(f'File: {file_path}')

    with open(file_path, 'rb') as f:
        # 读取股票索引记录
        f.seek(stock_offset)
        index_record = f.read(128)

        # 解析基本信息
        code = index_record[4:10].decode('ascii', errors='ignore')
        val20 = struct.unpack('<I', index_record[20:24])[0]
        num_records = struct.unpack('<I', index_record[24:28])[0]

        print(f'股票代码: {code}')
        print(f'[20] = {val20}')
        print(f'[24] = {num_records} (数据记录数?)')

        # 假设索引记录是 128 字节，实际数据从 stock_offset + 128 开始
        data_start = stock_offset + 128
        print(f'\n假设数据从 offset {data_start} 开始...')

        f.seek(data_start)
        data = f.read(1024)

        print(f'\n前 256 字节 hex:')
        for i in range(0, min(256, len(data)), 32):
            print(f'  {i:3d}: {data[i:i+32].hex()}')

        # 尝试解读为 56 字节记录 (4+4+48)
        print(f'\n尝试解读为 56 字节记录:')
        rec_size = 56
        for i in range(min(num_records, 10)):
            offset = i * rec_size
            if offset + rec_size > len(data):
                break

            date_val = struct.unpack('<I', data[offset:offset+4])[0]
            tradenum = struct.unpack('<f', data[offset+4:offset+8])[0]

            # 检查日期是否合理
            is_date = 20000000 <= date_val <= 20261231

            print(f'  Record {i}: date={date_val} ({"是日期" if is_date else "不是日期"}), tradenum={tradenum:.1f}')

            if is_date:
                # 读取 12 个 float
                if offset + 56 <= len(data):
                    floats = struct.unpack('<12f', data[offset+8:offset+56])
                    print(f'    floats[0:6]: {floats[0:6]}')

        # 如果不是 56 字节，试试其他大小
        if not (20000000 <= struct.unpack('<I', data[0:4])[0] <= 20261231):
            print(f'\n56字节假设不对，尝试其他记录大小...')
            for rec_size in [40, 48, 64, 80]:
                test_val = struct.unpack('<I', data[0:4])[0]
                print(f'  rec_size={rec_size}: first 4 bytes = {test_val}')

# 测试
read_stock_data(
    r'E:\02Learn\09\TradingAgents-CN\zstock\data_management\script\tdx_capital_flow\shexday.pkg',
    86012
)

read_stock_data(
    r'E:\02Learn\09\TradingAgents-CN\zstock\data_management\script\tdx_capital_flow\szexday.pkg',
    73724
)
