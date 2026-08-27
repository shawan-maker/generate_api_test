"""
_verify_auth.py — 验证鉴权配置（对齐 EcsCloud _verify_auth_config.js）

独立于发现流程，一键验证凭据配置 + 登录 + 探活 + cookie 状态。

用法：
  python -m scripts._verify_auth --project ecm-compute
  python -m scripts._verify_auth --project ecm-compute --force-probe
"""
import sys
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def parse_args():
    import argparse
    ap = argparse.ArgumentParser(description="验证鉴权配置")
    ap.add_argument("--project", required=True, help="项目 ID (projects/<id>/)")
    ap.add_argument("--force-probe", action="store_true", help="强制探活（忽略新鲜度缓存）")
    return ap.parse_args()


def load_profile(project_dir: Path) -> dict:
    try:
        import yaml
    except Exception:
        print("⚠️  pyyaml 未安装，尝试 JSON 加载")
        return {}
    p = project_dir / "profile.yaml"
    if p.exists():
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {}


def main():
    args = parse_args()
    project_dir = ROOT / "projects" / args.project

    print("=" * 60)
    print("  验证鉴权配置")
    print("=" * 60)
    print()

    # 1. 检查 profile.yaml 和 auth.json
    print("[1/4] 读取凭据配置 ...")
    if not project_dir.exists():
        print(f"❌ 项目目录不存在: {project_dir}")
        sys.exit(1)

    profile = load_profile(project_dir)
    if not profile:
        print(f"❌ 无法读取 profile.yaml: {project_dir / 'profile.yaml'}")
        sys.exit(1)

    # 构建临时 AuthSession 以使用 resolve_credentials
    from lib.auth import AuthSession
    sess = AuthSession(profile)
    ctx_dir = project_dir / "output" / "config"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    sess.context_path = str(ctx_dir / "context.json")

    # 检查 auth.json
    auth_json_path = ctx_dir / "auth.json"
    auth_json_info = AuthSession.load_auth_json(auth_json_path)

    # 解析凭据（四级优先级）
    creds = sess.resolve_credentials()
    username = creds["username"]
    password = creds["password"]

    print(f"   凭据来源:")
    print(f"     - auth.json: {'✅ 存在' if auth_json_path.exists() else '❌ 不存在'}")
    if auth_json_path.exists():
        print(f"       用户名: {auth_json_info['username'][:8] + '...' if auth_json_info.get('username') else '未配置'}")
    print(f"     - profile.yaml credentials: {'✅ 已配置' if profile.get('credentials', {}).get('username') else '❌ 未配置'}")
    print(f"   最终解析:")
    print(f"     用户名: {username[:8] + '...' if username else '❌ 未读取到'}")
    print(f"     密码:   {'***' + password[-4:] if password else '❌ 未读取到'}")
    print(f"     环境:   {profile.get('base_url', '未指定')}")
    print(f"     登录URL: {profile.get('login_url', '未指定')}")
    print()

    if not username or not password:
        print("❌ 未找到可用凭据，请配置以下任一方式:")
        print("   1. 创建 auth.json (推荐，可 .gitignore 排除)")
        print(f"      示例: {ctx_dir}/auth.json.example")
        print("   2. 编辑 profile.yaml 的 credentials 字段")
        print("   3. 设置环境变量 AUTO_LOGIN_USER / AUTO_LOGIN_PASS")
        sys.exit(1)

    # 2. 构建 AuthSession 并验证
    print("[2/4] 构建 AuthSession 并验证 ...")

    # 更新凭据到 sess
    sess.username = username
    sess.password = password

    base_url = profile.get("base_url", "")
    t0 = time.time()

    client = sess.ensure_client(base_url)
    ms = round((time.time() - t0) * 1000)

    if client is None:
        print(f"❌ 无法获取有效 client（耗时 {ms}ms）")
        print("   请检查凭据、网络连接、滑块登录是否正常")
        sys.exit(1)

    print(f"✅ 有效 client 已获取（耗时 {ms}ms）")
    print(f"   token: {sess.token[:16] + '...' if sess.token else '❌ 无'}")
    print(f"   cookies: {len(sess._cookies)} 条")
    print()

    # 3. 探活验证
    print("[3/4] 探活验证 ...")
    probe_ok = sess.probe_online(base_url)
    print(f"   探活结果: {'✅ 通过' if probe_ok else '❌ 失败'}")
    print()

    # 4. 检查 cookies.json 状态
    print("[4/4] cookies.json 状态 ...")
    cookies = sess.load_cookies()
    has_token = any(c.get("name") == "accessToken" for c in cookies)
    meta = sess._read_meta()
    saved_at = meta.get("savedAt", 0)
    saved_at_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(saved_at)) if saved_at else "无"

    print(f"   cookie 数量: {len(cookies)}")
    print(f"   accessToken: {'✅ 存在' if has_token else '❌ 缺失'}")
    print(f"   meta.savedAt: {saved_at_str}")
    print()

    client.close()

    # 汇总
    print("=" * 60)
    if client and probe_ok and has_token:
        print("✅ 全部验证通过！鉴权配置正确，登录逻辑正常")
        print("=" * 60)
        sys.exit(0)
    else:
        print("❌ 验证未完全通过，请检查上方输出")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
