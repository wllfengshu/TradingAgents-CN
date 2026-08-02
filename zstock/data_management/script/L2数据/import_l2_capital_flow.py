"""
导入通达信 L2 资金流历史数据到 zstock_capital_flow

数据来源：通达信导出，GBK 编码 Tab 分隔 .xls（实为文本）
文件命名：全部Ａ股_YYYYMMDD.xls 或 全部Ａ股_YYYYMMDD_序号.xls

字段映射（中文 -> MongoDB）：
  代码 / 名称 / RSI1-3 / 开盘 最高 最低 收盘 / 成交量 成交额
  超B额 超S额 大B额 大S额 中B额 中S额 小B额 小S额
  超B量 超S量 大B量 大S量 中B量 中S量 小B量 小S量
  主力净额 超净 大净 中净 小净

落库约定：
    trade_date 统一为 YYYY-MM-DD（与 zstock_ohlcv / 因子表一致）

用法：
    python import_l2_capital_flow.py                      # 导入全部文件
    python import_l2_capital_flow.py --dry-run            # 仅打印，不写库
    python import_l2_capital_flow.py --start 20260101     # 只导入该日期及之后
    python import_l2_capital_flow.py --end   20260331     # 只导入该日期及之前
"""
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[4]   # TradingAgents-CN/
ZSTOCK_ROOT  = Path(__file__).resolve().parents[3]   # TradingAgents-CN/zstock/
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zstock.common.utils.common_utils import normalize_date, to_yyyymmdd

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

# ─────────────────── 数据路径 ───────────────────
# 通达信导出文件放在 work/ 下（可含子目录，如 xiazai / xiazai2）
DATA_DIR = ZSTOCK_ROOT / 'data_management' / 'script' / 'L2数据' / 'work'

# ─────────────────── 字段映射 ───────────────────
# 中文列名 -> MongoDB 字段名
FIELD_MAP = {
    '代码':     'code',
    '名称':     'name',
    'RSI1':     'rsi1',
    'RSI2':     'rsi2',
    'RSI3':     'rsi3',
    '开盘':     'open',
    '最高':     'high',
    '最低':     'low',
    '收盘':     'close',
    '成交量':   'volume',
    '成交额':   'turnover',
    '超B额':    'xl_buy_amount',
    '超S额':    'xl_sell_amount',
    '大B额':    'l_buy_amount',
    '大S额':    'l_sell_amount',
    '中B额':    'm_buy_amount',
    '中S额':    'm_sell_amount',
    '小B额':    's_buy_amount',
    '小S额':    's_sell_amount',
    '超B量':    'xl_buy_volume',
    '超S量':    'xl_sell_volume',
    '大B量':    'l_buy_volume',
    '大S量':    'l_sell_volume',
    '中B量':    'm_buy_volume',
    '中S量':    'm_sell_volume',
    '小B量':    's_buy_volume',
    '小S量':    's_sell_volume',
    '主力净额': 'main_net',
    '超净':     'xl_net',
    '大净':     'l_net',
    '中净':     'm_net',
    '小净':     's_net',
}

COL_CAPITAL_FLOW = 'zstock_capital_flow'
# 用 period='L2_daily' 区分本脚本导入的 L2 数据与东财实时快照（period='today' 等）
PERIOD = 'L2_daily'

# ─────────────────── 文件解析 ───────────────────

# 匹配文件名中的日期：全部Ａ股_20260105.xls 或 全部Ａ股_20260105_12.xls
_FILENAME_RE = re.compile(r'全部[ＡA]股_(\d{8})(?:_\d+)?\.xls$', re.IGNORECASE)


def parse_filename(fname: str) -> Optional[str]:
    """从文件名提取 YYYYMMDD 日期。"""
    m = _FILENAME_RE.search(fname)
    return m.group(1) if m else None


def _parse_code(raw: str) -> str:
    """把 ='000001' / ="000001" / 000001 统一成纯数字 code。"""
    s = raw.strip().strip("=").strip('"').strip("'")
    return s


def _is_valid_code(code: str) -> bool:
    """仅接受 6 位数字股票代码；跳过文件末尾说明行等脏数据。"""
    return bool(code) and code.isdigit() and len(code) == 6


def _parse_float(s: str) -> float:
    """把空白 / 空字符串解析为 0.0，否则 float。"""
    s = s.strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def read_l2_file(filepath: str) -> Tuple[str, List[Dict]]:
    """
    读取一个 L2 导出文件。

    Returns:
        (trade_date, rows) — trade_date 格式 YYYY-MM-DD
        rows 每行是 dict，字段名已映射为英文
    """
    with open(filepath, "r", encoding="gbk") as fp:
        # 第 1 行：元数据，包含日期
        meta_line = fp.readline()
        # 第 2 行：列名
        header_line = fp.readline()
        # 剩余：数据行
        data_lines = fp.readlines()

    # 从 meta 行提取日期（格式 日期:2026-01-05）
    meta_date_m = re.search(r"日期:(\d{4})-(\d{2})-(\d{2})", meta_line)
    if not meta_date_m:
        raise ValueError(f"无法从 meta 行解析日期: {meta_line!r}")
    trade_date = normalize_date(
        f"{meta_date_m.group(1)}{meta_date_m.group(2)}{meta_date_m.group(3)}"
    )

    # 解析列名
    raw_cols = [c.strip() for c in header_line.rstrip("\r\n").split("\t")]
    # 建立 col_index -> field_name 映射
    col_indices: List[Tuple[int, str]] = []
    for idx, col in enumerate(raw_cols):
        if col in FIELD_MAP:
            col_indices.append((idx, FIELD_MAP[col]))
        # 忽略不在映射中的列（如末尾空列）

    # 解析数据行
    rows = []
    for line in data_lines:
        line = line.rstrip("\r\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        row: Dict = {"trade_date": trade_date, "period": PERIOD}
        for idx, field in col_indices:
            if idx >= len(parts):
                val = 0.0 if field not in ("code", "name") else ""
            else:
                raw = parts[idx]
                if field == "code":
                    val = _parse_code(raw)
                elif field == "name":
                    val = raw.strip()
                else:
                    val = _parse_float(raw)
            row[field] = val
        if _is_valid_code(row.get("code", "")):
            rows.append(row)

    return trade_date, rows


# ─────────────────── 主流程 ───────────────────

def _connect_mongo():
    """同步连接 MongoDB（复用 app.core.config 的配置）。"""
    from pymongo import MongoClient
    from app.core.config import settings

    uri = settings.MONGO_URI
    db_name = settings.MONGO_DB
    client = MongoClient(uri)
    db = client[db_name]
    # ping 验证
    client.admin.command('ping')
    return client, db


def _ensure_indexes(db):
    """为 zstock_capital_flow 建立必要的索引（已存在则跳过）。"""
    col = db[COL_CAPITAL_FLOW]
    existing = {info['name'] for info in col.list_indexes()}

    # (code, trade_date, period) 唯一索引，支持 upsert 去重
    idx_name = 'code_date_period'
    if idx_name not in existing:
        col.create_index(
            [('code', 1), ('trade_date', 1), ('period', 1)],
            unique=True,
            background=True,
            name=idx_name,
        )
        logger.info(f"  创建唯一索引 {idx_name}")
    else:
        logger.info(f"  索引 {idx_name} 已存在")

    # 查询常用组合
    idx2 = 'trade_date_period'
    if idx2 not in existing:
        col.create_index(
            [('trade_date', 1), ('period', 1)],
            background=True,
            name=idx2,
        )
    logger.info("✅ 索引已就绪")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='导入 L2 资金流历史数据')
    parser.add_argument('--dry-run',  action='store_true', help='只解析，不写库')
    parser.add_argument('--force',    action='store_true',
                        help='强制重导已存在的交易日（默认跳过）')
    parser.add_argument('--start',    type=str, default='', metavar='YYYYMMDD',
                        help='只导入 >= 该日期的文件')
    parser.add_argument('--end',      type=str, default='', metavar='YYYYMMDD',
                        help='只导入 <= 该日期的文件')
    args = parser.parse_args()

    if not DATA_DIR.exists():
        logger.error(f"数据目录不存在: {DATA_DIR}")
        sys.exit(1)

    # 递归扫描 work/ 及其子目录（xiazai、xiazai2 等）
    all_xls = sorted(DATA_DIR.rglob('*.xls'))
    logger.info(f"扫描到 {len(all_xls)} 个 .xls 文件，目录: {DATA_DIR}")

    start_key = to_yyyymmdd(args.start) if args.start else ""
    end_key = to_yyyymmdd(args.end) if args.end else ""

    # 按日期过滤；同一日期多文件只保留一份（避免重复导入）
    # key 用 YYYYMMDD 排序/去重，落库用 YYYY-MM-DD
    date_to_path: Dict[str, Path] = {}
    for path in all_xls:
        d = parse_filename(path.name)  # YYYYMMDD
        if not d:
            logger.warning(f"  跳过（文件名无法解析）: {path.relative_to(DATA_DIR)}")
            continue
        if start_key and d < start_key:
            continue
        if end_key and d > end_key:
            continue
        if d in date_to_path:
            logger.warning(
                f"  日期 {d} 重复，跳过: {path.relative_to(DATA_DIR)} "
                f"(已用 {date_to_path[d].relative_to(DATA_DIR)})"
            )
            continue
        date_to_path[d] = path

    file_with_dates = sorted(date_to_path.items(), key=lambda x: x[0])

    if not file_with_dates:
        logger.info("没有需要导入的文件（可能被 start/end 过滤掉了）")
        return

    logger.info(
        f"待导入: {len(file_with_dates)} 个交易日  "
        f"({normalize_date(file_with_dates[0][0])} ~ "
        f"{normalize_date(file_with_dates[-1][0])})"
    )

    db = None
    existing_dates: Set[str] = set()
    if not args.dry_run:
        client, db = _connect_mongo()
        logger.info(f"MongoDB 已连接: {client.address}")
        _ensure_indexes(db)
        if not args.force:
            # 已入库的 L2 日期直接跳过，避免重复写；--force 可强制重导
            existing_dates = {
                normalize_date(d)
                for d in db[COL_CAPITAL_FLOW].distinct(
                    "trade_date", {"period": PERIOD}
                )
            }
            if existing_dates:
                logger.info(f"库中已有 L2_daily {len(existing_dates)} 个交易日，将跳过")
    else:
        logger.info("🔸 dry-run 模式，不写库")

    total_files = len(file_with_dates)
    total_rows = 0
    total_written = 0
    skipped_existing = 0

    for i, (date_key, filepath) in enumerate(file_with_dates, 1):
        trade_date = normalize_date(date_key)
        rel = filepath.relative_to(DATA_DIR)
        if not args.dry_run and trade_date in existing_dates:
            skipped_existing += 1
            if skipped_existing <= 5 or skipped_existing % 50 == 0:
                logger.info(
                    f"  [{i}/{total_files}] {rel}  {trade_date}: 已存在，跳过"
                )
            continue

        try:
            _, rows = read_l2_file(str(filepath))
        except Exception as e:
            logger.error(f"  [{i}/{total_files}] 读取失败 {rel}: {e}")
            continue

        if not rows:
            logger.warning(f"  [{i}/{total_files}] 空数据 {rel}")
            continue

        total_rows += len(rows)

        if args.dry_run:
            logger.info(f"  [{i}/{total_files}] {rel}: {len(rows)} 行  (dry-run)")
            continue

        # upsert 写入（唯一键 code+trade_date+period，防止重复）
        from pymongo import UpdateOne

        now = datetime.now(timezone.utc)
        ops = []
        for row in rows:
            ops.append(
                UpdateOne(
                    {
                        "code": row["code"],
                        "trade_date": trade_date,
                        "period": PERIOD,
                    },
                    {"$set": {**row, "updated_at": now}},
                    upsert=True,
                )
            )
        result = db[COL_CAPITAL_FLOW].bulk_write(ops, ordered=False)
        upserted = result.upserted_count
        modified = result.modified_count
        total_written += upserted + modified
        logger.info(
            f"  [{i}/{total_files}] {rel}  {trade_date}: "
            f"{len(rows)} 行, 新增 {upserted}, 更新 {modified}"
        )

    if args.dry_run:
        logger.info(f"🔸 dry-run 完成，共解析 {total_rows} 行")
    else:
        logger.info(
            f"✅ 导入完成: {total_files} 个交易日, 跳过已有 {skipped_existing}, "
            f"解析 {total_rows} 行, 写库 {total_written} 条"
        )


if __name__ == '__main__':
    main()
