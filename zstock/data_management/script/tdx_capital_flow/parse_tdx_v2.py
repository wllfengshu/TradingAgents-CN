"""
通达信 pkg 资金流数据完整解析器 (v2)
支持: shexday / szexday / bjexday, 新旧格式 (num_fields=18 或 19)

===========================================================================
文件结构 (pkg 格式)
===========================================================================
  Header (12B):
    uint32  num_stocks      — 股票/板块总数
    uint32  num_days        — 最大天数 (通常 6000)
    uint32  data_offset     — 索引区结束偏移

  Index (3072B/条, 从 offset 3068 开始):
    uint32  prev_code       — 前一条代码(链表)
    char[8] stock_code      — ASCII 代码 (null 填充)
    char[8] reserved        — 保留
    uint32  num_days        — 实际天数 (~464-469)
    uint32  num_fields      — 字段数 (18 或 19)
    uint32  field_values[]  — num_fields 个 uint32 (偏移/索引值)
    ... padding to 3072B ...

  Data (3072B/槽位, 从 ~18MB 开始):
    每槽位 = 13天 × 236B + 4B 尾部
    每个槽位包含同一股票/板块连续 13 个交易日的数据

===========================================================================
236B 日线记录字段 (59 个 uint32/float)
===========================================================================
偏移   类型    确认含义                              验证方式
----   ----    --------                              --------
+0     uint32  日期 (YYYYMMDD)                       直接读取
+4     float   成交量 (手) / 主力净流入(板块)          akshare ✓
+8     float   成交额 (元)                            akshare ✓
+12    uint    内部字段1 (非零)
+16    uint    内部字段2 (非零)
+20    uint    内部字段3 (非零)
+24    uint    内部字段4 (非零)
+28    float   涨跌幅 (小数) / 占比(板块)              akshare 近似 ✓
+32    uint    保留
+36    uint    资金流相关 (大uint，可能为double高位)
+40    uint    保留
+44    uint    保留
+48    uint    保留
+52    uint    保留
+56    uint    保留
+60    float   换手率 (小数) / 其他(板块)              akshare 近似 ✓
+64    uint    保留
+68    uint    资金流相关 (类似+36)
+72    uint    保留
+76    float   资金流分项 A (金额)                     个股有效
+80    uint    保留
+84    uint    资金流 (大值)
+88    float   资金流分项 B (金额)                     个股有效
+92    float   资金流分项 C (金额)                     个股有效
+96    float   资金流分项 D (金额)                     个股有效
+100   float   资金流分项 E (金额)                     个股有效
+104   float   价格相关 (可能是均价/昨收)               个股有效
+108   float   价格相关 (可能是高低价)                  个股有效
+112   uint32  股票代码                                直接读取
+116   uint    标记位 (通常=1)

注意: 板块/指数记录的字段含义与个股可能不同,
      +4 对板块可能是主力净流入而非成交量。
===========================================================================
"""
import struct
import csv
import os
import sys
from pathlib import Path
from datetime import datetime


# === 常量 ===
INDEX_ENTRY_SIZE = 3072
INDEX_FIRST_ENTRY = 3068
REC_SIZE = 236
DAYS_PER_SLOT = 13
SLOT_SIZE = 3072  # 13 * 236 + 4


def parse_index(filepath):
    """解析索引表"""
    entries = []
    with open(filepath, 'rb') as f:
        header = f.read(12)
        num_stocks, num_days, data_offset = struct.unpack('<III', header)

        for i in range(num_stocks):
            offset = INDEX_FIRST_ENTRY + i * INDEX_ENTRY_SIZE
            f.seek(offset)
            raw = f.read(INDEX_ENTRY_SIZE)

            prev_code = struct.unpack_from('<I', raw, 0)[0]
            code_bytes = raw[4:12]
            try:
                code = code_bytes[:6].decode('ascii').strip('\x00').strip()
                if code.isdigit():
                    code = code.zfill(6)
                else:
                    continue
            except:
                continue

            nd = struct.unpack_from('<I', raw, 20)[0]
            nf = struct.unpack_from('<I', raw, 24)[0]
            vals = [struct.unpack_from('<I', raw, 28 + j * 4)[0] for j in range(min(nf, 19))]

            entries.append({
                'index': i, 'code': code, 'prev_code': prev_code,
                'num_days': nd, 'num_fields': nf, 'field_values': vals,
            })
    return entries, num_stocks, num_days, data_offset


def find_data_start(filepath, index_end):
    """查找数据区起始位置"""
    fsize = os.path.getsize(filepath)
    with open(filepath, 'rb') as f:
        search_pos = index_end
        while search_pos < fsize - 4:
            f.seek(search_pos)
            chunk = f.read(min(2 * 1024 * 1024, fsize - search_pos))
            # 搜索已知日期
            for dv in [20240801, 20260710, 20260709, 20260701, 20250101, 20240805]:
                idx = chunk.find(struct.pack('<I', dv))
                if idx >= 0:
                    return search_pos + idx
            search_pos += len(chunk) - 4
    return None


def parse_all_data(filepath, data_start, fsize):
    """解析所有数据槽位"""
    num_slots = (fsize - data_start) // SLOT_SIZE
    records = []

    with open(filepath, 'rb') as f:
        for slot_idx in range(num_slots):
            f.seek(data_start + slot_idx * SLOT_SIZE)
            slot_data = f.read(SLOT_SIZE)

            for day in range(DAYS_PER_SLOT):
                off = day * REC_SIZE
                date_val = struct.unpack_from('<I', slot_data, off)[0]
                if not (20000000 <= date_val <= 20301231):
                    continue

                code_raw = struct.unpack_from('<I', slot_data, off + 112)[0]
                code = str(code_raw).zfill(6) if 0 < code_raw < 1000000 else ''

                # 提取关键字段 (float)
                volume = struct.unpack_from('<f', slot_data, off + 4)[0]     # +4: 成交量(手)
                amount = struct.unpack_from('<f', slot_data, off + 8)[0]     # +8: 成交额(元)
                pct_chg = struct.unpack_from('<f', slot_data, off + 28)[0]   # +28: 涨跌幅(小数)
                turnover = struct.unpack_from('<f', slot_data, off + 60)[0]  # +60: 换手率(小数)
                flow_a = struct.unpack_from('<f', slot_data, off + 76)[0]    # +76: 资金流分项A
                flow_b = struct.unpack_from('<f', slot_data, off + 88)[0]    # +88: 资金流分项B
                flow_c = struct.unpack_from('<f', slot_data, off + 92)[0]    # +92: 资金流分项C
                flow_d = struct.unpack_from('<f', slot_data, off + 96)[0]    # +96: 资金流分项D
                flow_e = struct.unpack_from('<f', slot_data, off + 100)[0]   # +100: 资金流分项E
                price_ref1 = struct.unpack_from('<f', slot_data, off + 104)[0]  # +104: 价格参考1
                price_ref2 = struct.unpack_from('<f', slot_data, off + 108)[0]  # +108: 价格参考2

                records.append({
                    'slot': slot_idx,
                    'date': date_val,
                    'code': code,
                    'volume': volume,
                    'amount': amount,
                    'pct_chg': pct_chg,
                    'turnover': turnover,
                    'flow_a': flow_a,
                    'flow_b': flow_b,
                    'flow_c': flow_c,
                    'flow_d': flow_d,
                    'flow_e': flow_e,
                    'price_ref1': price_ref1,
                    'price_ref2': price_ref2,
                })

    return records


def export_csv(records, output_path):
    """导出为CSV"""
    headers = [
        'slot', 'date', 'code',
        'volume',          # +4:  成交量(手) / 板块主力净流入
        'amount',          # +8:  成交额(元)
        'pct_chg',         # +28: 涨跌幅(小数)
        'turnover',        # +60: 换手率(小数)
        'flow_a',          # +76: 资金流分项A
        'flow_b',          # +88: 资金流分项B
        'flow_c',          # +92: 资金流分项C
        'flow_d',          # +96: 资金流分项D
        'flow_e',          # +100: 资金流分项E
        'price_ref1',      # +104: 价格参考1 (均价/昨收)
        'price_ref2',      # +108: 价格参考2 (高/低价)
    ]

    with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for r in records:
            writer.writerow([
                r['slot'], r['date'], r['code'],
                f"{r['volume']:.2f}",
                f"{r['amount']:.2f}",
                f"{r['pct_chg']:.6f}",
                f"{r['turnover']:.6f}",
                f"{r['flow_a']:.2f}",
                f"{r['flow_b']:.2f}",
                f"{r['flow_c']:.2f}",
                f"{r['flow_d']:.2f}",
                f"{r['flow_e']:.2f}",
                f"{r['price_ref1']:.4f}",
                f"{r['price_ref2']:.4f}",
            ])

    return len(records)


def process_pkg(filepath):
    """处理单个 pkg 文件"""
    fname = os.path.basename(filepath)
    fsize = os.path.getsize(filepath)
    print(f"\n{'='*70}")
    print(f"处理: {fname} ({fsize/1024/1024:.1f} MB)")

    # 1. 索引
    entries, num_stocks, num_days, data_offset = parse_index(filepath)
    print(f"  索引: {len(entries)} 条 (header: {num_stocks} stocks, {num_days} days)")

    # 导出索引
    idx_path = filepath.replace('.pkg', '_index_v2.csv')
    with open(idx_path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['index', 'code', 'prev_code', 'num_days', 'num_fields'] +
                   [f'fv_{i}' for i in range(19)])
        for e in entries:
            w.writerow([e['index'], e['code'], e['prev_code'],
                       e['num_days'], e['num_fields']] + e['field_values'])

    # 2. 数据区
    index_end = INDEX_FIRST_ENTRY + num_stocks * INDEX_ENTRY_SIZE
    data_start = find_data_start(filepath, index_end)
    if data_start is None:
        print("  未找到数据区!")
        return

    num_slots = (fsize - data_start) // SLOT_SIZE
    print(f"  数据区: offset={data_start}, {num_slots} 槽位")

    # 3. 解析数据
    print(f"  解析中...")
    records = parse_all_data(filepath, data_start, fsize)
    print(f"  总记录: {len(records)}")

    if not records:
        return

    # 统计
    dates = sorted(set(r['date'] for r in records))
    codes = sorted(set(r['code'] for r in records if r['code']))
    print(f"  日期: {dates[0]} ~ {dates[-1]} ({len(dates)} 天)")
    print(f"  代码: {len(codes)} 个")

    # 4. 导出
    csv_path = filepath.replace('.pkg', '_capital_flow_v2.csv')
    n = export_csv(records, csv_path)
    csv_size = os.path.getsize(csv_path) / 1024 / 1024
    print(f"  导出: {csv_path} ({csv_size:.1f} MB, {n} 条)")

    # 5. 按代码汇总
    code_counts = {}
    for r in records:
        c = r['code']
        if c:
            code_counts[c] = code_counts.get(c, 0) + 1

    # 显示几个示例
    print(f"\n  示例数据 (最新日期):")
    latest = max(r['date'] for r in records)
    latest_recs = [r for r in records if r['date'] == latest and r['code']]
    latest_recs.sort(key=lambda x: x['amount'], reverse=True)
    for r in latest_recs[:10]:
        print(f"    {r['code']} | 量={r['volume']:>12.0f} | 额={r['amount']:>16.0f} | "
              f"涨幅={r['pct_chg']*100:>6.2f}% | 换手={r['turnover']*100:>6.2f}%")


def main():
    base = Path(__file__).parent

    # 处理所有 pkg 文件
    pkgs = sorted(base.glob('*_20260710.pkg'))
    if not pkgs:
        pkgs = sorted(base.glob('*.pkg'))

    for pkg in pkgs:
        process_pkg(str(pkg))


if __name__ == '__main__':
    main()
