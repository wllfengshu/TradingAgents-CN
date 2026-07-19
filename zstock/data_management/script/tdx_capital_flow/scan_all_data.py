"""
扫描整个文件，找到所有非零数据块
"""
import struct

def scan_nonzero_data(file_path: str):
    print(f'\n{"="*60}')
    print(f'Scanning: {file_path}')

    with open(file_path, 'rb') as f:
        file_size = f.seek(0, 2)
        f.seek(0)

        # 读取文件头
        header = f.read(12)
        num_stocks, num_days, data_offset = struct.unpack('<III', header)
        print(f'Header: {num_stocks} stocks, {num_days} days, data_offset={data_offset}')
        print(f'File size: {file_size:,} bytes')

        # 扫描 data_offset 之后的所有非零数据
        print(f'\n扫描 offset {data_offset} 之后的非零数据...')
        f.seek(data_offset)

        nonzero_blocks = []
        chunk_size = 1024 * 1024  # 1MB
        offset = data_offset
        current_block_start = None

        while offset < file_size:
            f.seek(offset)
            chunk = f.read(chunk_size)
            if not chunk:
                break

            # 查找非零字节
            for i, byte in enumerate(chunk):
                if byte != 0:
                    if current_block_start is None:
                        current_block_start = offset + i
                else:
                    if current_block_start is not None:
                        # 块结束
                        block_end = offset + i
                        block_size = block_end - current_block_start
                        nonzero_blocks.append((current_block_start, block_size))
                        current_block_start = None

            offset += len(chunk)

        # 记录最后一个块
        if current_block_start is not None:
            nonzero_blocks.append((current_block_start, file_size - current_block_start))

        print(f'\n找到 {len(nonzero_blocks)} 个非零数据块:')
        for i, (start, size) in enumerate(nonzero_blocks[:20]):  # 只显示前20个
            print(f'  Block {i}: offset {start:,}, size {size:,} bytes')

            # 读取并显示块的前几个字节
            f.seek(start)
            data = f.read(min(64, size))
            print(f'    Data: {data[:32].hex()}')

            # 尝试查找股票代码
            for j in range(len(data) - 6):
                try:
                    code = data[j:j+6].decode('ascii', errors='ignore')
                    if code.isdigit() and code[0] in ['6', '0', '3', '8']:
                        print(f'    Stock code at offset {start+j}: {code}')
                except:
                    pass

        # 统计
        total_nonzero = sum(size for _, size in nonzero_blocks)
        print(f'\n总非零数据: {total_nonzero:,} bytes ({total_nonzero/file_size*100:.2f}%)')
        print(f'零数据: {file_size - total_nonzero:,} bytes ({(file_size-total_nonzero)/file_size*100:.2f}%)')

# 扫描两个文件
scan_nonzero_data(r'E:\02Learn\09\TradingAgents-CN\zstock\data_management\script\tdx_capital_flow\shexday.pkg')
scan_nonzero_data(r'E:\02Learn\09\TradingAgents-CN\zstock\data_management\script\tdx_capital_flow\szexday.pkg')
