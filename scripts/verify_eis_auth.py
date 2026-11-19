"""
verify_eis_auth.py — EIS 登录策略全链路验证脚本

走真实管线调用路径（与 estack 完全一致）：
  AuthService.ensure_browser_auth(page, context)
    → LoginSession.ensure_auth（cookie 优先 → probe → 登录）
    → CookieStore.save 写回 workspace/eis/output/config/cookies.json

验证项：
  1. 策略注册（get_strategy("eis")）
  2. 凭据解析（credentials.yaml > profile.yaml）
  3. 首次登录（浏览器自动化 + OCR 验证码）
  4. cookie 写回（workspace/eis/output/config/cookies.json）
  5. 二次调用 cookie 复用（不重新登录）

用法（项目根目录下）:
    python scripts/verify_eis_auth.py [--headless]
"""

import asyncio
import argparse
import json
import sys
from pathlib import Path

import yaml

# 项目根加入 sys.path（脚本独立运行时）
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    profile = yaml.safe_load((ROOT / "projects" / "eis" / "profile.yaml").read_text(encoding="utf-8"))

    from core.discovery.auth_service import AuthService
    from lib.auth.strategies.registry import get_strategy, list_strategies
    from lib.browser_launcher import launch_browser
    from playwright.async_api import async_playwright

    # ===== 验证 1: 策略注册 =====
    strategies = list_strategies()
    print("已注册策略:", list(strategies.keys()))
    assert "eis" in strategies, "EIS 策略未注册！"
    s = get_strategy("eis")
    print(f"✅ [1] 策略注册 OK: {s.key} ({s.label})")

    # ===== 验证 2: AuthService 构建 =====
    auth = AuthService(
        project_dir=ROOT / "projects" / "eis",
        workspace_dir=ROOT / "workspace" / "eis",
        profile=profile,
    )
    print(f"✅ [2] AuthService 构建完成（strategy={profile['strategy']}）")

    cookie_file = ROOT / "workspace" / "eis" / "output" / "config" / "cookies.json"

    # ===== 验证 3: 首次登录（走完整 ensure_auth 流程）=====
    async with async_playwright() as pw:
        browser, context, page = await launch_browser(pw, headless=args.headless)
        try:
            ok = await auth.ensure_browser_auth(page, context)
            print(f"{'✅' if ok else '❌'} [3] 首次认证: {'成功' if ok else '失败'}")
            if not ok:
                sys.exit(1)
            print(f"    登录后 URL: {page.url}")

            # ===== 验证 4: cookie 写回 =====
            if cookie_file.exists():
                cookies = json.loads(cookie_file.read_text(encoding="utf-8"))
                names = [c["name"] for c in cookies]
                has_token = "token" in names
                print(f"{'✅' if has_token else '❌'} [4] cookie 写回: {cookie_file}")
                print(f"    cookie 条目: {names}")
                assert has_token, "cookie 中无 token！"
            else:
                print(f"❌ [4] cookie 文件未生成: {cookie_file}")
                sys.exit(1)

            # ===== 验证 5: 二次调用（cookie 复用，不重新登录）=====
            page2 = await context.new_page()
            ok2 = await auth.ensure_browser_auth(page2, context)
            print(f"{'✅' if ok2 else '❌'} [5] 二次认证（cookie 复用）: {'成功' if ok2 else '失败'}")
            print(f"    探测后 URL: {page2.url}")
        finally:
            await browser.close()

    print("\n全部验证通过 ✅")


if __name__ == "__main__":
    asyncio.run(main())
