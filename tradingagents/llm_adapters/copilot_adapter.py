"""
GitHub Copilot 适配器
支持通过 GitHub Token 访问多种优质大模型

🔵 方案1: Azure AI Inference 端点（标准模型，备用方案）
- gpt-4o: GPT-4 Optimized ⭐(推荐，默认)
- gpt-4.1: GPT-4.1
- gpt-4o-mini: GPT-4o Mini (快速且经济)
- gpt-4: GPT-4
- gpt-3.5-turbo: GPT-3.5 Turbo

🟣 方案2: GitHub Copilot Chat API 端点（模拟IDE插件，支持全模型）
- gpt-5: OpenAI GPT-5 ⭐(最新旗舰模型)
- gpt-4o: GPT-4 Optimized
- claude-sonnet-4.5: Claude Sonnet 4.5 ⭐(Anthropic最新模型)
- claude-3.5-sonnet: Claude 3.5 Sonnet
- o1-preview: OpenAI O1 Preview (推理模型)
- o1-mini: OpenAI O1 Mini (快速推理)

🔄 智能端点选择策略：
1. Claude 模型 → 尝试使用 Copilot Chat API (方案2)
2. GPT-5 系列 → 尝试使用 Copilot Chat API (方案2)
3. O1 系列 → 尝试使用 Copilot Chat API (方案2)
4. 其他模型 → 使用 Azure AI Inference (方案1，✅ 推荐)

⚠️ 重要提示：
- 方案2需要从IDE插件提取Copilot session token，普通GitHub token会得到403错误
- 推荐使用方案1 (gpt-4o等模型)，配置简单且稳定


获取 GitHub Token (方案1):
1. 安装 GitHub CLI: https://cli.github.com/
2. 运行认证: gh auth login
3. 获取 token: gh auth token
4. 设置环境变量: GITHUB_COPILOT_TOKEN=your_token

获取 Copilot Session Token (方案2):
1. 浏览器打开：https://github.com/copilot/c/d0d67bfa-ce78-4a3c-b7b8-7f803eaec472
2. 按 F12 打开开发者工具，切换到 Network 标签
3. 在搜索框输入 "messages"
4. 选择任意请求，查看 Headers → Request Headers
5. 找到 Authorization 字段，例如：GitHub-Bearer sT3hfPHjgnlTjmd5ma8n1cijNusQggq8BHDMrqx6XVTNlHKXWbXfxImbXnlfdc-1h1Y1BZ32JdR6DJ_-wo8cMfcEskRDm4sLRU56XA2qEjQ=
6. 环境变量配置:GITHUB_COPILOT_SESSION_TOKEN=your_copilot_session_token_here

配置文件示例:
1. 在.env 文件中配置:
   GITHUB_COPILOT_TOKEN=ghp_your_github_token_here
   GITHUB_COPILOT_MODEL=gpt-4o
   GITHUB_COPILOT_ENABLED=true

"""

import os
import uuid
import json
import httpx
from typing import Optional, Dict, Any, List, Iterator
from tradingagents.llm_adapters.openai_compatible_base import OpenAICompatibleBase
from langchain_core.messages import BaseMessage, AIMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from openai import OpenAI

# 导入日志模块
from tradingagents.utils.logging_manager import get_logger
logger = get_logger('agents')


class CopilotHTTPClient(httpx.Client):
    """自定义 HTTP 客户端，支持 GitHub-Bearer 认证格式"""

    def __init__(self, *args, **kwargs):
        self.use_github_bearer = kwargs.pop('use_github_bearer', False)
        super().__init__(*args, **kwargs)

    def build_request(self, *args, **kwargs):
        request = super().build_request(*args, **kwargs)

        # 如果启用了 GitHub-Bearer，修改 Authorization 头
        if self.use_github_bearer and 'authorization' in request.headers:
            auth_value = request.headers['authorization']
            if auth_value.startswith('Bearer '):
                # 将 "Bearer " 替换为 "GitHub-Bearer "
                token = auth_value[7:]  # 去掉 "Bearer " 前缀
                request.headers['authorization'] = f'GitHub-Bearer {token}'
                logger.debug(f"🔑 [Copilot客户端] 使用 GitHub-Bearer 格式")

        return request


class CopilotAsyncHTTPClient(httpx.AsyncClient):
    """异步版本的自定义 HTTP 客户端"""

    def __init__(self, *args, **kwargs):
        self.use_github_bearer = kwargs.pop('use_github_bearer', False)
        super().__init__(*args, **kwargs)

    def build_request(self, *args, **kwargs):
        request = super().build_request(*args, **kwargs)

        if self.use_github_bearer and 'authorization' in request.headers:
            auth_value = request.headers['authorization']
            if auth_value.startswith('Bearer '):
                token = auth_value[7:]
                request.headers['authorization'] = f'GitHub-Bearer {token}'
                logger.debug(f"🔑 [Copilot客户端] 使用 GitHub-Bearer 格式 (async)")

        return request


def get_copilot_token_from_ide() -> Optional[str]:
    """
    从 IDE 配置文件中提取 Copilot token
    支持 VSCode, JetBrains (IntelliJ, PyCharm), Neovim 等

    Returns:
        Copilot token 或 None
    """
    import platform
    from pathlib import Path
    import glob

    system = platform.system()
    home = Path.home()

    logger.info(f"🔍 [Copilot Token] 开始从 IDE 配置中查找 Copilot token...")
    logger.info(f"🔍 [Copilot Token] 系统: {system}, 用户目录: {home}")

    # 可能的配置文件路径
    config_path_patterns = []

    if system == "Windows":
        # Windows 下的配置路径
        jetbrains_base = home / "AppData" / "Roaming" / "JetBrains"

        config_path_patterns = [
            # JetBrains IDEs 的配置 (优先查找)
            str(jetbrains_base / "PyCharm*" / "github-copilot" / "hosts.json"),
            str(jetbrains_base / "IntelliJIdea*" / "github-copilot" / "hosts.json"),
            str(jetbrains_base / "IdeaIC*" / "github-copilot" / "hosts.json"),
            str(jetbrains_base / "*" / "github-copilot" / "hosts.json"),
            # VSCode 配置
            str(home / "AppData" / "Roaming" / "Code" / "User" / "globalStorage" / "github.copilot" / "versions" / "*.json"),
            str(home / "AppData" / "Roaming" / "github-copilot" / "hosts.json"),
            # 通用位置
            str(home / ".config" / "github-copilot" / "hosts.json"),
        ]
    elif system == "Darwin":  # macOS
        config_path_patterns = [
            # JetBrains
            str(home / "Library" / "Application Support" / "JetBrains" / "PyCharm*" / "github-copilot" / "hosts.json"),
            str(home / "Library" / "Application Support" / "JetBrains" / "IntelliJIdea*" / "github-copilot" / "hosts.json"),
            str(home / "Library" / "Application Support" / "JetBrains" / "*" / "github-copilot" / "hosts.json"),
            # VSCode
            str(home / "Library" / "Application Support" / "Code" / "User" / "globalStorage" / "github.copilot" / "versions" / "*.json"),
            str(home / ".config" / "github-copilot" / "hosts.json"),
        ]
    else:  # Linux
        config_path_patterns = [
            # JetBrains
            str(home / ".config" / "JetBrains" / "PyCharm*" / "github-copilot" / "hosts.json"),
            str(home / ".config" / "JetBrains" / "IntelliJIdea*" / "github-copilot" / "hosts.json"),
            str(home / ".config" / "JetBrains" / "*" / "github-copilot" / "hosts.json"),
            # VSCode
            str(home / ".config" / "Code" / "User" / "globalStorage" / "github.copilot" / "versions" / "*.json"),
            str(home / ".config" / "github-copilot" / "hosts.json"),
        ]

    logger.info(f"🔍 [Copilot Token] 将搜索 {len(config_path_patterns)} 个路径模式")

    # 尝试读取配置文件
    for i, config_path_pattern in enumerate(config_path_patterns, 1):
        logger.debug(f"🔍 [Copilot Token] [{i}/{len(config_path_patterns)}] 搜索: {config_path_pattern}")

        matching_paths = glob.glob(config_path_pattern, recursive=False)

        if matching_paths:
            logger.info(f"✅ [Copilot Token] 找到 {len(matching_paths)} 个匹配的路径")

        for config_path_str in matching_paths:
            config_path = Path(config_path_str)
            logger.debug(f"   📁 检查: {config_path}")

            if not config_path.exists():
                continue

            try:
                # 处理目录情况（VSCode versions 目录）
                if config_path.is_dir():
                    logger.debug(f"   📂 这是一个目录，查找其中的 JSON 文件...")
                    for json_file in config_path.glob("*.json"):
                        logger.debug(f"      📄 读取: {json_file.name}")
                        try:
                            with open(json_file, 'r', encoding='utf-8') as f:
                                data = json.load(f)
                                # VSCode 格式
                                if isinstance(data, dict):
                                    token = data.get("token") or data.get("oauth_token")
                                    if token and len(token) > 20:
                                        logger.info(f"✅ [Copilot Token] 从 VSCode 配置中提取到 token!")
                                        logger.info(f"   📁 文件: {json_file}")
                                        logger.info(f"   🔑 Token 长度: {len(token)}, 前10位: {token[:10]}...")
                                        return token
                        except Exception as e:
                            logger.debug(f"      ❌ 读取失败: {e}")
                else:
                    # 处理单个 JSON 文件 (JetBrains hosts.json)
                    logger.debug(f"   📄 读取 JSON 文件: {config_path.name}")
                    try:
                        with open(config_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                            logger.debug(f"      📋 JSON 包含的键: {list(data.keys())}")

                            # JetBrains hosts.json 格式
                            if "github.com" in data:
                                github_data = data["github.com"]
                                logger.debug(f"      📋 github.com 数据键: {list(github_data.keys())}")

                                token = github_data.get("oauth_token") or github_data.get("token")
                                if token and len(token) > 20:
                                    logger.info(f"✅ [Copilot Token] 从 JetBrains IDE 配置中提取到 token!")
                                    logger.info(f"   📁 文件: {config_path}")
                                    logger.info(f"   🔑 Token 长度: {len(token)}, 前10位: {token[:10]}...")
                                    return token
                                else:
                                    logger.debug(f"      ⚠️  找到 github.com 但 token 无效或太短")
                            else:
                                logger.debug(f"      ⚠️  JSON 中没有 github.com 键")
                    except json.JSONDecodeError as e:
                        logger.debug(f"      ❌ JSON 解析失败: {e}")
                    except Exception as e:
                        logger.debug(f"      ❌ 读取失败: {e}")

            except Exception as e:
                logger.debug(f"   ❌ 处理路径失败: {e}")
                continue

    logger.warning("⚠️ [Copilot Token] 未能从 IDE 配置中找到 Copilot token")
    logger.info("💡 [Copilot Token] 提示:")
    logger.info("   1. 确保 IDE 已安装 GitHub Copilot 插件")
    logger.info("   2. 确保在 IDE 中已登录 GitHub Copilot")
    logger.info("   3. 或使用 .env 文件中的 GITHUB_COPILOT_TOKEN")
    return None


class ChatCopilot(OpenAICompatibleBase):
    """
    GitHub Copilot 大模型适配器

    通过 GitHub Copilot API 访问多种优质模型，包括：
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
        use_chat_api: bool = False,  # 强制使用 Chat API
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
            use_chat_api: 强制使用 Copilot Chat API (方案2)
            **kwargs: 其他参数
        """

        # 🔄 智能端点选择策略（双方案）
        # 方案1: Azure AI Inference (备用，向后兼容)
        # 方案2: Copilot Chat API (主要，模拟IDE插件)

        if not base_url:
            # 需要使用 Copilot Chat API 的模型（方案2）
            chat_api_models = [
                "claude",           # 所有Claude模型
                "gpt-5",           # GPT-5系列
                "o1-preview",      # O1系列
                "o1-mini"
            ]

            # 检查模型是否需要使用 Chat API
            if use_chat_api or any(m in model.lower() for m in chat_api_models):
                # 使用方案2: GitHub Copilot Chat API (模拟IDE插件)
                # 注意：只使用基础URL，OpenAI SDK会自动添加 /chat/completions
                base_url = "https://api.githubcopilot.com"
                use_chat_api = True
                logger.info(f"🔄 [Copilot适配器] 检测到 {model}，使用方案2: Copilot Chat API")
                logger.info(f"🎭 [Copilot适配器] 模拟IDE插件模式，支持Claude等全模型")
            else:
                # 使用方案1: Azure AI Inference (备用，向后兼容)
                base_url = "https://models.inference.ai.azure.com"
                logger.info(f"🔵 [Copilot适配器] 使用方案1: Azure AI Inference (备用)")

        logger.info(f"🚀 [Copilot适配器] 初始化 GitHub Copilot 适配器")
        logger.info(f"🎯 [Copilot适配器] 模型: {model}")
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
            logger.info(f"🎯 [Copilot适配器] {model} 使用 max_completion_tokens 参数")
            max_tokens = None
            if 'model_kwargs' not in init_kwargs:
                init_kwargs['model_kwargs'] = {}
            init_kwargs['model_kwargs']['max_completion_tokens'] = 8000

        # 🎭 如果使用 Chat API (方案2)，添加IDE插件模拟headers
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
                # VSCode Copilot 插件标识
                "Editor-Version": "vscode/1.95.0",
                "Editor-Plugin-Version": "copilot/1.250.0",
                "Openai-Organization": "github-copilot",
                "Openai-Intent": "conversation-panel",
                "VScode-SessionId": session_id,
                "VScode-MachineId": machine_id,
                # JetBrains IDE 标识（备用）
                "X-GitHub-Api-Version": "2023-07-07",
                "User-Agent": "GithubCopilot/1.250.0",
            }

            # LangChain的ChatOpenAI支持default_headers参数
            init_kwargs['default_headers'] = ide_headers
            logger.info(f"✅ [Copilot适配器] IDE headers已配置: {list(ide_headers.keys())}")

            # 🔑 从环境变量读取 Session Token
            if not api_key:
                session_token = os.getenv("GITHUB_COPILOT_SESSION_TOKEN")
                if session_token:
                    api_key = session_token
                    logger.info("✅ [Copilot适配器] 使用 .env 文件中的 Session Token")

            # 🔧 使用自定义 HTTP 客户端支持 GitHub-Bearer 格式
            # 创建支持 GitHub-Bearer 的 HTTP 客户端
            import ssl
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            http_client = CopilotHTTPClient(
                use_github_bearer=True,
                verify=False,  # 禁用 SSL 验证（公司网络环境）
                timeout=timeout
            )

            async_http_client = CopilotAsyncHTTPClient(
                use_github_bearer=True,
                verify=False,
                timeout=timeout
            )

            init_kwargs['http_client'] = http_client
            init_kwargs['http_async_client'] = async_http_client
            logger.info("✅ [Copilot适配器] 已启用 GitHub-Bearer 认证格式")

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
    # 🔄 方案2: Copilot Chat API (模拟IDE插件，支持全模型)
    "claude-sonnet-4.5",     # Claude Sonnet 4.5 (Anthropic最新) ⭐️⭐️
    "claude-3.5-sonnet",     # Claude 3.5 Sonnet ⭐️
    "gpt-5",                 # GPT-5 (最新旗舰模型) ⭐️⭐️
    "gpt-5-mini",            # GPT-5 Mini (轻量快速)
    "o1-preview",            # O1 Preview (推理模型) ⭐️
    "o1-mini",               # O1 Mini (快速推理)

    # 🔵 方案1: Azure AI Inference (备用，向后兼容)
    "gpt-4o",                # GPT-4 Optimized ⭐️
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
    print("🎯 测试 GitHub Copilot 适配器")

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
        print("\n🔧 测试调用...")
        response = llm.invoke("你好，请用一句话介绍你自己")
        print(f"✅ 调用成功: {response.content}")

    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()

