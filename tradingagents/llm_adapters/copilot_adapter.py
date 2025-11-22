"""
GitHub Copilot 适配器 - Azure AI Inference 端点
通过 GitHub Token 访问 Azure AI Inference 提供的优质大模型

支持的模型：
- gpt-5-mini
- gpt-4.1
- gpt-4o: GPT-4 Optimized ⭐(推荐，默认)
- gpt-4o-mini: GPT-4o Mini (快速且经济)
- gpt-4: GPT-4   ---   不支持
- gpt-3.5-turbo: GPT-3.5 Turbo
- o1-preview: OpenAI O1 Preview (推理模型)  ---   不支持
- o1-mini: OpenAI O1 Mini (快速推理)  ---   不支持

获取 GitHub Token:
1. 安装 GitHub CLI: https://cli.github.com/
2. 运行认证: gh auth login
3. 获取 token: gh auth token
4. 在 .env 文件中配置:
   GITHUB_COPILOT_TOKEN=your_github_token_here
   GITHUB_COPILOT_MODEL=gpt-4o
   GITHUB_COPILOT_ENABLED=true
"""

import os
from typing import Optional
from tradingagents.llm_adapters.openai_compatible_base import OpenAICompatibleBase
from tradingagents.utils.logging_manager import get_logger

logger = get_logger('agents')


class ChatCopilot(OpenAICompatibleBase):
    """
    GitHub Copilot 大模型适配器 - 使用 Azure AI Inference 端点

    继承自 OpenAICompatibleBase，通过覆盖必要的配置实现与 Copilot 的集成
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
            model: 模型名称，默认 gpt-4o
            api_key: GitHub Token (如不提供则从环境变量 GITHUB_COPILOT_TOKEN 读取)
            base_url: API 基础 URL (默认使用 Azure AI Inference)
            temperature: 温度参数 (Azure AI Inference 限制为 1.0)
            max_tokens: 最大 token 数
            timeout: 请求超时时间(秒)
            **kwargs: 其他参数
        """
        # 设置默认 base_url
        if not base_url:
            base_url = "https://models.inference.ai.azure.com"

        # 从环境变量读取 token
        if not api_key:
            api_key = os.getenv("GITHUB_COPILOT_TOKEN")

        if not api_key:
            raise ValueError(
                "未找到 GitHub Copilot Token。\n"
                "请在 .env 文件中设置 GITHUB_COPILOT_TOKEN，或通过参数传入。\n"
                "获取方式: gh auth token (需先安装 GitHub CLI)"
            )

        # Azure AI Inference 限制：所有 Copilot 相关模型 temperature 必须为 1.0
        temperature = 1.0

        # O1 系列模型使用 max_completion_tokens 参数
        if "o1" in model.lower():
            logger.info(f"🎯 [Copilot适配器] {model} 使用 max_completion_tokens 参数")
            kwargs['max_completion_tokens'] = max_tokens or 8000
            max_tokens = None

        logger.info(f"🚀 [Copilot适配器] 初始化 - 模型: {model}, 端点: {base_url}")

        # 调用父类初始化
        super().__init__(
            provider_name="copilot",
            model=model,
            api_key_env_var="GITHUB_COPILOT_TOKEN",
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            **kwargs
        )

        logger.info("✅ [Copilot适配器] 初始化成功")


