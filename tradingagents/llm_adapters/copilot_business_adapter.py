"""
GitHub Copilot Business 适配器
通过 Copilot Business Bearer Token 访问 GPT-5、Claude-Sonnet-4.5 等高级模型

✅ 已验证支持的模型：
- gpt-5: OpenAI GPT-5 ⭐(最新旗舰模型，已测试可用)
- gpt-4.1: GPT-4.1 (已测试可用)
- gpt-4o: GPT-4 Optimized (已测试可用)
- claude-sonnet-4.5: Claude Sonnet 4.5 ⭐(Anthropic最新模型，已测试可用)
- gemini-2.5-pro: Gemini 2.5 Pro (Google最新模型)
- grok-code-fast-1: Grok Code Fast 1

注意：
- VSCode 中显示的模型名称可能与 API 实际接受的名称不同
- 部分模型需要特定权限或在特定区域才可用
- 建议先测试确认模型可用性

获取 Copilot Business Token:
1. 使用抓包工具
2. 找到 https://business.githubcopilot.com/域名
3. 找到 chat/completions 请求
4. 复制 Authorization 头中的完整 Bearer token
5. 在 .env 文件中配置:
   GITHUB_COPILOT_BUSINESS_TOKEN=Bearer tid=...;sku=copilot_for_business...
   GITHUB_COPILOT_BUSINESS_MODEL=gpt-5
   GITHUB_COPILOT_BUSINESS_ENABLED=true

注意：
- Business Token 格式包含 tid=, sku=copilot_for_business 等字段
- Token 可能几小时后过期，需要重新获取
- 完全独立实现，不受父类限制
"""

import os
import uuid
import httpx
from typing import Any, Dict, List, Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from langchain_core.callbacks import CallbackManagerForLLMRun
from pydantic import Field
from tradingagents.utils.logging_manager import get_logger

logger = get_logger('agents')

class ChatCopilotBusiness(BaseChatModel):
    """
    GitHub Copilot Business 大模型适配器

    完全独立实现，直接调用 business.githubcopilot.com API
    不依赖任何父类的请求逻辑，完全控制 URL、请求头、参数和响应解析
    """

    model: str = Field(default="gpt-5", description="模型名称")
    api_key: Optional[str] = Field(default=None, description="Business Bearer Token")
    temperature: float = Field(default=1.0, description="温度参数")
    max_tokens: Optional[int] = Field(default=8000, description="最大token数")
    timeout: int = Field(default=120, description="超时时间(秒)")
    base_url: str = Field(default="https://api.business.githubcopilot.com", description="API端点")

    # 内部属性
    _http_client: Optional[httpx.Client] = None
    _vscode_machine_id: str = ""
    _vscode_session_id: str = ""

    def __init__(self, **kwargs):
        """初始化 Copilot Business 适配器"""
        super().__init__(**kwargs)

        # 从环境变量读取 token（支持两个变量名）
        if not self.api_key:
            self.api_key = os.getenv("GITHUB_COPILOT_BUSINESS_TOKEN") or os.getenv("GITHUB_COPILOT_SESSION_TOKEN")

        # 去除可能的引号
        if self.api_key:
            self.api_key = self.api_key.strip("'\"")

        if not self.api_key:
            raise ValueError(
                "未找到 GitHub Copilot Business Token。\n"
                "请在 .env 文件中设置 GITHUB_COPILOT_BUSINESS_TOKEN 或 GITHUB_COPILOT_SESSION_TOKEN\n"
                "Token 格式示例: Bearer tid=...;sku=copilot_for_business..."
            )

        # 生成 VSCode 会话标识
        self._vscode_machine_id = os.getenv('COPILOT_VSCODE_MACHINEID', str(uuid.uuid4()))
        self._vscode_session_id = os.getenv('COPILOT_VSCODE_SESSIONID', str(uuid.uuid4()))

        # 初始化 HTTP 客户端
        self._http_client = httpx.Client(
            timeout=self.timeout,
            verify=False  # 禁用 SSL 验证以支持公司网络
        )

        # 验证和记录 token 信息
        self._validate_and_log_token()

        logger.info(f"🚀 [Copilot Business] 初始化 - 模型: {self.model}, 端点: {self.base_url}")
        logger.info(f"✅ [Copilot Business] 初始化成功")

    def _validate_and_log_token(self) -> None:
        """验证 token 格式并打印过期时间信息"""
        if not self.api_key:
            return

        token = self.api_key.strip()

        # 检查是否为 Business token 格式
        if 'sku=copilot_for_business' in token or 'tid=' in token:
            logger.info("[INIT] 检测到 Copilot Business Token")

            # 提取过期时间
            import re
            exp_match = re.search(r'exp=(\d+)', token)
            if exp_match:
                from datetime import datetime, timezone
                exp_timestamp = int(exp_match.group(1))
                exp_datetime = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
                now = datetime.now(timezone.utc)
                remaining = exp_datetime - now

                total_seconds = int(remaining.total_seconds())
                if total_seconds < 0:
                    status = "EXPIRED"
                    hours, remainder = divmod(abs(total_seconds), 3600)
                    minutes, seconds = divmod(remainder, 60)
                    remaining_str = f"-{hours}h {minutes}m {seconds}s"
                else:
                    status = "VALID"
                    hours, remainder = divmod(total_seconds, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    remaining_str = f"{hours}h {minutes}m {seconds}s"

                logger.info(
                    f"[INIT] Token exp={exp_timestamp} "
                    f"(UTC {exp_datetime.strftime('%Y-%m-%d %H:%M:%S')}) "
                    f"remaining={remaining_str} status={status}"
                )

                if status == "EXPIRED":
                    logger.warning(
                        "⚠️  [Copilot Business] Token 已过期！请更新 .env 中的 GITHUB_COPILOT_BUSINESS_TOKEN\n"
                        "获取方式：浏览器 F12 -> Network -> 查找 chat/completions 请求"
                    )
        else:
            logger.warning("⚠️  [Copilot Business] Token 格式不符合 Business Token 规范")

    def _build_headers(self) -> Dict[str, str]:
        """构建请求头"""
        # 规范化 token：确保有 Bearer 前缀
        token = self.api_key.strip()
        if not token.lower().startswith('bearer '):
            token = f'Bearer {token}'

        return {
            'authorization': token,
            'content-type': 'application/json',
            'accept': 'application/json',
            'copilot-integration-id': os.getenv('COPILOT_INTEGRATION_ID', 'vscode-chat'),
            'editor-plugin-version': os.getenv('COPILOT_EDITOR_PLUGIN_VERSION', 'copilot-chat/0.33.1'),
            'editor-version': os.getenv('COPILOT_EDITOR_VERSION', 'vscode/1.106.0'),
            'openai-intent': 'conversation-panel',
            'user-agent': os.getenv('COPILOT_USER_AGENT', 'GitHubCopilotChat/0.33.1'),
            'vscode-machineid': self._vscode_machine_id,
            'vscode-sessionid': self._vscode_session_id,
            'x-github-api-version': os.getenv('COPILOT_API_VERSION', '2025-10-01'),
            'x-initiator': 'user',
            'x-interaction-id': str(uuid.uuid4()),
            'x-interaction-type': 'conversation-panel',
            'x-request-id': str(uuid.uuid4()),
        }

    def _convert_messages(self, messages: List[BaseMessage]) -> List[Dict[str, str]]:
        """将 LangChain 消息格式转换为 API 格式"""
        result = []
        for msg in messages:
            if isinstance(msg, HumanMessage):
                result.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                result.append({"role": "assistant", "content": msg.content})
            elif isinstance(msg, SystemMessage):
                result.append({"role": "system", "content": msg.content})
            else:
                # 其他类型按 user 处理
                result.append({"role": "user", "content": str(msg.content)})
        return result

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """生成回复（同步方法）"""
        # 构建请求体
        body = {
            "model": self.model,
            "messages": self._convert_messages(messages),
            "temperature": self.temperature,
            "top_p": 1.0,
            "n": 1,
            "stream": False,  # 非流式
            "max_tokens": self.max_tokens or 4096,
        }

        # 合并额外参数
        body.update(kwargs)

        # 构建请求头
        headers = self._build_headers()

        # 发送请求
        url = f"{self.base_url}/chat/completions"

        try:
            logger.info(f"🔍 [Copilot Business] 请求 URL: {url}")
            logger.info(f"🔍 [Copilot Business] 请求模型: {self.model}")
            logger.debug(f"🔍 [Copilot Business] 请求体: {body}")
            logger.debug(f"🔍 [Copilot Business] 请求头: {list(headers.keys())}")

            response = self._http_client.post(url, json=body, headers=headers)

            logger.info(f"🔍 [Copilot Business] 响应状态: {response.status_code}")

            if response.status_code >= 400:
                error_text = response.text[:500]
                logger.error(f"❌ [Copilot Business] 请求失败: {response.status_code} | model={self.model} | body={body} | error={error_text}")
                raise RuntimeError(
                    f"Copilot Business API 请求失败 {response.status_code}: {error_text}\n"
                    f"请检查 token 是否过期或格式是否正确"
                )

            # 解析响应
            data = response.json()

            # 记录响应中的模型信息
            actual_model = data.get('model', 'unknown')
            logger.info(f"🔍 [Copilot Business] 实际使用的模型: {actual_model}")

            # 提取内容
            content = data.get('choices', [{}])[0].get('message', {}).get('content', '')

            if not content:
                content = data.get('output_text') or str(data)

            # 创建结果
            message = AIMessage(content=content)
            generation = ChatGeneration(message=message)

            logger.info(f"✅ [Copilot Business] 请求成功 (请求模型={self.model}, 实际模型={actual_model})")

            return ChatResult(generations=[generation])

        except Exception as e:
            logger.error(f"❌ [Copilot Business] 请求失败: {e} | model={self.model} | body={body}")
            raise

    @property
    def _llm_type(self) -> str:
        """返回 LLM 类型"""
        return "copilot-business"

    @property
    def _identifying_params(self) -> Dict[str, Any]:
        """返回识别参数"""
        return {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

    def __del__(self):
        """清理资源"""
        if self._http_client:
            try:
                self._http_client.close()
            except:
                pass
