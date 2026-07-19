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

def to_text_content(content) -> str:
    """ 将各种格式的内容转换为纯文本字符串，兼容字符串、列表和字典等常见格式。 """
    if content is None:
        return ""

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                # Compatible with common segmented payloads from LLM providers.
                if item.get("type") == "text" and item.get("text"):
                    parts.append(str(item.get("text")))
                elif item.get("content"):
                    parts.append(str(item.get("content")))
            else:
                parts.append(str(item))

        return "\n".join([part for part in parts if part]).strip()

    return str(content)


def codes_for_log(stocks) -> list:
    """从股票列表中提取代码，兼容字符串和字典格式，方便日志输出。"""
    codes = []
    for stock in stocks or []:
        if isinstance(stock, dict):
            codes.append(stock.get("code", ""))
        else:
            codes.append(str(stock))
    return codes


def extract_json_block(text: str) -> Optional[Dict]:
    """从文本中提取最后一个可解析的 JSON 对象。

    兼容格式：
    1) ```json ... ```
    2) <RAW_DATA> ... </RAW_DATA>
    3) 直接输出原始 JSON
    """

    def _try_parse(block: str) -> Optional[Dict]:
        block = (block or "").strip()
        if not block:
            return None
        try:
            parsed = json.loads(block)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    text = (text or "").strip()
    if not text:
        return None

    candidates = []
    candidates.extend(re.findall(r'```json\s*(.*?)\s*```', text, re.DOTALL))
    candidates.extend(re.findall(r'<RAW_DATA>\s*(.*?)\s*</RAW_DATA>', text, re.DOTALL))
    # 回退：整个文本可能就是 JSON。
    candidates.append(text)

    for block in reversed(candidates):
        parsed = _try_parse(block)
        if parsed is not None:
            return parsed

    logger.error("extract_json_block: 错误：数据解析失败！")
    return None
