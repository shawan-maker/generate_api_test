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

    # 导入
    L.append('import sys, json')
    L.append('from pathlib import Path')
    L.append('')
    L.append('# 确保能找到 lib（向上搜索含 lib/auth.py 的仓库根）')
    L.append('_root = Path(__file__).resolve()')
    L.append("while _root.parent != _root and not (_root / 'lib' / 'auth.py').exists():")
    L.append('    _root = _root.parent')
    L.append('sys.path.insert(0, str(_root))')
    L.append('')
    L.append('from lib.test_runtime import TestRunner')
    L.append('')

    # Manifest 数据
    # json.dumps 输出 JSON 格式（null/true/false），需替换为 Python 字面量
    manifest_json = json.dumps(manifest, indent=2, ensure_ascii=False)
    manifest_json = manifest_json.replace(": null", ": None")
    manifest_json = manifest_json.replace(": true", ": True")
    manifest_json = manifest_json.replace(": false", ": False")
    L.append(f'MANIFEST = {manifest_json}')
    L.append('')

    # 入口
    L.append('if __name__ == "__main__":')
    L.append('    import io')
    L.append("    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')")
    L.append("    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')")
    L.append('    runner = TestRunner(MANIFEST)')
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
    output_path = scripts_dir / f"{module_name}_API测试.py"
    output_path.write_text(script, encoding="utf-8")
    LOG.info(f"  脚本已生成: {output_path}")
    return str(output_path)
