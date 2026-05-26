"""
股票处理-通用工具类
"""

import re
from datetime import datetime
import asyncio
import uuid
import json
import logging
import time
import threading
from datetime import datetime
from typing import Dict, Any, Optional, List
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

def is_main_board_stock(code: str) -> bool:
    """判断股票是否属于主板（排除科创板、创业板、北交所）

    主板代码规则：
    - 上交所主板：600/601/603/605 开头
    - 深交所主板：000/001/002/003 开头
    排除：
    - 科创板：688 开头
    - 创业板：300/301 开头
    - 北交所：8 开头（830/831/832/833/834/835/836/837/838/839）
    """
    code = str(code).strip()
    if not code or len(code) < 3:
        return False
    # 科创板
    if code.startswith("688"):
        return False
    # 创业板
    if code.startswith("300") or code.startswith("301"):
        return False
    # 北交所（8开头的6位代码）
    if len(code) == 6 and code.startswith("8"):
        return False
    # 主板：沪市 600/601/603/605，深市 000/001/002/003
    main_board_prefixes = ("600", "601", "603", "605", "000", "001", "002", "003")
    return code.startswith(main_board_prefixes)

def make_serializable(obj):
    """将对象转换为可序列化的格式"""
    if isinstance(obj, dict):
        return {k: make_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [make_serializable(v) for v in obj]
    elif isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    else:
        return str(obj)

def extract_json_block(text: str) -> Optional[Dict]:
    """从文本中提取最后一个JSON块并解析。

    兼容以下格式（以及混合嵌套）：
      1. ```json ... ```
      2. <RAW_DATA> ... </RAW_DATA>
      3. <RAW_DATA> ```json ... ``` </RAW_DATA>  （AI同时输出两种标记时）
    取所有候选块中**最后一个**能成功解析的 JSON 对象。
    """
    def _try_parse(block: str) -> Optional[Dict]:
        block = block.strip()
        # 若块内还套了 ```json ... ```，先剥掉
        inner = re.findall(r'```json\s*(.*?)\s*```', block, re.DOTALL)
        if inner:
            block = inner[-1].strip()
        try:
            return json.loads(block)
        except (json.JSONDecodeError, Exception):
            return None

    candidates: list = []
    # 格式1：```json ... ```
    candidates.extend(re.findall(r'```json\s*(.*?)\s*```', text, re.DOTALL))
    # 格式2：<RAW_DATA> ... </RAW_DATA>
    candidates.extend(re.findall(r'<RAW_DATA>\s*(.*?)\s*</RAW_DATA>', text, re.DOTALL))

    last_valid = None
    for block in candidates:
        result = _try_parse(block)
        if result is not None:
            last_valid = result
    if last_valid is not None:
        return last_valid

    logger.debug("extract_json_block: 未找到可解析的JSON块")
    return None

def is_trading_hours() -> bool:
    """判断当前是否为A股交易时段（工作日 9:30-11:30 / 13:00-15:00）"""
    from datetime import time as dtime
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    if now.weekday() >= 5:
        return False
    t = now.time()
    return (dtime(9, 30) <= t <= dtime(11, 30)) or (dtime(13, 0) <= t <= dtime(15, 0))

