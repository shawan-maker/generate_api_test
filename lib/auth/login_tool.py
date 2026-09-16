#!/usr/bin/env python3
"""
login_tool.py — 独立登录工具

运行登录流程，获取 cookie 后保存到 config/cookies.json。
用户可以：
1. 运行此工具自动登录并保存 cookie
2. 手动编辑 config/cookies.json（Playwright cookie 格式）

使用方式：
    # 从项目目录运行
    cd projects/ecm-compute
    python -m lib.auth.login_tool

    # 或指定输出路径
    python -m lib.auth.login_tool --output scripts/v1.0.0/api/config/cookies.json

    # 覆盖凭据
    python -m lib.auth.login_tool --username admin --password secret

    # 无头模式
    python -m lib.auth.login_tool --headless
"""

import argparse
import asyncio
import json
import sys
import io
from pathlib import Path

# Windows 终端 UTF-8 支持
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def main():
    parser = argparse.ArgumentParser(
        description="登录并保存 cookie 到 config/cookies.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output", "-o",
        help="输出路径（默认：output/config/cookies.json）",
        default="output/config/cookies.json",
    )
    parser.add_argument(
        "--username", "-u",
        help="用户名（可选，默认从 profile.yaml 读取）",
    )
    parser.add_argument(
        "--password", "-p",
        help="密码（可选，默认从 profile.yaml 读取）",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="无头模式运行（不显示浏览器窗口）",
    )
    args = parser.parse_args()

    # 加载当前目录的 profile.yaml
    profile_path = Path("profile.yaml")
    if not profile_path.exists():
        print(f"❌ 当前目录找不到 profile.yaml")
        print(f"   请先 cd 到项目目录，如：cd projects/ecm-compute")
        sys.exit(1)

    try:
        import yaml
    except ImportError:
        print("❌ 缺少 PyYAML 依赖，请运行: pip install pyyaml")
        sys.exit(1)

    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))

    # 提取登录配置
    login_url = profile.get("login_url")
    if not login_url:
        print(f"❌ profile.yaml 中缺少 login_url")
        sys.exit(1)

    username = args.username or profile.get("credentials", {}).get("username")
    password = args.password or profile.get("credentials", {}).get("password")
    if not username or not password:
        print(f"❌ 缺少用户名或密码")
        print(f"   可通过 --username/--password 参数指定，或在 profile.yaml 中配置")
        sys.exit(1)

    # 构建 auth profile
    auth_profile = {
        "login_url": login_url,
        "auth": profile.get("auth", {}),
        "captcha": profile.get("captcha", {}),
        "credentials": {"username": username, "password": password},
    }

    print(f"🔐 登录目标: {login_url}")
    print(f"👤 用户名: {username}")
    print(f"💾 保存到: {args.output}")

    # 运行登录
    asyncio.run(do_login(auth_profile, args.output, args.headless))


async def do_login(auth_profile: dict, output_path: str, headless: bool):
    """执行登录并保存 cookie"""
    from playwright.async_api import async_playwright
    from .auth import AuthSession

    # 创建 AuthSession
    auth_session = AuthSession(auth_profile)

    # 启动浏览器
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            ignore_https_errors=True,
        )
        page = await context.new_page()

        # 执行登录
        print("🚀 开始登录...")
        success = await auth_session.login_with_browser(page, context)

        if not success:
            print("❌ 登录失败")
            await browser.close()
            sys.exit(1)

        # 获取 cookie
        cookies = await context.cookies()
        print(f"✅ 登录成功，获取到 {len(cookies)} 个 cookie")

        # 保存 cookie
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(
            json.dumps(cookies, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"💾 Cookie 已保存: {output_file}")

        # 显示关键 cookie
        token_key = auth_profile.get("auth", {}).get("cookie_token_key", "")
        if token_key:
            for c in cookies:
                if c.get("name") == token_key:
                    print(f"🔑 Token ({token_key}): {c['value'][:20]}...")
                    break

        await browser.close()


if __name__ == "__main__":
    main()
