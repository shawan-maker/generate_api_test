"""
run_with_version.py — 版本入口转发器（复用 EcsCloud run_with_version.js 思路）。

用法（与 EcsCloud 对齐）：
  python -m module_discovery.run_with_version --project ecm-compute --url ... --module 用户管理 --stage all
  python -m module_discovery.run_with_version v2.1.0 --project ecm-compute --url ... --module 用户管理 --stage all
  API_VERSION=v3.1.0 python -m module_discovery.run_with_version --project ecm-compute ...

规则：
  1) 第一个位置参数若形如 vX.Y.Z 则视为版本号（覆盖 .api_version / API_VERSION 环境变量）
  2) 设置 os.environ["API_VERSION"] 后，将剩余参数转发给 module_discovery.run
  3) 生成的脚本写入 projects/<id>/scripts/<version>/api/，不同版本互不覆盖
"""
import os
import sys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import subprocess

VER_RE = re.compile(r"^v\d+\.\d+\.\d+$")


def main():
    args = sys.argv[1:]
    # 第一个位置参数可能是版本号
    version = None
    if args and VER_RE.match(args[0]):
        version = args[0]
        args = args[1:]

    if version:
        os.environ["API_VERSION"] = version
        print(f"[版本] 使用位置参数版本: {version}")
    else:
        # 回退到 .api_version / API_VERSION
        from module_discovery import version as ver_mod
        # 需要 project 才能解析 .api_version；若 args 含 --project 则解析
        proj = None
        for i, a in enumerate(args):
            if a == "--project" and i + 1 < len(args):
                proj = args[i + 1]
        if not os.environ.get("API_VERSION") and proj:
            pdir = ROOT / "projects" / proj
            v = ver_mod.resolve_version(pdir)
            os.environ["API_VERSION"] = v
            print(f"[版本] 使用解析版本: {v}")

    # 转发给 run.py
    cmd = [sys.executable, "-m", "module_discovery.run", *args]
    print(f"[版本] 转发: {' '.join(cmd)}")
    print("")
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
