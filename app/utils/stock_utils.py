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
    """从文本中提取最后一个```json代码块并解析"""
    try:
        matches = re.findall(r'```json\s*(.*?)\s*```', text, re.DOTALL)
        if matches:
            return json.loads(matches[-1])
    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"解析JSON代码块失败: {e}")
    return None

def is_trading_hours(self) -> bool:
    """判断当前是否为A股交易时段（工作日 9:30-11:30 / 13:00-15:00）"""
    from datetime import time as dtime
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    if now.weekday() >= 5:
        return False
    t = now.time()
    return (dtime(9, 30) <= t <= dtime(11, 30)) or (dtime(13, 0) <= t <= dtime(15, 0))

