# LLM Adapters for TradingAgents
# 使可选依赖的适配器在缺少第三方库时不阻塞其他适配器的使用

from .dashscope_openai_adapter import ChatDashScopeOpenAI

try:
    from .google_openai_adapter import ChatGoogleOpenAI  # 需要 langchain_google_genai
except ImportError:
    ChatGoogleOpenAI = None  # 标记为不可用

from .copilot_adapter import ChatCopilot
from .copilot_business_adapter import ChatCopilotBusiness

__all__ = ["ChatDashScopeOpenAI", "ChatCopilot", "ChatCopilotBusiness"]
if ChatGoogleOpenAI is not None:
    __all__.append("ChatGoogleOpenAI")
