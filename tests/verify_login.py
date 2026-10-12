#!/usr/bin/env python3
"""
verify_login.py — 验证各项目登录功能

测试重构后的登录模块是否能成功登录各个项目。
不需要运行完整的 Stage 1-2 流程，只验证登录本身。

用法:
    python verify_login.py [project_name]

示例:
    python verify_login.py estack      # 只测试 estack
    python verify_login.py bcov        # 只测试 bcov
    python verify_login.py             # 测试所有项目
"""

import sys
import asyncio
import yaml
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from lib.auth import LoginSession, LoginConfig
from lib.auth.strategies.registry import get_strategy
from lib.auth.cookie_store import CookieStore


def load_project_config(project_name: str):
    """加载项目配置"""
    config_path = Path(__file__).parent / "projects" / project_name / "profile.yaml"
    if not config_path.exists():
        return None

    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 加载全局凭据（按 base_url 匹配）
    creds_path = Path(__file__).parent / "config" / "credentials.yaml"
    if creds_path.exists():
        with open(creds_path, 'r', encoding='utf-8') as f:
            all_creds = yaml.safe_load(f)
            sites = all_creds.get('sites', {})

            # 根据 base_url 匹配凭据
            base_url = config.get('base_url', '')
            creds = sites.get(base_url, {})

            if creds:
                config.setdefault('credentials', {})
                config['credentials']['username'] = creds.get('username', '')
                config['credentials']['password'] = creds.get('password', '')
                config['credentials']['username_env'] = creds.get('username_env', '')
                config['credentials']['password_env'] = creds.get('password_env', '')

                # 合并 auth 配置（凭据文件的 auth 覆盖 profile 的 auth）
                if 'auth' in creds:
                    config.setdefault('auth', {})
                    config['auth'].update(creds['auth'])

    return config


async def test_project_login(project_name: str) -> bool:
    """测试单个项目的登录功能"""
    print(f"\n{'='*60}")
    print(f"测试项目: {project_name}")
    print(f"{'='*60}")

    # 加载配置
    config = load_project_config(project_name)
    if not config:
        print(f"[FAIL] 未找到项目配置: {project_name}")
        return False

    # 获取策略名称
    strategy_name = config.get('strategy', 'ecscloud')
    print(f"使用策略: {strategy_name}")

    try:
        # 获取策略实例
        strategy = get_strategy(strategy_name)

        # 构建登录配置
        login_config = LoginConfig.from_profile(config)

        # 获取工作目录（用于存储 cookie）
        workspace_dir = Path(__file__).parent / "workspace" / project_name
        workspace_dir.mkdir(parents=True, exist_ok=True)

        # 创建登录会话
        session = LoginSession(workspace_dir, login_config)

        # 检查是否有新鲜 cookie
        store = CookieStore()
        if store.is_fresh(workspace_dir, login_config.fresh_ttl_seconds):
            print(f"[OK] 使用新鲜 cookie（跳过登录）")
            return True

        # 执行登录 - 需要启动 Playwright
        print(f"开始登录...")
        print(f"  登录 URL: {login_config.login_url}")
        print(f"  用户名: {login_config.username}")

        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            # 启动浏览器（忽略 HTTPS 证书错误）
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context(ignore_https_errors=True)
            page = await context.new_page()

            try:
                # 调用 ensure_auth
                success = await session.ensure_auth(page, context, strategy)

                if success:
                    print(f"[PASS] 登录成功")
                    print(f"  当前 URL: {page.url}")

                    # ensure_auth 已经保存了 cookie，读取显示
                    cookies = store.load(workspace_dir)
                    print(f"  Cookie 数量: {len(cookies)}")
                    print(f"  Cookie 已保存到: {workspace_dir}")

                    return True
                else:
                    print(f"[FAIL] 登录失败")
                    return False

            finally:
                await browser.close()

    except Exception as e:
        print(f"[FAIL] 登录异常: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """主函数"""
    # 解析命令行参数
    if len(sys.argv) > 1:
        projects = [sys.argv[1]]
    else:
        # 测试所有项目
        projects = ['estack', 'bcov', 'ecso', 'tianshu']

    print(f"\n{'#'*60}")
    print(f"# 登录模块验证测试")
    print(f"# 测试项目: {', '.join(projects)}")
    print(f"{'#'*60}")

    results = {}

    for project in projects:
        try:
            success = await test_project_login(project)
            results[project] = success
        except Exception as e:
            print(f"[FAIL] {project} 测试异常: {e}")
            results[project] = False

    # 输出汇总
    print(f"\n{'='*60}")
    print(f"测试汇总")
    print(f"{'='*60}")

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for project, success in results.items():
        status = "[PASS]" if success else "[FAIL]"
        print(f"{status} - {project}")

    print(f"\n总计: {passed}/{total} 通过")
    print(f"{'='*60}\n")

    return passed == total


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
