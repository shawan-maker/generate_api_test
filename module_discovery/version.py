"""
version.py — 按版本管理测试脚本（复用 EcsCloud run_with_version.js 思路）。

设计（与 EcsCloud 对齐）：
  * 每个版本独立维护一套脚本：projects/<id>/scripts/<version>/api/<模块>_API测试.py
  * 版本号来源优先级：环境变量 API_VERSION > 项目根 .api_version 文件 > 默认 v1.0.0
  * 不同版本互不影响，重跑发现不会覆盖其它版本的脚本
"""
import os
import json
from pathlib import Path
from typing import Optional


DEFAULT_VERSION = "v1.0.0"


def resolve_version(project_dir) -> str:
    """解析当前版本号。"""
    project_dir = Path(project_dir)
    env = os.environ.get("API_VERSION")
    if env:
        return env.strip()
    f = project_dir / ".api_version"
    if f.exists():
        v = f.read_text(encoding="utf-8").strip()
        if v:
            return v
    return DEFAULT_VERSION


def write_version_file(project_dir, version: str):
    """写入 .api_version 标记。"""
    project_dir = Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".api_version").write_text(version + "\n", encoding="utf-8")


def flows_dir_for(project_dir, version: Optional[str] = None) -> Path:
    """返回指定版本的 flows 目录（自动创建）。已废弃，请使用 scripts_dir_for。"""
    project_dir = Path(project_dir)
    version = version or resolve_version(project_dir)
    d = project_dir / "flows" / version
    d.mkdir(parents=True, exist_ok=True)
    return d


def scripts_dir_for(project_dir, version: Optional[str] = None) -> Path:
    """返回指定版本的 scripts 目录（自动创建）。"""
    project_dir = Path(project_dir)
    version = version or resolve_version(project_dir)
    d = project_dir / "scripts" / version
    d.mkdir(parents=True, exist_ok=True)
    return d


def available_versions(project_dir) -> list:
    """列出已存在的版本目录。"""
    project_dir = Path(project_dir)
    # 优先查找 scripts 目录
    sd = project_dir / "scripts"
    if sd.exists():
        return sorted([d.name for d in sd.iterdir() if d.is_dir()])
    # 回退到 flows 目录（向后兼容）
    fd = project_dir / "flows"
    if not fd.exists():
        return []
    return sorted([d.name for d in fd.iterdir() if d.is_dir()])
