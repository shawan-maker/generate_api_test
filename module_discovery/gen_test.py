"""
gen_test.py — Stage 4: 生成可执行的 API 测试脚本 (manifest 模式)

根据 manifest dict 生成薄脚本（~50 行），所有执行逻辑由 lib/test_runtime.py 提供。
"""

import json
import time
import logging
from pathlib import Path

LOG = logging.getLogger("gen_test")


def generate_script(manifest: dict, module_name: str) -> str:
    """生成 manifest 驱动的薄脚本。

    Args:
        manifest: 完整的测试清单 dict（由 analyze_flow.build_manifest 生成）
        module_name: 模块名称

    Returns:
        Python 脚本字符串
    """
    return generate_manifest_script(manifest, module_name)


def generate_manifest_script(manifest: dict, module_name: str) -> str:
    """生成嵌入 manifest 的薄脚本（泛化架构）。

    新架构下生成的脚本只有 ~50 行：manifest JSON + import runtime + main()。
    所有执行逻辑由 lib/test_runtime.py 提供。

    Args:
        manifest: 完整的测试清单 dict（由 analyze_flow.build_manifest 生成）
        module_name: 模块名称

    Returns:
        Python 脚本字符串
    """
    L = []

    # 文件头
    L.append('"""')
    L.append(f'{module_name}_API测试.py — 由 module_discovery 自动生成 (manifest 模式)')
    L.append(f'生成时间: {time.strftime("%Y-%m-%d %H:%M:%S")}')
    target_url = manifest.get("module", {}).get("target_url", "")
    L.append(f'目标URL: {target_url}')
    L.append('')
    steps = manifest.get("steps", [])
    L.append('执行流程:')
    for i, s in enumerate(steps, 1):
        L.append(f'  {i}. {s.get("label", s.get("action", "?"))}')
    sa = manifest.get("state_assertions", {})
    if sa.get("after_delete") == "NOT_EXIST":
        L.append('')
        L.append('状态断言:')
        L.append('  - 删除后数据不应出现')
    L.append('"""')
    L.append('')

    # 导入：bootstrap 找到同目录的 lib/
    L.append('import sys, json')
    L.append('from pathlib import Path')
    L.append('')
    L.append('# 找到同目录的 lib/（脚本独立运行时使用）')
    L.append('_lib = Path(__file__).resolve().parent / "lib"')
    L.append('sys.path.insert(0, str(_lib.parent))')
    L.append('from lib.runtime.test_runtime import TestRunner')
    L.append('')

    # Manifest 数据 — 移除 cookie-only 模式不需要的字段
    # json.dumps 输出 JSON 格式（null/true/false），需替换为 Python 字面量
    manifest_clean = json.loads(json.dumps(manifest))
    auth_profile = manifest_clean.get("auth_profile", {})
    # cookie-only 模式不需要 captcha 和 credentials_default
    auth_profile.pop("captcha", None)
    auth_profile.pop("credentials_default", None)

    manifest_json = json.dumps(manifest_clean, indent=2, ensure_ascii=False)
    manifest_json = manifest_json.replace(": null", ": None")
    manifest_json = manifest_json.replace(": true", ": True")
    manifest_json = manifest_json.replace(": false", ": False")
    L.append(f'MANIFEST = {manifest_json}')
    L.append('')

    # 全局前置 API 支持
    L.append('# --- 全局前置 API 支持 ---')
    L.append('_shared_ctx_file = Path(__file__).resolve().parent / ".shared_context.json"')
    L.append('SHARED_CONTEXT = {}')
    L.append('if _shared_ctx_file.exists():')
    L.append('    try:')
    L.append('        from lib.runtime.global_pre_apis import GlobalPreApiExecutor')
    L.append('        SHARED_CONTEXT = GlobalPreApiExecutor.load_context(_shared_ctx_file)')
    L.append('        if SHARED_CONTEXT:')
    L.append('            print("  ✅ 检测到共享上下文")')
    L.append('    except ImportError:')
    L.append('        pass')
    L.append('')

    # 入口
    L.append('if __name__ == "__main__":')
    L.append('    import io')
    L.append("    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')")
    L.append("    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')")
    L.append('    runner = TestRunner(MANIFEST, shared_context=SHARED_CONTEXT)')
    L.append('    steps = sys.argv[1:] if len(sys.argv) > 1 else None')
    L.append('    runner.run(steps_filter=steps)')

    return '\n'.join(L)


def save_script_to_file(script: str, project_dir: str, module_name: str, version: str = ""):
    """将生成的脚本保存到文件。version 非空时写入 scripts/<version>/api/。"""
    from . import version as _ver
    if version:
        scripts_dir = _ver.scripts_dir_for(project_dir, version) / "api"
    else:
        scripts_dir = Path(project_dir) / "scripts" / "v1.0.0" / "api"
        scripts_dir.mkdir(parents=True, exist_ok=True)

    # 同步运行时 lib/ 到 api/lib/
    _sync_runtime_lib(scripts_dir)

    output_path = scripts_dir / f"{module_name}_API测试.py"
    output_path.write_text(script, encoding="utf-8")
    LOG.info(f"  脚本已生成: {output_path}")
    return str(output_path)


def _sync_runtime_lib(api_dir: Path):
    """将项目根 lib/ 下的运行时文件复制到 api/lib/。"""
    src_lib = Path(__file__).resolve().parent.parent / "lib"
    dst_lib = api_dir / "lib"
    dst_lib.mkdir(parents=True, exist_ok=True)

    # 生成脚本同步的运行时文件（相对于 src_lib 的路径）
    # 包含子目录：runtime/、report/、auth/
    files = [
        "__init__.py",
        "runtime/__init__.py",
        "runtime/test_runtime.py",
        "runtime/test_runner.py",
        "runtime/run_history.py",
        "runtime/global_pre_apis.py",
        "report/__init__.py",
        "report/test_report.py",
        "auth/__init__.py",
        "auth/cookie_client.py",
    ]
    for rel_path in files:
        src = src_lib / rel_path
        dst = dst_lib / rel_path
        if not src.exists():
            continue
        # 确保目标目录存在
        dst.parent.mkdir(parents=True, exist_ok=True)
        src_content = src.read_text(encoding="utf-8")
        if dst.exists() and dst.read_text(encoding="utf-8") == src_content:
            continue  # 内容相同，跳过
        dst.write_text(src_content, encoding="utf-8")
        LOG.info(f"  运行时同步: {rel_path}")

    # 清理旧版登录文件（生成脚本不再包含滑块登录）
    for old_file in ["auth.py", "slider.py"]:
        p = dst_lib / old_file
        if p.exists():
            p.unlink()
            LOG.info(f"  清理旧文件: {old_file}")
