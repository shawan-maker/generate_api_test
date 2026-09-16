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
    """将项目根 lib/ 下的运行时文件复制到 api/lib/。

    只同步 API 测试脚本真正需要的文件（依赖树追踪），
    废代码文件不再同步，旧版本残留自动清理。

    依赖关系:
      用户管理_API测试.py
      ├── lib/runtime/test_runtime.py     (直接导入)
      │   ├── lib.cookie_client           (懒加载，cookie 鉴权)
      │   └── lib.test_report             (懒加载，报告生成)
      └── lib/runtime/global_pre_apis.py  (条件导入，前置 API)
    """
    import shutil
    src_lib = Path(__file__).resolve().parent.parent / "lib"
    dst_lib = api_dir / "lib"
    dst_lib.mkdir(parents=True, exist_ok=True)

    # 同步映射表: (源相对路径, 目标相对路径)
    # 支持重命名（如 auth/cookie_client.py → cookie_client.py）
    sync_map = [
        # 顶层包标记
        ("__init__.py", "__init__.py"),
        # runtime 子包: 只同步真正使用的模块
        ("runtime/__init__.py", "runtime/__init__.py"),
        ("runtime/test_runtime.py", "runtime/test_runtime.py"),
        ("runtime/global_pre_apis.py", "runtime/global_pre_apis.py"),
        # 扁平文件: 从子目录提取到 lib/ 根（test_runtime.py 的懒加载依赖）
        ("auth/cookie_client.py", "cookie_client.py"),
        ("report/test_report.py", "test_report.py"),
    ]

    for src_rel, dst_rel in sync_map:
        src = src_lib / src_rel
        dst = dst_lib / dst_rel
        if not src.exists():
            LOG.warning(f"  运行时同步: 源文件不存在 {src_rel}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        src_content = src.read_text(encoding="utf-8")
        # __init__.py 写入最小 stub（源码中的 __init__.py 可能 re-export 废文件）
        if src_rel.endswith("__init__.py"):
            src_content = f'"""{src_rel.replace("/", ".")}"""\n'
        if dst.exists() and dst.read_text(encoding="utf-8") == src_content:
            continue  # 内容相同，跳过
        dst.write_text(src_content, encoding="utf-8")
        LOG.info(f"  运行时同步: {dst_rel}")

    # ---- 清理旧版残留 ----
    # 1. 删除已废弃的子包目录（auth/、report/ 不再需要）
    for dead_dir in ["auth", "report"]:
        dead_path = dst_lib / dead_dir
        if dead_path.exists():
            shutil.rmtree(dead_path)
            LOG.info(f"  清理旧目录: lib/{dead_dir}/")

    # 2. 删除旧版登录文件
    for old_file in ["auth.py", "slider.py"]:
        p = dst_lib / old_file
        if p.exists():
            p.unlink()
            LOG.info(f"  清理旧文件: lib/{old_file}")

    # 3. 删除已废弃的 runtime 子模块（不再使用）
    for dead_file in ["runtime/test_runner.py", "runtime/run_history.py"]:
        p = dst_lib / dead_file
        if p.exists():
            p.unlink()
            LOG.info(f"  清理旧文件: lib/{dead_file}")

    # 4. 删除历史残留的平铺重复文件（旧版同步机制遗留的旧副本）
    #    注意: test_report.py 是有效文件（test_runtime.py 的懒加载依赖），保留
    for flat_dead in ["test_runtime.py", "global_pre_apis.py"]:
        p = dst_lib / flat_dead
        if p.exists():
            p.unlink()
            LOG.info(f"  清理旧文件: lib/{flat_dead}")
