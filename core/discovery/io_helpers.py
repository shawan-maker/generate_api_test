"""
io_helpers.py — 项目 I/O 辅助函数

从 run.py 提取的纯 I/O 函数，无副作用，可被多处复用。
"""

import json
import re
import time
import logging
from pathlib import Path

from core.discovery.path_mapper import get_workspace_dir
from lib.utils import safe_read_json, safe_write_json

LOG = logging.getLogger("io_helpers")


def load_profile(project_dir: Path) -> dict:
    """加载项目的 profile.yaml，并合并全局凭据库。

    凭据优先级：credentials.yaml (按 base_url 匹配) > 环境变量 > profile.yaml

    credentials.yaml 格式（按 base_url 组织）：
        sites:
          "https://10.151.61.248":
            label: "EcsCloud/estack"
            username: estack-yy
            password: R@9eDuck$!mpleM00n
            auth: { header_name: Authorization, ... }
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

    # 2. 加载全局凭据库并合并（按 base_url 匹配）
    try:
        framework_root = Path(__file__).resolve().parents[2]  # core/discovery/ -> framework root
        creds_file = framework_root / "config" / "credentials.yaml"

        if creds_file.exists():
            all_creds = yaml.safe_load(creds_file.read_text(encoding="utf-8")) or {}
            sites = all_creds.get("sites", {})
            base_url = profile.get("base_url", "")

            site_creds = sites.get(base_url, {})
            if site_creds:
                LOG.debug(f"已加载全局凭据: {base_url}")

                # 合并 auth 配置（深度合并，credentials.yaml 优先）
                if "auth" in site_creds:
                    profile.setdefault("auth", {}).update(site_creds["auth"])

                # 合并凭据到 profile.credentials（兼容旧代码）
                profile.setdefault("credentials", {})
                for k in ("username", "password"):
                    if k in site_creds:
                        profile["credentials"][k] = site_creds[k]

                # 合并 URL 字段（如 credentials.yaml 中有不同的 login_url）
                for k in ("base_url", "login_url"):
                    if k in site_creds:
                        profile[k] = site_creds[k]
    except Exception as e:
        LOG.warning(f"加载全局凭据库失败: {e}")

    return profile


def save_credentials(project_id: str, credentials: dict):
    """将项目凭据保存到全局凭据库（credentials.yaml）。

    按 base_url 组织到 sites 下：
        sites:
          "https://10.151.61.248":
            username: estack-yy
            password: ...
            auth: { ... }

    Args:
        project_id: 项目 ID（用作 label，向后兼容）
        credentials: 凭据字典，必须包含 base_url，可选 username, password, auth, login_url
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

        # 确保 sites 字典存在
        sites = all_creds.setdefault("sites", {})

        # 按 base_url 写入
        base_url = credentials.get("base_url", "")
        if not base_url:
            LOG.warning(f"save_credentials: 缺少 base_url，无法保存")
            return

        if base_url not in sites:
            sites[base_url] = {}

        # 合并凭据（不覆盖 base_url 本身）
        for k, v in credentials.items():
            if k == "base_url":
                continue
            if k == "auth" and isinstance(v, dict) and isinstance(sites[base_url].get("auth"), dict):
                # auth 深度合并
                sites[base_url]["auth"].update(v)
            else:
                sites[base_url][k] = v

        # 确保 label 字段存在
        if "label" not in sites[base_url]:
            sites[base_url]["label"] = project_id

        # 确保目录存在
        creds_file.parent.mkdir(parents=True, exist_ok=True)

        # 写入文件
        creds_file.write_text(
            yaml.dump(all_creds, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8"
        )

        LOG.info(f"✅ 凭据已保存到全局凭据库: {base_url}")
    except Exception as e:
        LOG.warning(f"保存凭据失败: {e}")


def load_ui_result(project_dir: Path, module_name: str) -> dict:
    """加载已保存的 UI 探测结果。"""
    ws = get_workspace_dir(project_dir)
    p = ws / "kb" / "module_discovered" / f"{module_name}_ui.json"
    return safe_read_json(p, default={})


def load_capture_result(project_dir: Path, module_name: str) -> dict:
    """加载已保存的 API 捕获结果。"""
    ws = get_workspace_dir(project_dir)
    p = ws / "kb" / "module_discovered" / f"{module_name}_capture.json"
    return safe_read_json(p, default={})


def save_json(data: dict, path: Path):
    """将数据保存为 JSON 文件（使用安全写入，防 EPERM）。"""
    safe_write_json(path, data, ensure_ascii=False, indent=2)


def load_modules_yaml(project_dir: Path) -> list:
    """加载 modules.yaml 模块清单。返回模块列表，按 priority 排序。

    每个模块保证包含 group 字段（兼容旧格式：从 tags 提取或默认 "未分类"）。
    """
    modules_file = project_dir / "modules.yaml"
    if not modules_file.exists():
        LOG.error(f"模块清单不存在: {modules_file}")
        LOG.info("请先运行导航发现生成 modules.yaml：")
        LOG.info("  python -m core.discovery.run --project <项目名> --discover-only --home-url <URL> --headless")
        LOG.info("  python -m core.discovery.run --project <项目名> --export-modules")
        return []

    try:
        import yaml
        data = yaml.safe_load(modules_file.read_text(encoding="utf-8")) or {}
        modules = data.get("modules", [])

        # 过滤 enabled=false 的模块
        modules = [m for m in modules if m.get("enabled", True)]

        # 确保每个模块有 group 字段（兼容旧格式）
        for m in modules:
            if "group" not in m or not m["group"]:
                # 从 tags 中提取一级菜单，或默认 "未分类"
                tags = m.get("tags", [])
                if tags and isinstance(tags[0], str):
                    raw = tags[0]
                    m["group"] = raw.split(" > ")[0].strip() if raw else "未分类"
                else:
                    m["group"] = "未分类"

        # 按 priority 排序（默认 100）
        modules.sort(key=lambda m: m.get("priority", 100))

        LOG.info(f"已加载 {len(modules)} 个模块（来自 {modules_file}）")
        return modules
    except Exception as e:
        LOG.error(f"加载 modules.yaml 失败: {e}")
        return []


def _scan_indexed_dirs(base_dir: Path) -> dict:
    """扫描目录下已编号的子目录/文件，返回 {name: index} 映射。

    支持两种格式：
    - 目录: {idx}_{name}/
    - 文件: {idx}_{name}_API测试.py 或 {idx}_{name}.py

    Args:
        base_dir: 要扫描的目录（如 api/访问控制/ 或 api/）

    Returns:
        {name: index_str} 映射，如 {"用户管理": "03", "角色管理": "05"}
    """
    mapping = {}
    if not base_dir.exists():
        return mapping

    for item in base_dir.iterdir():
        name_part = item.stem if item.is_file() else item.name
        m = re.match(r'^(\d{2})_(.+?)(?:_API测试)?$', name_part)
        if m:
            idx_str = m.group(1)
            item_name = m.group(2)
            # 后出现的覆盖先出现的（取最新）
            mapping[item_name] = idx_str

    return mapping


def _find_group_dirs(base_dir: Path, group: str) -> list:
    """查找 group 对应的所有目录（新旧格式都查）。

    新格式: {idx}_{group}/  (如 01_访问控制/)
    旧格式: {group}/        (如 访问控制/)

    Returns:
        匹配的目录路径列表
    """
    dirs = []
    if not base_dir.exists():
        return dirs

    for d in base_dir.iterdir():
        if not d.is_dir():
            continue
        # 精确匹配：新格式 "{idx}_{group}" 或 旧格式 "{group}"
        if d.name == group:
            dirs.append(d)
        else:
            m = re.match(r'^\d{2}_(.+)$', d.name)
            if m and m.group(1) == group:
                dirs.append(d)

    return dirs


def get_or_assign_module_index(project_dir: Path, version: str, group: str,
                                module_name: str, side: str = "api") -> str:
    """获取或分配模块的稳定编号。

    策略：
    1. 扫描 {project_dir}/{version}/{side}/ 下所有匹配 group 的目录
       （同时查找新格式 "{idx}_{group}" 和旧格式 "{group}"）
    2. 如果该模块已有编号 → 返回已有编号（稳定）
    3. 如果是新模块 → 取当前最大编号 + 1
    4. 如果目录不存在 → 返回 "01"

    优先从 api 侧读取编号（api 脚本总是先生成），
    确保 API 和 UI 编号一致。

    Args:
        project_dir: 项目目录
        version: 版本号（如 "v1.0.0"）
        group: 分组名（如 "访问控制"，不含编号前缀）
        module_name: 模块名（如 "用户管理"）
        side: "api" 或 "ui"，指定从哪侧扫描

    Returns:
        两位数字编号（如 "01", "02"）
    """
    version = version or "v1.0.0"

    def _scan_all_sides(base_side: str) -> dict:
        """扫描指定 side 下所有匹配 group 的目录"""
        type_dir = project_dir / version / base_side
        result = {}
        for d in _find_group_dirs(type_dir, group):
            scanned = _scan_indexed_dirs(d)
            for k, v in scanned.items():
                if k not in result:
                    result[k] = v
        return result

    # 优先从 api 侧读取
    existing = _scan_all_sides("api")

    # 补充 ui 侧
    if module_name not in existing:
        for k, v in _scan_all_sides("ui").items():
            if k not in existing:
                existing[k] = v

    # 补充 export 侧
    for k, v in _scan_all_sides("export").items():
        if k not in existing:
            existing[k] = v

    # 1. 已有编号 → 直接返回
    if module_name in existing:
        return existing[module_name]

    # 2. 新模块 → 取最大编号 + 1
    if existing:
        max_idx = max(int(v) for v in existing.values())
        return f"{max_idx + 1:02d}"

    return "01"


def get_or_assign_group_index(project_dir: Path, version: str,
                               group: str, side: str = "api") -> str:
    """获取或分配 group（一级目录）的稳定编号。

    扫描 {project_dir}/{version}/ 下所有 side（api/ui/export）的子目录，
    已有编号则返回，否则取最大编号 + 1。
    同时检查新格式 "{idx}_{group}" 和旧格式 "{group}"。

    Args:
        project_dir: 项目目录
        version: 版本号
        group: 分组名（如 "访问控制"）
        side: "api" 或 "ui"（作为主要扫描方向）

    Returns:
        两位数字编号（如 "01", "02"）
    """
    version = version or "v1.0.0"
    _RESERVED = {"lib", "config", "reports", "__pycache__", "_summary"}

    group_mapping = {}  # {group_name: index_str or None}

    # 扫描所有 side 目录（api, ui, export）取并集
    for scan_side in ["api", "ui", "export"]:
        type_dir = project_dir / version / scan_side
        if not type_dir.exists():
            continue

        for d in type_dir.iterdir():
            if not d.is_dir() or d.name in _RESERVED:
                continue
            m = re.match(r'^(\d{2})_(.+)$', d.name)
            if m:
                g_name = m.group(2)
                g_idx = m.group(1)
                # 已有编号的优先
                if g_name not in group_mapping or group_mapping[g_name] is None:
                    group_mapping[g_name] = g_idx
            else:
                if d.name not in _RESERVED:
                    group_mapping.setdefault(d.name, None)

    # 已有编号 → 返回
    if group in group_mapping and group_mapping[group] is not None:
        return group_mapping[group]

    # 新 group → 取最大编号 + 1
    numbered = [int(v) for v in group_mapping.values() if v is not None]
    if numbered:
        return f"{max(numbered) + 1:02d}"

    return "01"


def get_module_group_index(project_dir: Path, module_name: str) -> str:
    """获取模块在其分组内的编号（01, 02, 03...）。

    已废弃：请优先使用 get_or_assign_module_index()。
    此函数保留向后兼容，内部代理到新函数。

    Args:
        project_dir: 项目目录
        module_name: 模块名称

    Returns:
        两位数字编号（如 "01", "02"），找不到时返回 "01"
    """
    modules = load_modules_yaml(project_dir)
    if not modules:
        return "01"

    # 找到目标模块的 group
    target_group = None
    for m in modules:
        if m.get("name") == module_name:
            target_group = m.get("group")
            break

    if not target_group:
        return "01"

    # 尝试从文件系统获取稳定编号
    from core.discovery import version as ver_mod
    version = ver_mod.resolve_version(project_dir)
    return get_or_assign_module_index(project_dir, version, target_group, module_name)


def needs_rediscovery(project_dir: Path, module_name: str, module_url: str,
                      force: bool = False) -> bool:
    """检查模块是否需要重新发现（增量逻辑）。

    检查 _manifest.json（Stage 3-4 的最终产物）是否存在且有效。
    有 manifest 说明全流程已完成，无需重跑。

    Returns:
        True = 需要重新发现
        False = 可以跳过（已有结果且 URL 未变更）
    """
    if force:
        return True

    ws = get_workspace_dir(project_dir)

    # 检查 manifest（Stage 3-4 最终产物）
    manifest_file = ws / "kb" / "module_discovered" / f"{module_name}_manifest.json"
    data = safe_read_json(manifest_file, default=None)
    if data is None:
        return True

    try:
        # manifest 中 module.url 存的是相对路径或完整 URL
        module_info = data.get("module", {})
        old_url = module_info.get("url", "")

        # URL 变更则重新发现（兼容相对路径和完整 URL）
        if old_url and old_url != module_url:
            # 比较时去掉 base_url 前缀（manifest 可能存相对路径）
            if not module_url.endswith(old_url) and not old_url.endswith(module_url.split("/")[-1]):
                LOG.info(f"  URL 已变更: {old_url} → {module_url}")
                return True

        # 检查文件 mtime（超过 7 天则重新发现）
        import os
        mtime = os.path.getmtime(manifest_file)
        age_days = (time.time() - mtime) / 86400
        if age_days > 7:
            LOG.info(f"  发现结果已过期（{age_days:.1f} 天前）")
            return True

        return False
    except Exception as e:
        LOG.warning(f"  检查发现结果失败: {e}")
        return True
