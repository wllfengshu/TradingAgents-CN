import json
from tradingagents.utils.logging_init import get_logger
logger = get_logger("default")

_CONTENT_LIMIT = 2000


def _trim(text, limit=_CONTENT_LIMIT) -> str:
    """截断字符串，并打印长度"""
    s = str(text) if text is not None else "(空)"
    return s if len(s) <= limit else s[:limit] + f"...(截断，共{len(s)}字)"


def log_llm_input(node_name: str, messages: list, prompt_template=None) -> None:
    """打印 LLM 调用前的消息列表（含渲染后的 system prompt）"""
    logger.debug(f"━━━ [{node_name}] LLM INPUT ━━━  共 {len(messages)} 条消息")
    for i, msg in enumerate(messages):
        role = getattr(msg, "type", type(msg).__name__)
        content = getattr(msg, "content", str(msg))
        tool_calls = getattr(msg, "tool_calls", None)
        logger.debug(f"  [{i}] role={role}  内容: {_trim(content)}")
        if tool_calls:
            for tc in tool_calls:
                logger.debug(f"  [{i}]   tool_call: name={tc.get('name')}  args={_trim(tc.get('args'))}")


def log_llm_output(node_name: str, result) -> None:
    """打印 LLM 返回结果（content + tool_calls）"""
    content = getattr(result, "content", None)
    tool_calls = getattr(result, "tool_calls", [])
    logger.debug(f"━━━ [{node_name}] LLM OUTPUT ━━━")
    logger.debug(f"  content: {_trim(content)}")
    if tool_calls:
        for tc in tool_calls:
            args_str = _trim(json.dumps(tc.get("args", {}), ensure_ascii=False))
            logger.debug(f"  tool_call: name={tc.get('name')}  args={args_str}")
    else:
        logger.debug(f"  tool_calls: (无)")


def log_tool_input(tool_name: str, **kwargs) -> None:
    """打印工具调用入参"""
    logger.debug(f"━━━ [工具] {tool_name} INPUT ━━━")
    for k, v in kwargs.items():
        logger.debug(f"  {k}: {_trim(v)}")


def log_tool_output(tool_name: str, result: str) -> None:
    """打印工具调用返回值"""
    logger.debug(f"━━━ [工具] {tool_name} OUTPUT ━━━  长度={len(result)}")
    logger.debug(f"  {_trim(result)}")
