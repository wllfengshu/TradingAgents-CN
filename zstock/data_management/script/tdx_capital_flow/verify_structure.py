"""
简单验证通达信 pkg 数据结构
从已知位置开始解析
"""
import struct

# 从分析中已知的信息：
# SH: 第一个非零数据在 86012，股票代码 "880033" 在 86014
# SZ: 第一个非零数据在 73724，股票代码 "000026" 在 73728

def verify_structure(file_path: str, first_data_offset: int):
    print(f'\n{"="*60}')
    print(f'File: {file_path}')

    with open(file_path, 'rb') as f:
        # 读取文件头
        header = f.read(12)
        num_stocks, num_days, data_offset = struct.unpack('<III', header)
        print(f'Header: {num_stocks} stocks, {num_days} days')

        # 跳转到第一个非零数据位置
        f.seek(first_data_offset)
        data = f.read(512)

        print(f'\n数据 hex (前 256 字节):')
        for i in range(0, min(256, len(data)), 32):
            print(f'  {i:3d}: {data[i:i+32].hex()}')

        # 尝试解析
        print(f'\n尝试解析:')

        # 前 4 字节
        val1 = struct.unpack('<I', data[0:4])[0]
        print(f'  [0:4] uint32: {val1} (0x{val1:08x})')

        # 接下来 6 字节 (股票代码)
        code = data[4:10].decode('ascii', errors='ignore')
        print(f'  [4:10] ASCII: "{code}"')

        # 接下来 2 字节 (可能是 padding)
        pad = struct.unpack('<H', data[10:12])[0]
        print(f'  [10:12] uint16: {pad}')

        # 接下来 4 字节 (可能是日期)
        date_val = struct.unpack('<I', data[12:16])[0]
        print(f'  [12:16] uint32: {date_val} (0x{date_val:08x})')
        if 20000000 <= date_val <= 20261231:
            print(f'    -> 看起来像日期: {date_val}')

        # 接下来 4 字节 (可能是 tradenum)
        tradenum = struct.unpack('<f', data[16:20])[0]
        print(f'  [16:20] float: {tradenum:.1f}')

        # 接下来 12 个 float (48 bytes)
        if len(data) >= 68:
            floats = struct.unpack('<12f', data[20:68])
            print(f'  [20:68] 12 floats:')
            for i in range(0, 12, 4):
                print(f'    f{i:2d}-f{i+3:2d}: {floats[i:i+4]}')

        # 继续读取更多数据，看看是否是连续记录
        print(f'\n尝试读取连续记录 (假设每条 52 字节 = 4+4+48):')
        rec_size = 52
        for i in range(5):
            offset = 12 + i * rec_size
            if offset + rec_size > len(data):
                break

            date = struct.unpack('<I', data[offset:offset+4])[0]
            tradenum = struct.unpack('<f', data[offset+4:offset+8])[0]
            floats = struct.unpack('<12f', data[offset+8:offset+52])

            print(f'  Record {i} @ offset {offset}:')
            print(f'    date={date}, tradenum={tradenum:.1f}')
            print(f'    floats[0:4]={floats[0:4]}')

# 验证两个文件
verify_structure(
    r'E:\02Learn\09\TradingAgents-CN\zstock\data_management\script\tdx_capital_flow\shexday.pkg',
    86012
)

verify_structure(
    r'E:\02Learn\09\TradingAgents-CN\zstock\data_management\script\tdx_capital_flow\szexday.pkg',
    73724
)
