"""
JSON 压缩工具 - 减少发送给大模型时的 token 消耗

compress_json_for_llm(data) 是主入口，后续可扩展更多压缩策略。
"""
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def compress_json_for_llm(data: Any, *, remove_nulls: bool = True) -> str:
    """
    将数据序列化为紧凑字符串，减少 token 消耗。

    当前策略（从易到难）：
      1. 去掉 indent，使用无空格分隔符 → 去除格式化空白
      2. 去掉 null / None / "N/A" 等无意义字段 → 进一步瘦身
      3. 把 ": " 替换为 " "，去掉键名引号（类 TOML 风格）→ 可选，默认关闭

    参数
    ----
    data          : 任意可 JSON 序列化的对象
    remove_nulls  : 是否递归删除 None / "N/A" 等字段，默认 True

    返回
    ----
    压缩后的字符串
    """
    # 用于对比的原始长度（未压缩、带 indent）
    original = json.dumps(data, ensure_ascii=False, indent=2)
    original_len = len(original)

    if remove_nulls:
        data = _strip_nulls(data)

    # separators=(',', ':') 去掉所有多余空格，比 indent=2 约节省 30-50%
    compact = json.dumps(data, ensure_ascii=False, separators=(',', ':'))

    # 把 ': ' → ' '（此处已无空格，留作手动触发时的扩展示例）
    # 如需进一步压缩可取消下面注释，但会破坏标准 JSON 格式
    # compact = _kv_inline(compact)

    compact_len = len(compact)
    saved_pct = (1 - compact_len / original_len) * 100 if original_len else 0
    logger.debug(
        "🗜️ [json_compressor] 原始=%d字符 → 压缩后=%d字符，节省%.1f%%",
        original_len, compact_len, saved_pct,
    )

    return compact


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

_NULL_VALUES = {None, "N/A", "暂无数据", "--", "nan", "NaN"}


def _is_null(v: Any) -> bool:
    """判断值是否为无意义的空值（安全处理不可哈希类型）。"""
    if v is None or v == "":
        return True
    try:
        return v in _NULL_VALUES
    except TypeError:
        # list / dict 等不可哈希类型，不视为空值
        return False


def _strip_nulls(obj: Any) -> Any:
    """递归移除字典中值为 None / "N/A" 等的键。"""
    if isinstance(obj, dict):
        return {k: _strip_nulls(v) for k, v in obj.items() if not _is_null(v)}
    if isinstance(obj, list):
        return [_strip_nulls(item) for item in obj if not _is_null(item)]
    return obj


def _kv_inline(s: str) -> str:
    """
    把标准 JSON 的  ": "  替换为单个空格，同时去掉键名两侧的引号。
    ⚠️  结果不再是合法 JSON，仅用于纯文本发送给 LLM 的场景。

    示例：
      {"pe":"12.34","pb":"1.56"}  →  pe 12.34 pb 1.56
    """
    s = re.sub(r'[{}\[\]]', '', s)
    s = s.replace('":"', '" "').replace('":', ' ').replace(',"', ' ').replace('"', '')
    return s.strip()
