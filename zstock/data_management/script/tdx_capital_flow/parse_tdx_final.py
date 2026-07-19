"""
正确解析通达信 pkg 资金流数据
结构：股票索引 + 数据记录
"""
import struct
import csv
from pathlib import Path


def parse_pkg_correct(file_path: str, output_csv: str):
    print(f'\n{"="*80}')
    print(f'Parsing: {file_path}')

    with open(file_path, 'rb') as f:
        # 读取文件头
        header = f.read(12)
        num_stocks, num_days, data_offset = struct.unpack('<III', header)

        print(f'Header: {num_stocks} stocks, {num_days} days, data_offset={data_offset}')

        # 从 data_offset 开始扫描，寻找股票代码模式
        f.seek(data_offset)
        file_size = f.seek(0, 2)

        # 扫描整个文件，寻找 6 位 ASCII 数字（股票代码）
        print(f'\n扫描股票代码...')
        f.seek(data_offset)

        stock_records = []
        search_chunk_size = 1024 * 1024  # 1MB chunks
        offset = data_offset

        while offset < file_size:
            f.seek(offset)
            chunk = f.read(search_chunk_size)
            if not chunk:
                break

            # 查找股票代码模式
            for i in range(len(chunk) - 8):
                # 查找 6 位 ASCII 数字
                try:
                    code_bytes = chunk[i+2:i+8]  # 跳过前面的 2 字节
                    code_str = code_bytes.decode('ascii', errors='ignore')

                    if code_str.isdigit() and len(code_str) == 6:
                        # 验证是否是股票代码（以 6/0/3 开头）
                        if code_str[0] in ['6', '0', '3']:
                            # 找到股票代码，尝试解析后续数据
                            stock_offset = offset + i

                            # 读取股票代码前后的数据
                            f.seek(stock_offset - 4)
                            pre_data = f.read(4)
                            pre_val = struct.unpack('<I', pre_data)[0]

                            # 读取股票代码后的数据
                            f.seek(stock_offset + 8)  # 跳过前4字节 + 6字节代码 + 2字节padding
                            post_data = f.read(128)

                            # 尝试解析为日期 + 数据
                            if len(post_data) >= 64:
                                # 前 4 字节可能是日期
                                date_val = struct.unpack('<I', post_data[0:4])[0]

                                # 检查日期是否合理 (20200101 ~ 20261231)
                                if 20000000 <= date_val <= 20261231:
                                    # 解析后续数据
                                    # 尝试 12 个 float (48 bytes)
                                    floats = struct.unpack('<12f', post_data[4:52])

                                    record = {
                                        'offset': stock_offset,
                                        'code': code_str,
                                        'pre_val': pre_val,
                                        'date': date_val,
                                        'floats': floats
                                    }
                                    stock_records.append(record)

                                    # 跳过这个股票，继续找下一个
                                    offset = stock_offset + 1000
                                    break
                except:
                    pass

            offset += len(chunk) - 10  # 重叠一点避免遗漏

            # 找到足够多的股票后停止
            if len(stock_records) >= num_stocks:
                break

        print(f'找到 {len(stock_records)} 条股票记录')

        # 输出到 CSV
        if stock_records:
            print(f'\n写入 {output_csv}...')
            with open(output_csv, 'w', newline='', encoding='utf-8-sig') as csvf:
                writer = csv.writer(csvf)
                writer.writerow(['code', 'date', 'offset'] + [f'f{i}' for i in range(12)])

                for rec in stock_records:
                    row = [rec['code'], rec['date'], rec['offset']] + list(rec['floats'])
                    writer.writerow(row)

            print(f'写入 {len(stock_records)} 条记录')

            # 打印前 10 条样本
            print(f'\n前 10 条记录样本:')
            for i, rec in enumerate(stock_records[:10]):
                print(f'{i+1}. {rec["code"]} date={rec["date"]} offset={rec["offset"]}')
                print(f'   floats: {rec["floats"][:6]}')


# 解析上海和深圳文件
parse_pkg_correct(
    r'E:\02Learn\09\TradingAgents-CN\zstock\data_management\script\tdx_capital_flow\shexday.pkg',
    r'E:\02Learn\09\TradingAgents-CN\zstock\data_management\script\tdx_capital_flow\shexday.csv'
)

parse_pkg_correct(
    r'E:\02Learn\09\TradingAgents-CN\zstock\data_management\script\tdx_capital_flow\szexday.pkg',
    r'E:\02Learn\09\TradingAgents-CN\zstock\data_management\script\tdx_capital_flow\szexday.csv'
)
