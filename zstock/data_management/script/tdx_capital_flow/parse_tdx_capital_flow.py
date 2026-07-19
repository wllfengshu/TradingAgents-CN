"""
通达信 pkg 资金流数据解析器
文件结构:
  - 12字节头: num_stocks(uint32), num_days(uint32), data_offset(uint32)
  - 索引表: 从 offset 3068 开始, 每条 3072 字节, 共 num_stocks 条
    - +0: prev_code (uint32, 链表指针)
    - +4: stock_code (8字节 ASCII, null填充)
    - +12: 保留 (8字节)
    - +20: num_days (uint32) = 464
    - +24: num_fields (uint32) = 18
    - +28: 18 x uint32 偏移/数据值
  - 数据区: 从 ~18MB 开始
    - 78032 个板块槽位, 每个 3072 字节
    - 每个槽位: 13天 x 236字节 日线记录 + 4字节尾部
    - 日线记录 236 字节:
      - +0: date (uint32, YYYYMMDD)
      - +4: float (主力净流入?)
      - +8: float (成交额?)
      - +12~+108: 其他字段 (float/uint混合)
      - +112: sector_code (uint32)
      - +116~+232: 更多字段
      - +232: uint32 (尾部标记)
"""
import struct
import csv
import sys
from pathlib import Path


# === 常量 ===
INDEX_ENTRY_SIZE = 3072
INDEX_FIRST_ENTRY = 3068
DATA_REC_SIZE = 236        # 每天每板块的记录大小
DAYS_PER_SLOT = 13         # 每个槽位包含13个交易日
SLOT_SIZE = 3072           # 每个数据槽位大小 = 13 * 236 + 4


def parse_index_entries(filepath: str):
    """解析索引表, 返回所有有效的股票/板块条目"""
    entries = []
    with open(filepath, 'rb') as f:
        # 读文件头
        header = f.read(12)
        num_stocks, num_days, data_offset = struct.unpack('<III', header)

        # 读索引表
        for i in range(num_stocks):
            offset = INDEX_FIRST_ENTRY + i * INDEX_ENTRY_SIZE
            f.seek(offset)
            raw = f.read(INDEX_ENTRY_SIZE)

            prev_code = struct.unpack_from('<I', raw, 0)[0]
            code_bytes = raw[4:12]
            # 提取 ASCII 股票代码
            try:
                code = code_bytes[:6].decode('ascii').strip('\x00').strip()
                if code.isdigit():
                    code = code.zfill(6)  # 补零到6位
            except:
                code = ''

            if not code or not code.isdigit():
                continue

            num_days_entry = struct.unpack_from('<I', raw, 20)[0]
            num_fields = struct.unpack_from('<I', raw, 24)[0]
            field_values = [struct.unpack_from('<I', raw, 28 + j * 4)[0]
                           for j in range(num_fields)]

            entries.append({
                'index': i,
                'offset': offset,
                'prev_code': prev_code,
                'code': code,
                'num_days': num_days_entry,
                'num_fields': num_fields,
                'field_values': field_values,
            })

    return entries, num_stocks, num_days, data_offset


def parse_data_sector(f, sector_offset):
    """解析一个数据槽位 (3072 字节), 返回 13 天的数据"""
    f.seek(sector_offset)
    raw = f.read(SLOT_SIZE)

    records = []
    for day in range(DAYS_PER_SLOT):
        rec_offset = day * DATA_REC_SIZE
        date_val = struct.unpack_from('<I', raw, rec_offset)[0]

        # 验证日期
        if not (20000000 <= date_val <= 20301231):
            continue

        # 提取所有字段 (59 个 uint32 / float)
        uints = []
        floats = []
        for j in range(0, DATA_REC_SIZE, 4):
            u = struct.unpack_from('<I', raw, rec_offset + j)[0]
            fv = struct.unpack_from('<f', raw, rec_offset + j)[0]
            uints.append(u)
            floats.append(fv)

        sector_code_raw = uints[28] if len(uints) > 28 else 0  # +112
        sector_code = str(sector_code_raw).zfill(6) if sector_code_raw > 0 else '0'

        records.append({
            'date': date_val,
            'sector_code': sector_code,
            'uints': uints,
            'floats': floats,
        })

    return records


def parse_data_section(filepath: str, max_sectors=None):
    """解析数据区所有槽位"""
    file_size = Path(filepath).stat().st_size

    with open(filepath, 'rb') as f:
        # 先找到数据区起始位置
        # 数据区从索引结束后 ~几MB 的位置开始
        # 通过扫描找到第一个有效日期
        header = f.read(12)
        num_stocks, num_days, data_offset = struct.unpack('<III', header)

        # 数据区在索引表之后
        index_end = INDEX_FIRST_ENTRY + num_stocks * INDEX_ENTRY_SIZE

        # 扫描找到数据区起始 (第一个有效日期)
        data_start = None
        search_pos = index_end
        while search_pos < file_size - 4:
            f.seek(search_pos)
            chunk = f.read(min(1024 * 1024, file_size - search_pos))
            # 搜索日期模式 20240801 = 0x0134D9A1
            idx = chunk.find(struct.pack('<I', 20240801))
            if idx >= 0:
                data_start = search_pos + idx
                break
            search_pos += len(chunk) - 4

        if data_start is None:
            print("未找到数据区!")
            return []

        # 计算槽位数
        total_data = file_size - data_start
        num_slots = total_data // SLOT_SIZE
        print(f"数据区: offset={data_start}, 大小={total_data} bytes")
        print(f"槽位数: {num_slots}, 每槽 {SLOT_SIZE} 字节")

        if max_sectors:
            num_slots = min(num_slots, max_sectors)

        all_records = []
        for slot_idx in range(num_slots):
            slot_offset = data_start + slot_idx * SLOT_SIZE
            records = parse_data_sector(f, slot_offset)
            for rec in records:
                rec['slot_index'] = slot_idx
            all_records.extend(records)

            if (slot_idx + 1) % 10000 == 0:
                print(f"  已解析 {slot_idx + 1}/{num_slots} 槽位...")

        return all_records


def export_csv(records, output_path, include_all_fields=False):
    """导出为 CSV"""
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
        if include_all_fields:
            # 导出所有 59 个字段
            headers = ['slot_index', 'date', 'sector_code']
            headers += [f'uint_{i}' for i in range(59)]
            headers += [f'float_{i}' for i in range(59)]

            writer = csv.writer(f)
            writer.writerow(headers)

            for rec in records:
                row = [rec['slot_index'], rec['date'], rec['sector_code']]
                row += rec['uints']
                row += [f"{v:.4f}" for v in rec['floats']]
                writer.writerow(row)
        else:
            # 只导出关键字段
            # 基于分析, 关键字段位置:
            # +0: date, +4: float(主力?), +8: float(成交额?),
            # +28: float(涨跌幅?), +60: float, +76: float,
            # +112: sector_code, +144: float, +176: float, +204: uint
            headers = [
                'slot_index', 'date', 'sector_code',
                'field_01_float',   # +4
                'field_02_float',   # +8
                'field_03_uint',    # +12
                'field_04_uint',    # +16
                'field_05_uint',    # +20
                'field_06_uint',    # +24
                'field_07_float',   # +28 (涨跌幅?)
                'field_08_uint',    # +32
                'field_09_uint',    # +36
                'field_10_uint',    # +40
                'field_11_uint',    # +44
                'field_12_uint',    # +48
                'field_13_uint',    # +52
                'field_14_uint',    # +56
                'field_15_float',   # +60
                'field_16_uint',    # +64
                'field_17_uint',    # +68
                'field_18_uint',    # +72
                'field_19_float',   # +76
                'field_20_uint',    # +80
                'field_21_uint',    # +112=sector_code
                'field_22_uint',    # +116
                'field_23_uint',    # +120
                'field_24_uint',    # +124
                'field_25_uint',    # +128
                'field_26_uint',    # +132
                'field_27_uint',    # +136
                'field_28_uint',    # +140
                'field_29_float',   # +144
                'field_30_uint',    # +148
                'field_31_uint',    # +152
                'field_32_uint',    # +156
                'field_33_uint',    # +160
                'field_34_uint',    # +164
                'field_35_uint',    # +168
                'field_36_uint',    # +172
                'field_37_float',   # +176
                'field_38_uint',    # +180
                'field_39_uint',    # +184
                'field_40_uint',    # +188
                'field_41_uint',    # +192
                'field_42_uint',    # +196
                'field_43_uint',    # +200
                'field_44_uint',    # +204
            ]

            writer = csv.writer(f)
            writer.writerow(headers)

            for rec in records:
                u = rec['uints']
                fl = rec['floats']
                row = [
                    rec['slot_index'], rec['date'], rec['sector_code'],
                    f"{fl[1]:.4f}",    # +4
                    f"{fl[2]:.4f}",    # +8
                    u[3],              # +12
                    u[4],              # +16
                    u[5],              # +20
                    u[6],              # +24
                    f"{fl[7]:.4f}",    # +28
                    u[8],              # +32
                    u[9],              # +36
                    u[10],             # +40
                    u[11],             # +44
                    u[12],             # +48
                    u[13],             # +52
                    u[14],             # +56
                    f"{fl[15]:.4f}",   # +60
                    u[16],             # +64
                    u[17],             # +68
                    u[18],             # +72
                    f"{fl[19]:.4f}",   # +76
                    u[20],             # +80
                    u[28],             # +112
                    u[29],             # +116
                    u[30],             # +120
                    u[31],             # +124
                    u[32],             # +128
                    u[33],             # +132
                    u[34],             # +136
                    u[35],             # +140
                    f"{fl[36]:.4f}",   # +144
                    u[37],             # +148
                    u[38],             # +152
                    u[39],             # +156
                    u[40],             # +160
                    u[41],             # +164
                    u[42],             # +168
                    u[43],             # +172
                    f"{fl[44]:.4f}",   # +176
                    u[45],             # +180
                    u[46],             # +184
                    u[47],             # +188
                    u[48],             # +192
                    u[49],             # +196
                    u[50],             # +200
                    u[51],             # +204
                ]
                writer.writerow(row)

    print(f"导出 {len(records)} 条记录到 {output_path}")


def main():
    base_dir = Path(__file__).parent

    for pkg_name in ['shexday.pkg', 'szexday.pkg']:
        pkg_path = base_dir / pkg_name
        if not pkg_path.exists():
            print(f"文件不存在: {pkg_path}")
            continue

        print(f"\n{'='*80}")
        print(f"解析: {pkg_name}")
        print(f"{'='*80}")

        # 1. 解析索引
        print("\n[1/3] 解析索引表...")
        entries, num_stocks, num_days, data_offset = parse_index_entries(str(pkg_path))
        print(f"  文件头: {num_stocks} stocks, {num_days} days, data_offset={data_offset}")
        print(f"  有效条目: {len(entries)}")

        # 导出索引
        idx_csv = base_dir / f"{pkg_name.replace('.pkg', '_index.csv')}"
        with open(idx_csv, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(['index', 'code', 'prev_code', 'num_days', 'num_fields'] +
                          [f'field_{i}' for i in range(18)])
            for e in entries:
                writer.writerow([e['index'], e['code'], e['prev_code'],
                               e['num_days'], e['num_fields']] + e['field_values'])
        print(f"  索引导出到: {idx_csv}")

        # 2. 解析数据区
        print("\n[2/3] 解析数据区...")
        records = parse_data_section(str(pkg_path))
        print(f"  总记录数: {len(records)}")

        # 统计
        if records:
            dates = sorted(set(r['date'] for r in records))
            codes = sorted(set(r['sector_code'] for r in records if r['sector_code'] != '0'))
            print(f"  日期范围: {dates[0]} ~ {dates[-1]} ({len(dates)} 个日期)")
            print(f"  板块/股票数: {len(codes)}")
            print(f"  板块代码示例: {codes[:10]}")

        # 3. 导出
        print("\n[3/3] 导出 CSV...")
        # 精简版
        csv_path = base_dir / f"{pkg_name.replace('.pkg', '_capital_flow.csv')}"
        export_csv(records, str(csv_path), include_all_fields=False)

        # 完整版
        csv_full_path = base_dir / f"{pkg_name.replace('.pkg', '_capital_flow_full.csv')}"
        export_csv(records, str(csv_full_path), include_all_fields=True)


if __name__ == '__main__':
    main()
