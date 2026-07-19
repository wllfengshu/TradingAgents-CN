"""
重新分析通达信 pkg 文件结构
这些可能是索引表，指向实际数据
"""
import struct

def analyze_index(file_path: str, offset: int):
    print(f'\n{"="*60}')
    print(f'Analyzing: {file_path} at offset {offset}')

    with open(file_path, 'rb') as f:
        f.seek(offset)
        data = f.read(256)

    # 解析前几个字段
    print(f'\nHex: {data[:64].hex()}')

    # 尝试不同的解读方式
    print(f'\n作为 uint32 数组:')
    for i in range(0, min(64, len(data)), 4):
        val = struct.unpack('<I', data[i:i+4])[0]
        print(f'  [{i:3d}] = {val:10d} (0x{val:08x})')

    # 查找股票代码
    print(f'\n查找股票代码:')
    for i in range(len(data) - 6):
        try:
            code = data[i:i+6].decode('ascii', errors='ignore')
            if code.isdigit() and code[0] in ['6', '0', '3', '8']:
                print(f'  offset {offset+i}: "{code}"')
        except:
            pass

    # 根据文件头信息推测结构
    with open(file_path, 'rb') as f:
        header = f.read(12)
        num_stocks, num_days, data_offset = struct.unpack('<III', header)

        print(f'\n文件头信息:')
        print(f'  num_stocks = {num_stocks}')
        print(f'  num_days = {num_days}')
        print(f'  data_offset = {data_offset}')

        # 如果这是一个索引表，每个股票可能占固定字节
        # 计算每个股票占多少字节
        index_size = data_offset - 12  # 减去文件头
        if num_stocks > 0:
            bytes_per_stock = index_size / num_stocks
            print(f'\n索引区域: {index_size} bytes / {num_stocks} stocks = {bytes_per_stock:.1f} bytes/stock')

        # 尝试跳转到 data_offset 看看那里是什么
        f.seek(data_offset)
        data_region = f.read(256)
        print(f'\ndata_offset ({data_offset}) 处的数据:')
        print(f'  Hex: {data_region[:64].hex()}')

        # 在 data_region 查找股票代码
        print(f'\ndata_region 中的股票代码:')
        for i in range(len(data_region) - 6):
            try:
                code = data_region[i:i+6].decode('ascii', errors='ignore')
                if code.isdigit() and code[0] in ['6', '0', '3', '8']:
                    print(f'  offset {data_offset+i}: "{code}"')
            except:
                pass

# 分析两个文件
analyze_index(
    r'E:\02Learn\09\TradingAgents-CN\zstock\data_management\script\tdx_capital_flow\shexday.pkg',
    86012
)

analyze_index(
    r'E:\02Learn\09\TradingAgents-CN\zstock\data_management\script\tdx_capital_flow\szexday.pkg',
    73724
)
