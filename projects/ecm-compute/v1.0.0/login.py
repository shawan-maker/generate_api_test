#!/usr/bin/env python3
"""
login.py — 独立登录脚本，保存 cookie 到 api/config/ 和 ui/config/

使用方法:
    cd scripts/v1.0.0
    python login.py
    python login.py --user estack-yy --pass "password"

登录成功后，cookie 会保存到:
  - api/config/cookies.json
  - ui/config/cookies.json
"""

import argparse
import json
import sys
from pathlib import Path

# 添加框架根目录到 path
# login.py 在 projects/ecm-compute/scripts/v1.0.0/，框架根在 D:\Mobile\API_AI_test\
_scripts_dir = Path(__file__).resolve().parent       # scripts/v1.0.0
_root = _scripts_dir.parent.parent.parent.parent     # API_AI_test (向上4层)
sys.path.insert(0, str(_root))

from lib.auth import AuthSession


def main():
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="登录并保存 cookie")
    parser.add_argument("--user", help="用户名")
    parser.add_argument("--password", dest="password", help="密码")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    project_dir = script_dir.parent.parent  # ecm-compute

    # 加载 profile.yaml
    import yaml
    profile_path = project_dir / "profile.yaml"
    if not profile_path.exists():
        print(f"[ERROR] profile.yaml 不存在: {profile_path}")
        sys.exit(1)

    with open(profile_path, "r", encoding="utf-8") as f:
        profile = yaml.safe_load(f)

    # 覆盖凭据（如果命令行提供）
    if args.user and args.password:
        profile.setdefault("credentials", {})["username"] = args.user
        profile["credentials"]["password"] = args.password

    print("=" * 60)
    print("  登录获取 Cookie")
    print("=" * 60)
    print(f"  项目: {profile.get('name')}")
    print(f"  URL:  {profile.get('login_url')}")
    print()

    # 创建 AuthSession
    auth = AuthSession(profile)

    # 设置 context_path 到 api/config/（AuthSession 会保存到这里）
    api_config_dir = script_dir / "api" / "config"
    api_config_dir.mkdir(parents=True, exist_ok=True)
    context_path = api_config_dir / "context.json"
    auth.set_context_path(context_path)

    # 调用 ensure_client 触发登录（如果 cookie 失效）
    base_url = profile.get("base_url", "")
    print("🔐 正在登录...")
    client = auth.ensure_client(base_url, launch_browser=True)

    if not client or not auth._cookies:
        print("❌ 登录失败，未获取到 cookies")
        sys.exit(1)

    print(f"✅ 登录成功，获取到 {len(auth._cookies)} 个 cookies")
    print()

    # 保存到 ui/config/
    ui_config_dir = script_dir / "ui" / "config"
    ui_config_dir.mkdir(parents=True, exist_ok=True)
    ui_cookies_path = ui_config_dir / "cookies.json"

    with open(ui_cookies_path, "w", encoding="utf-8") as f:
        json.dump(auth._cookies, f, ensure_ascii=False, indent=2)

    print(f"✅ 已保存: api/config/cookies.json")
    print(f"✅ 已保存: ui/config/cookies.json")
    print()
    print("=" * 60)
    print("  现在可以运行测试脚本了:")
    print("=" * 60)
    print()
    print("  API 测试:")
    print("    cd api")
    print("    python 用户管理_API测试.py")
    print()
    print("  UI 测试:")
    print("    cd ui")
    print("    python 用户管理.py --headless")
    print()


if __name__ == "__main__":
    main()
