"""
path_mapper.py — 集中管理项目目录 → workspace 目录的路径映射。

设计:
  projects/<project>/     — 项目配置 + 最终脚本（用户可见）
  workspace/<project>/    — 管线运行时数据（中间产物、报告等）

用法:
  from core.discovery.path_mapper import get_workspace_dir, ensure_workspace

  project_dir = Path("projects/ecm-compute")
  workspace_dir = get_workspace_dir(project_dir)
  ensure_workspace(workspace_dir)
"""

from pathlib import Path


def get_workspace_dir(project_dir: Path) -> Path:
    """从 project_dir 推导 workspace 目录。

    projects/ecm-compute  →  workspace/ecm-compute
    """
    project_dir = Path(project_dir)
    return project_dir.parent.parent / "workspace" / project_dir.name


def ensure_workspace(workspace_dir: Path):
    """创建 workspace 子目录结构（幂等）。"""
    workspace_dir = Path(workspace_dir)
    for sub in [
        "kb/module_discovered",
        "captures",
        "exports",
        "output/config",
        "output/debug",
        "reports/api",
        "reports/ui",
    ]:
        (workspace_dir / sub).mkdir(parents=True, exist_ok=True)


def scripts_dir_for(project_dir: Path, version: str) -> Path:
    """返回项目下的版本目录（扁平化，不再嵌套 scripts/）。

    projects/ecm-compute/v1.0.0/
    """
    d = Path(project_dir) / version
    d.mkdir(parents=True, exist_ok=True)
    return d
