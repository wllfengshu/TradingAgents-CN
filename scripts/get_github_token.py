#!/usr/bin/env python3
"""
自动获取并配置 GitHub Copilot Token
适用于 Windows 系统
"""

import os
import re
import subprocess
import sys
from pathlib import Path


def check_gh_cli_installed():
    """检查 GitHub CLI 是否已安装"""
    try:
        result = subprocess.run(
            ["gh", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            version = result.stdout.strip().split('\n')[0]
            print(f"✅ GitHub CLI 已安装: {version}")
            return True
        else:
            return False
    except FileNotFoundError:
        return False
    except Exception as e:
        print(f"⚠️  检查 GitHub CLI 时出错: {e}")
        return False


def install_gh_cli():
    """指导用户安装 GitHub CLI"""
    print("\n" + "="*60)
    print("📦 GitHub CLI 未安装")
    print("="*60)
    print("\n请选择安装方式：")
    print("\n1️⃣  使用 winget 安装（推荐）:")
    print("   winget install --id GitHub.cli")
    print("\n2️⃣  使用 Chocolatey 安装:")
    print("   choco install gh")
    print("\n3️⃣  手动下载安装:")
    print("   访问: https://cli.github.com/")
    print("   下载 Windows 安装包并安装")
    print("\n" + "="*60)

    choice = input("\n是否现在使用 winget 自动安装? (y/n): ").strip().lower()
    if choice == 'y':
        print("\n🔄 正在使用 winget 安装 GitHub CLI...")
        try:
            result = subprocess.run(
                ["winget", "install", "--id", "GitHub.cli", "--silent"],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                print("✅ GitHub CLI 安装成功！")
                print("💡 请重新打开命令提示符窗口，然后再次运行此脚本")
                return True
            else:
                print(f"❌ 安装失败: {result.stderr}")
                return False
        except Exception as e:
            print(f"❌ 安装失败: {e}")
            return False
    else:
        print("\n💡 请手动安装 GitHub CLI 后再次运行此脚本")
        return False


def check_gh_auth():
    """检查是否已登录 GitHub"""
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if "Logged in to github.com" in result.stdout:
            print("✅ 已登录 GitHub")
            return True
        else:
            return False
    except Exception as e:
        print(f"⚠️  检查登录状态时出错: {e}")
        return False


def login_github():
    """登录 GitHub"""
    print("\n" + "="*60)
    print("🔐 需要登录 GitHub")
    print("="*60)
    print("\n将打开浏览器进行身份验证...")
    print("💡 请在浏览器中完成登录流程")

    try:
        # 使用交互式登录
        result = subprocess.run(
            ["gh", "auth", "login"],
            timeout=300  # 5分钟超时
        )
        if result.returncode == 0:
            print("\n✅ GitHub 登录成功！")
            return True
        else:
            print("\n❌ 登录失败")
            return False
    except subprocess.TimeoutExpired:
        print("\n❌ 登录超时")
        return False
    except Exception as e:
        print(f"\n❌ 登录失败: {e}")
        return False


def get_github_token():
    """获取 GitHub Token"""
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            token = result.stdout.strip()
            if token and len(token) > 20:
                print(f"✅ 成功获取 GitHub Token (长度: {len(token)})")
                return token
            else:
                print("❌ Token 无效")
                return None
        else:
            print(f"❌ 获取 Token 失败: {result.stderr}")
            return None
    except Exception as e:
        print(f"❌ 获取 Token 失败: {e}")
        return None


def update_env_file(token):
    """更新 .env 文件中的 Token"""
    # 查找项目根目录的 .env 文件
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    env_file = project_root / ".env"

    if not env_file.exists():
        print(f"❌ .env 文件不存在: {env_file}")
        return False

    try:
        # 读取原文件
        with open(env_file, 'r', encoding='utf-8') as f:
            content = f.read()

        # 替换 Token（保留注释和格式）
        pattern = r'(GITHUB_COPILOT_TOKEN=).*'
        replacement = f'\\1{token}'

        if re.search(pattern, content):
            new_content = re.sub(pattern, replacement, content)
        else:
            # 如果不存在，添加到文件末尾
            if not content.endswith('\n'):
                content += '\n'
            new_content = content + f'\nGITHUB_COPILOT_TOKEN={token}\n'

        # 写回文件
        with open(env_file, 'w', encoding='utf-8') as f:
            f.write(new_content)

        print(f"✅ .env 文件已更新: {env_file}")
        return True

    except Exception as e:
        print(f"❌ 更新 .env 文件失败: {e}")
        return False


def main():
    """主函数"""
    print("="*60)
    print("🚀 GitHub Copilot Token 自动配置工具")
    print("="*60)
    print()

    # 1. 检查 GitHub CLI
    if not check_gh_cli_installed():
        if not install_gh_cli():
            print("\n❌ 配置失败: 请先安装 GitHub CLI")
            sys.exit(1)
        # 安装后需要重启终端
        sys.exit(0)

    # 2. 检查是否已登录
    if not check_gh_auth():
        if not login_github():
            print("\n❌ 配置失败: 无法登录 GitHub")
            sys.exit(1)

    # 3. 获取 Token
    print("\n🔄 正在获取 GitHub Token...")
    token = get_github_token()

    if not token:
        print("\n❌ 配置失败: 无法获取 Token")
        sys.exit(1)

    # 4. 更新 .env 文件
    print("\n🔄 正在更新 .env 文件...")
    if not update_env_file(token):
        print("\n⚠️  自动更新失败，请手动将以下内容添加到 .env 文件:")
        print(f"\nGITHUB_COPILOT_TOKEN={token}")

    # 5. 完成
    print("\n" + "="*60)
    print("🎉 配置完成！")
    print("="*60)
    print("\n✅ GitHub Copilot Token 已配置成功")
    print("\n📋 下一步:")
    print("   1. 运行测试脚本: python scripts\\test_copilot_integration.py")
    print("   2. 重启应用服务")
    print("   3. 在 Web 界面选择 GitHub Copilot 模型")
    print("\n💡 提示: Token 已保存到 .env 文件中，无需手动复制")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  用户取消操作")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

