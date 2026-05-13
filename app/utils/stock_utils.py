"""
股票处理-通用工具类
"""

import os
from typing import Optional


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
    code = str(code).strip().lstrip("0") if code else ""
    # 重新用原始值判断更稳妥，直接用前缀匹配
    code = str(code).strip()
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
