"""
io_helpers.py — 项目 I/O 辅助函数

从 run.py 提取的纯 I/O 函数，无副作用，可被多处复用。
"""

import json
import time
import logging
from pathlib import Path

from core.discovery.path_mapper import get_workspace_dir

LOG = logging.getLogger("io_helpers")


def load_profile(project_dir: Path) -> dict:
    """加载项目的 profile.yaml，并合并全局凭据库。

    优先级：全局凭据库 (config/credentials.yaml) > 项目 profile.yaml
    """
    try:
        import yaml
    except ImportError:
        return {}

    # 1. 加载项目级 profile.yaml
    p = project_dir / "profile.yaml"
    profile = {}
    if p.exists():
        profile = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    # 2. 加载全局凭据库并合并
    try:
        framework_root = Path(__file__).resolve().parents[2]  # core/discovery/ -> framework root
        creds_file = framework_root / "config" / "credentials.yaml"

        if creds_file.exists():
            all_creds = yaml.safe_load(creds_file.read_text(encoding="utf-8")) or {}
            project_id = project_dir.name  # e.g. "ecm-compute"

            if project_id in all_creds:
                proj_creds = all_creds[project_id]
                LOG.debug(f"已加载全局凭据: {project_id}")

                # 合并凭据（全局优先）
                for key in ["base_url", "login_url", "username", "password", "auth"]:
                    if key in proj_creds:
                        if key == "auth" and "auth" in profile:
                            # auth 字典深度合并
                            profile["auth"].update(proj_creds["auth"])
                        else:
                            profile[key] = proj_creds[key]

                # 确保 credentials 字段存在（兼容旧代码）
                if "credentials" not in profile:
                    profile["credentials"] = {}
                if "username" in proj_creds:
                    profile["credentials"]["username"] = proj_creds["username"]
                if "password" in proj_creds:
                    profile["credentials"]["password"] = proj_creds["password"]
    except Exception as e:
        LOG.warning(f"加载全局凭据库失败: {e}")

    return profile


def save_credentials(project_id: str, credentials: dict):
    """将项目凭据保存到全局凭据库。

    Args:
        project_id: 项目 ID (如 "ecm-compute")
        credentials: 凭据字典，包含 username, password, base_url, login_url, auth 等
    """
    try:
        import yaml
    except ImportError:
        LOG.warning("PyYAML 未安装，无法保存凭据")
        return

    try:
        framework_root = Path(__file__).resolve().parents[2]
        creds_file = framework_root / "config" / "credentials.yaml"

        # 加载现有凭据
        all_creds = {}
        if creds_file.exists():
            all_creds = yaml.safe_load(creds_file.read_text(encoding="utf-8")) or {}

        # 合并新项目凭据
        if project_id not in all_creds:
            all_creds[project_id] = {}

        all_creds[project_id].update(credentials)

        # 确保目录存在
        creds_file.parent.mkdir(parents=True, exist_ok=True)

        # 写入文件
        creds_file.write_text(
            yaml.dump(all_creds, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8"
        )

        LOG.info(f"✅ 凭据已保存到全局凭据库: {project_id}")
    except Exception as e:
        LOG.warning(f"保存凭据失败: {e}")


def load_ui_result(project_dir: Path, module_name: str) -> dict:
    """加载已保存的 UI 探测结果。"""
    ws = get_workspace_dir(project_dir)
    p = ws / "kb" / "module_discovered" / f"{module_name}_ui.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def load_capture_result(project_dir: Path, module_name: str) -> dict:
    """加载已保存的 API 捕获结果。"""
    ws = get_workspace_dir(project_dir)
    p = ws / "kb" / "module_discovered" / f"{module_name}_capture.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def save_json(data: dict, path: Path):
    """将数据保存为 JSON 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_modules_yaml(project_dir: Path) -> list:
    """加载 modules.yaml 模块清单。返回模块列表，按 priority 排序。"""
    modules_file = project_dir / "modules.yaml"
    if not modules_file.exists():
        LOG.error(f"模块清单不存在: {modules_file}")
        LOG.info("请创建 modules.yaml，格式参考 projects/estack/modules.yaml")
        return []

    try:
        import yaml
        data = yaml.safe_load(modules_file.read_text(encoding="utf-8")) or {}
        modules = data.get("modules", [])

        # 过滤 enabled=false 的模块
        modules = [m for m in modules if m.get("enabled", True)]

        # 按 priority 排序（默认 100）
        modules.sort(key=lambda m: m.get("priority", 100))

        LOG.info(f"已加载 {len(modules)} 个模块（来自 {modules_file}）")
        return modules
    except Exception as e:
        LOG.error(f"加载 modules.yaml 失败: {e}")
        return []


def needs_rediscovery(project_dir: Path, module_name: str, module_url: str,
                      force: bool = False) -> bool:
    """检查模块是否需要重新发现（增量逻辑）。

    Returns:
        True = 需要重新发现
        False = 可以跳过（已有结果且 URL 未变更）
    """
    if force:
        return True

    ws = get_workspace_dir(project_dir)
    discovery_file = ws / "kb" / "module_discovered" / f"{module_name}.json"
    if not discovery_file.exists():
        return True

    try:
        data = json.loads(discovery_file.read_text(encoding="utf-8"))
        old_url = data.get("target_url", "")

        # URL 变更则重新发现
        if old_url != module_url:
            LOG.info(f"  URL 已变更: {old_url} → {module_url}")
            return True

        # 检查文件 mtime（超过 7 天则重新发现）
        import os
        mtime = os.path.getmtime(discovery_file)
        age_days = (time.time() - mtime) / 86400
        if age_days > 7:
            LOG.info(f"  发现结果已过期（{age_days:.1f} 天前）")
            return True

        return False
    except Exception as e:
        LOG.warning(f"  检查发现结果失败: {e}")
        return True
