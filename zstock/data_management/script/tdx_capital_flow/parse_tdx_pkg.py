"""
解析通达信 .pkg 资金流数据文件
"""
import struct
import csv
from pathlib import Path


def parse_tdx_pkg(file_path: str, output_csv: str = None):
    """
    解析通达信 pkg 文件

    根据分析：
    - Header (12 bytes): num_stocks, num_days, data_offset
    - Index area: 包含股票代码和偏移信息
    - Data area: 实际数据记录
    """

    with open(file_path, 'rb') as f:
        # 读取文件头
        header = f.read(12)
        num_stocks, num_days, data_offset = struct.unpack('<III', header)

        print(f"File: {file_path}")
        print(f"Header: {num_stocks} stocks, {num_days} days, data_offset={data_offset}")

        # 扫描数据区域，寻找股票代码和数据
        f.seek(data_offset)
        file_size = 258588672 if 'shexday' in file_path else (219638784 if 'szexday' in file_path else 18435072)

        records = []
        current_offset = data_offset

        while current_offset < file_size:
            f.seek(current_offset)
            # 读取一个数据块
            chunk = f.read(1024)

            if not chunk:
                break

            # 寻找股票代码模式 (6位数字ASCII)
            for i in range(len(chunk) - 6):
                try:
                    # 检查是否有6位数字
                    code_bytes = chunk[i:i+6]
                    code_str = code_bytes.decode('ascii', errors='ignore')

                    if code_str.isdigit() and len(code_str) == 6:
                        # 找到股票代码，尝试读取后续数据
                        # 前面可能有日期或其他字段
                        if i >= 16:
                            # 尝试读取前面的数据
                            prev_data = chunk[i-16:i]
                            values = struct.unpack('<IIII', prev_data)

                            # 读取后面的数据字段
                            if i + 6 + 80 <= len(chunk):
                                data_fields = chunk[i+6:i+6+80]

                                # 尝试解析为多个整数
                                fields = []
                                for j in range(0, min(80, len(data_fields)), 4):
                                    if j + 4 <= len(data_fields):
                                        val = struct.unpack('<I', data_fields[j:j+4])[0]
                                        fields.append(val)

                                # 检查数据是否看起来合理（不全为0）
                                if any(v != 0 for v in fields[:10]):
                                    record = {
                                        'code': code_str,
                                        'offset': current_offset + i,
                                        'prev_values': values,
                                        'fields': fields[:20]
                                    }
                                    records.append(record)

                                    # 跳过这个记录，继续寻找下一个
                                    current_offset = current_offset + i + 100
                                    break
                except:
                    pass

            current_offset += 512  # 移动512字节继续扫描

        print(f"Found {len(records)} records")

        # 输出到CSV
        if output_csv and records:
            print(f"\nWriting to {output_csv}...")
            with open(output_csv, 'w', newline='', encoding='utf-8-sig') as csvf:
                writer = csv.writer(csvf)
                # 写入表头
                writer.writerow(['code', 'offset'] + [f'field_{i}' for i in range(20)])

                # 写入数据
                for rec in records:
                    row = [rec['code'], rec['offset']] + rec['fields']
                    writer.writerow(row)

            print(f"Written {len(records)} records to CSV")

        # 打印前10条记录样本
        if records:
            print("\nSample records:")
            for i, rec in enumerate(records[:10]):
                print(f"{i+1}. Code: {rec['code']}, Offset: {rec['offset']}")
                print(f"   Fields: {rec['fields'][:10]}")


if __name__ == '__main__':
    # 解析上海交易所数据
    parse_tdx_pkg(
        r'/zstock/data_management/script/tdx_capital_flow/shexday.pkg',
        r'E:\02Learn\09\TradingAgents-CN\zstock\data_management\script\tdx_capital_flow\shexday_sample.csv'
    )
