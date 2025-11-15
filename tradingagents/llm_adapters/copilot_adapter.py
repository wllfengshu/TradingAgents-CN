"""
GitHub Copilot 适配器
支持通过 GitHub Token 访问多种优质大模型

🔹 方案1: Azure AI Inference 端点（标准模型，备用方案）:
- gpt-4o: GPT-4 Optimized ⭐ (推荐，默认)
- gpt-4.1: GPT-4.1
- gpt-4o-mini: GPT-4o Mini (快速且经济)
- gpt-4: GPT-4
- gpt-3.5-turbo: GPT-3.5 Turbo

🔸 方案2: GitHub Copilot Chat API 端点（模拟IDE插件，支持全模型）:
- gpt-5: OpenAI GPT-5 ⭐ (最新旗舰模型)
- gpt-4o: GPT-4 Optimized
- claude-sonnet-4.5: Claude Sonnet 4.5 ⭐ (Anthropic最新模型)
- claude-3.5-sonnet: Claude 3.5 Sonnet
- o1-preview: OpenAI O1 Preview (推理模型)
- o1-mini: OpenAI O1 Mini (快速推理)

🎯 智能端点选择策略：
1. Claude 模型 → 尝试使用 Copilot Chat API (方案2，⚠️ 需要特殊token)
2. GPT-5 系列 → 尝试使用 Copilot Chat API (方案2，⚠️ 需要特殊token)
3. O1 系列 → 尝试使用 Copilot Chat API (方案2，⚠️ 需要特殊token)
4. 其他模型 → 使用 Azure AI Inference (方案1，✅ 推荐)

⚠️ 重要提示：
- 方案2需要从IDE插件提取Copilot session token，普通GitHub token会得到403错误
- 推荐使用方案1 (gpt-4o, gpt-5等模型)，配置简单且稳定
- 详细说明请参考: docs/COPILOT_CLAUDE_SUPPORT.md

获取 GitHub Token (方案1):
1. 安装 GitHub CLI: https://cli.github.com/
2. 运行认证: gh auth login
3. 获取 token: gh auth token
4. 设置环境变量: GITHUB_COPILOT_TOKEN=your_token

推荐使用方法（方案1）:
```python
from tradingagents.llm_adapters.copilot_adapter import ChatCopilot

# 推荐：使用 GPT-4o (方案1)
llm = ChatCopilot(model="gpt-4o", temperature=0.7)
response = llm.invoke("你好")
```

配置文件示例:
1. 在 .env 文件中配置:
   GITHUB_COPILOT_TOKEN=ghp_your_github_token_here
   GITHUB_COPILOT_MODEL=gpt-4o
   GITHUB_COPILOT_ENABLED=true

2. 在 config/models.json 中添加配置:
   {
     "provider": "copilot",
     "model_name": "gpt-4o",
     "api_key": "",
     "base_url": null,
     "max_tokens": 8000,
     "temperature": 0.7,
     "enabled": true
   }
"""

import os
import uuid
from typing import Optional, Dict, Any
from tradingagents.llm_adapters.openai_compatible_base import OpenAICompatibleBase

# 导入日志模块
from tradingagents.utils.logging_manager import get_logger
logger = get_logger('agents')


class ChatCopilot(OpenAICompatibleBase):
    """
    GitHub Copilot 大模型适配器

    通过 GitHub Copilot API 访问多种优质模型，包括:
    - GPT-4o, GPT-5 (推荐)
    - Claude Sonnet 4.5, Claude 3.5 Sonnet ⭐
    - O1 系列等

    双方案策略：
    - 方案1（备用）: Azure AI Inference - 适用于标准GPT模型
    - 方案2（主要）: Copilot Chat API - 模拟IDE插件，支持Claude等全模型
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 1.0,
        max_tokens: Optional[int] = 8000,
        timeout: Optional[int] = 120,
        **kwargs
    ):
        """
        初始化 GitHub Copilot 适配器

        Args:
            model: 模型名称 (支持: gpt-4o, gpt-5, claude-sonnet-4.5, claude-3.5-sonnet, o1-preview等)
            api_key: GitHub Token (如果不提供则从环境变量 GITHUB_COPILOT_TOKEN 获取)
            base_url: API 基础 URL (None=自动选择)
            temperature: 温度参数
            max_tokens: 最大 token 数
            timeout: 请求超时时间(秒)
            **kwargs: 其他参数
        """

        # 🎯 智能端点选择策略（双方案）
        # 方案1: Azure AI Inference (备用，向后兼容)
        # 方案2: Copilot Chat API (主要，模拟IDE插件)
        use_chat_api = False  # 标记是否使用Chat API

        if not base_url:
            # 需要使用 Copilot Chat API 的模型（方案2）
            chat_api_models = [
                "claude",           # 所有Claude模型
                "gpt-5",           # GPT-5系列
                "o1-preview",      # O1系列
                "o1-mini"
            ]

            # 检查模型是否需要使用 Chat API
            if any(m in model.lower() for m in chat_api_models):
                # 使用方案2: GitHub Copilot Chat API (模拟IDE插件)
                # 注意：只使用基础URL，OpenAI SDK会自动添加 /chat/completions
                base_url = "https://api.githubcopilot.com"
                use_chat_api = True
                logger.info(f"🎯 [Copilot适配器] 检测到 {model}，使用方案2: Copilot Chat API")
                logger.info(f"🔧 [Copilot适配器] 模拟IDE插件模式，支持Claude等全模型")
            else:
                # 使用方案1: Azure AI Inference (备用，向后兼容)
                base_url = "https://models.inference.ai.azure.com"
                logger.info(f"🔧 [Copilot适配器] 使用方案1: Azure AI Inference (备用)")

        logger.info(f"🚀 [Copilot适配器] 初始化 GitHub Copilot 适配器")
        logger.info(f"📦 [Copilot适配器] 模型: {model}")
        logger.info(f"🌐 [Copilot适配器] API地址: {base_url}")

        # Azure AI Inference 的限制：某些模型只支持 temperature=1
        # 仅在方案1下应用此限制
        if "azure.com" in base_url.lower():
            if temperature != 1.0:
                logger.warning(f"⚠️  [Copilot适配器] 方案1限制: temperature 从 {temperature} 重置为 1.0")
                temperature = 1.0

        # GPT-5 系列和 O1 系列的特殊处理
        # 这些模型使用 max_completion_tokens 而不是 max_tokens
        init_kwargs = kwargs.copy()
        if "gpt-5" in model.lower() or "o1" in model.lower():
            logger.info(f"🔧 [Copilot适配器] {model} 使用 max_completion_tokens 参数")
            max_tokens = None
            if 'model_kwargs' not in init_kwargs:
                init_kwargs['model_kwargs'] = {}
            init_kwargs['model_kwargs']['max_completion_tokens'] = 8000

        # 🎨 如果使用 Chat API (方案2)，添加IDE插件模拟headers
        if use_chat_api or "api.githubcopilot.com" in base_url:
            logger.info(f"🎭 [Copilot适配器] 启用IDE插件模拟模式")

            # 模拟 JetBrains IDE 的 Copilot 插件
            # 通过 model_kwargs 传递额外的请求参数
            if 'model_kwargs' not in init_kwargs:
                init_kwargs['model_kwargs'] = {}

            # 生成唯一的会话ID
            session_id = str(uuid.uuid4())
            machine_id = str(uuid.uuid4())

            # 注意：OpenAI SDK 会自动处理某些headers
            # 我们通过 default_headers 参数传递自定义headers
            ide_headers = {
                "Editor-Version": "PyCharm/2024.2",
                "Editor-Plugin-Version": "copilot-intellij/1.5.0",
                "OpenAI-Intent": "conversation-panel",
                "VScode-SessionId": session_id,
                "VScode-MachineId": machine_id,
                "User-Agent": "GithubCopilot/1.5.0",
            }

            # LangChain的ChatOpenAI支持default_headers参数
            init_kwargs['default_headers'] = ide_headers
            logger.info(f"✅ [Copilot适配器] IDE headers已配置: {list(ide_headers.keys())}")

        # 调用父类初始化
        try:
            super().__init__(
                provider_name="copilot",
                model=model,
                api_key_env_var="GITHUB_COPILOT_TOKEN",
                base_url=base_url,
                api_key=api_key,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                **init_kwargs
            )
            logger.info(f"✅ [Copilot适配器] GitHub Copilot 适配器初始化成功")

            # 如果使用 Chat API，给出重要提示
            if use_chat_api:
                logger.warning(f"⚠️  [Copilot适配器] 注意: {model} 使用方案2 (Copilot Chat API)")
                logger.warning(f"⚠️  [Copilot适配器] 此方案需要 Copilot session token，不能使用普通 GitHub token")
                logger.warning(f"⚠️  [Copilot适配器] 如遇到 403 错误，建议改用 gpt-4o 等方案1支持的模型")
                logger.warning(f"⚠️  [Copilot适配器] 详见文档: docs/COPILOT_CLAUDE_SUPPORT.md")

        except Exception as e:
            if use_chat_api and "403" in str(e):
                logger.error(f"❌ [Copilot适配器] 方案2认证失败 (403 Forbidden)")
                logger.error(f"💡 [Copilot适配器] 解决方案:")
                logger.error(f"   1. 推荐: 改用 gpt-4o 模型 (方案1，只需 GitHub token)")
                logger.error(f"   2. 或者: 从 IDE 插件中提取 Copilot session token")
                logger.error(f"   3. 或者: 直接使用 Anthropic API 访问 Claude")
                logger.error(f"   详见: docs/COPILOT_CLAUDE_SUPPORT.md")
            raise


def create_copilot_llm(
    model: str = "gpt-4o",
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    temperature: float = 1.0,  # Azure AI Inference 默认值
    max_tokens: int = 8000,
    timeout: int = 120,
    **kwargs
) -> ChatCopilot:
    """
    快速创建 GitHub Copilot LLM 实例

    Args:
        model: 模型名称
        api_key: GitHub Token
        base_url: API 基础 URL
        temperature: 温度参数
        max_tokens: 最大 token 数
        timeout: 超时时间
        **kwargs: 其他参数

    Returns:
        ChatCopilot 实例

    Example:
        >>> llm = create_copilot_llm(model="gpt-4o")
        >>> response = llm.invoke("你好，请介绍一下你自己")
    """
    return ChatCopilot(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        **kwargs
    )


# 支持的模型列表
COPILOT_MODELS = [
    # 🎯 方案2: Copilot Chat API (模拟IDE插件，支持全模型)
    "claude-sonnet-4.5",     # Claude Sonnet 4.5 (Anthropic最新) ⭐⭐⭐
    "claude-3.5-sonnet",     # Claude 3.5 Sonnet ⭐⭐
    "gpt-5",                 # GPT-5 (最新旗舰模型) ⭐⭐⭐
    "gpt-5-mini",            # GPT-5 Mini (轻量快速)
    "o1-preview",            # O1 Preview (推理模型) ⭐⭐
    "o1-mini",               # O1 Mini (快速推理)

    # 🔧 方案1: Azure AI Inference (备用，向后兼容)
    "gpt-4o",                # GPT-4 Optimized ⭐⭐
    "gpt-4o-mini",           # GPT-4o Mini (快速且经济) ⭐
    "gpt-4.1",               # GPT-4.1
    "gpt-4",                 # GPT-4
    "gpt-3.5-turbo",         # GPT-3.5 Turbo
]


def get_copilot_models():
    """获取支持的 Copilot 模型列表"""
    return COPILOT_MODELS


if __name__ == "__main__":
    # 测试代码
    print("🧪 测试 GitHub Copilot 适配器")

    # 检查环境变量
    token = os.getenv("GITHUB_COPILOT_TOKEN")
    if not token:
        print("❌ 错误: 未设置 GITHUB_COPILOT_TOKEN 环境变量")
        print("💡 提示: 运行 'gh auth token' 获取 GitHub Token")
        exit(1)

    print(f"✅ 已检测到 GitHub Token (长度: {len(token)})")

    # 创建 LLM 实例
    try:
        llm = create_copilot_llm(model="gpt-4o")
        print("✅ Copilot LLM 实例创建成功")

        # 测试调用
        print("\n🔄 测试调用...")
        response = llm.invoke("你好，请用一句话介绍你自己")
        print(f"✅ 调用成功: {response.content}")

    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()

