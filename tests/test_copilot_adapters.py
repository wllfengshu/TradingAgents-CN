"""
测试 Copilot 和 Copilot Business 适配器
"""
import os
import sys

# 添加项目根目录到 Python 路径
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from dotenv import load_dotenv
load_dotenv()

def test_copilot_standard():
    """测试标准 Copilot 适配器"""
    print("=" * 80)
    print("测试标准 GitHub Copilot 适配器 (Azure AI Inference)")
    print("=" * 80)

    try:
        from tradingagents.llm_adapters.copilot_adapter import ChatCopilot

        # 检查环境变量
        token = os.getenv("GITHUB_COPILOT_TOKEN")
        if not token:
            print("⚠️  未设置 GITHUB_COPILOT_TOKEN，跳过测试")
            return

        # 初始化适配器
        copilot = ChatCopilot(
            model="gpt-4.1",
            temperature=1.0,
            max_tokens=500,
            timeout=120
        )

        print(f"\n✅ 初始化成功！")
        print(f"   模型: {copilot.model_name}")
        print(f"   Base URL: {copilot.openai_api_base}")

        # 测试简单调用
        print("\n测试简单对话...")
        response = copilot.invoke("请用一句话介绍你自己")
        print(f"\n回答: {response.content[:200]}...")

        print("\n✅ 标准 Copilot 测试通过！")

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()

def test_copilot_business():
    """测试 Copilot Business 适配器"""
    print("\n" + "=" * 80)
    print("测试 GitHub Copilot Business 适配器")
    print("=" * 80)

    try:
        from tradingagents.llm_adapters.copilot_business_adapter import ChatCopilotBusiness

        # 检查环境变量（支持两个变量名）
        token = os.getenv("GITHUB_COPILOT_BUSINESS_TOKEN") or os.getenv("GITHUB_COPILOT_SESSION_TOKEN")

        # 调试信息
        print(f"🔍 环境变量检查:")
        print(f"   GITHUB_COPILOT_BUSINESS_TOKEN: {'已设置' if os.getenv('GITHUB_COPILOT_BUSINESS_TOKEN') else '未设置'}")
        print(f"   GITHUB_COPILOT_SESSION_TOKEN: {'已设置' if os.getenv('GITHUB_COPILOT_SESSION_TOKEN') else '未设置'}")

        if not token:
            print("⚠️  未设置 Business Token，跳过测试")
            print("   请在 .env 文件中设置 GITHUB_COPILOT_BUSINESS_TOKEN 或 GITHUB_COPILOT_SESSION_TOKEN")
            return

        # 去除可能的引号
        token = token.strip("'\"")
        print(f"✅ 检测到 Business Token (长度: {len(token)})")

        # 测试多个模型名称（根据 VSCode Copilot 可用模型列表）
        test_models = [
            # ✅ 已验证可用的模型
            "gpt-5",                    # OpenAI GPT-5
            "gpt-4.1",                  # GPT-4.1
            "gpt-4o",                   # GPT-4 Optimized
            "claude-sonnet-4.5",        # Claude Sonnet 4.5

            # 🔍 待测试的其他模型（从 VSCode 截图）
            "gpt-5-mini",               # GPT-5 Mini
            "grok-code-fast-1",         # Grok Code Fast 1
            "claude-haiku-4.5",         # Claude Haiku 4.5
            "claude-sonnet-4",          # Claude Sonnet 4
            "gemini-2.5-pro",           # Gemini 2.5 Pro
            # "gemini-3-pro",             # Gemini 3 Pro (Preview)
            # "gpt-5-codex",              # GPT-5-Codex (Preview)
            "gpt-5.1",                  # GPT-5.1 (Preview)
            # "gpt-5.1-codex",            # GPT-5.1-Codex (Preview)
        ]

        print(f"\n🔍 测试可用模型 (总共 {len(test_models)} 个)...")
        from langchain_core.messages import HumanMessage

        success_models = []
        failed_models = []

        for i, model_name in enumerate(test_models, 1):
            try:
                print(f"\n{'='*60}")
                print(f"[{i}/{len(test_models)}] 测试模型: {model_name}")
                print(f"{'='*60}")

                # 初始化适配器
                copilot_biz = ChatCopilotBusiness(
                    model=model_name,
                    temperature=1.0,
                    max_tokens=100,
                    timeout=120
                )

                # 测试简单调用
                messages = [HumanMessage(content="你好，简单介绍一下你是什么模型")]
                response = copilot_biz._generate(messages)

                answer = response.generations[0].message.content[:150]
                print(f"✅ {model_name} 可用")
                print(f"   回答: {answer}...")
                success_models.append(model_name)

            except Exception as e:
                error_msg = str(e)[:200]
                print(f"❌ {model_name} 失败: {error_msg}")
                failed_models.append((model_name, error_msg))

        # 打印统计信息
        print("\n" + "="*80)
        print("📊 测试统计")
        print("="*80)
        print(f"✅ 可用模型 ({len(success_models)}/{len(test_models)}):")
        for model in success_models:
            print(f"   • {model}")

        if failed_models:
            print(f"\n❌ 不可用模型 ({len(failed_models)}/{len(test_models)}):")
            for model, error in failed_models:
                print(f"   • {model}: {error[:100]}")

        print("\n✅ Copilot Business 测试完成！")

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()

def test_imports():
    """测试导入"""
    print("\n" + "=" * 80)
    print("测试模块导入")
    print("=" * 80)

    try:
        from tradingagents.llm_adapters import ChatCopilot, ChatCopilotBusiness
        print("✅ ChatCopilot 导入成功")
        print("✅ ChatCopilotBusiness 导入成功")
        return True
    except Exception as e:
        print(f"❌ 导入失败: {e}")
        return False

if __name__ == "__main__":
    # 测试导入
    if not test_imports():
        sys.exit(1)

    # 测试标准 Copilot
    test_copilot_standard()

    # 测试 Business Copilot
    # test_copilot_business()

    print("\n" + "=" * 80)
    print("所有测试完成！")
    print("=" * 80)

