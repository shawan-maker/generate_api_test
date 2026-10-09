"""
discover_navigation.py — 导航自动发现 + 对比 + 交互选择

职责：
  1. 调用 nav_discovery.py 爬取菜单树
  2. 与已有脚本对比，标记状态（新模块 / 已有脚本）
  3. 交互式选择要处理的模块列表
  4. 自动生成 modules.yaml（供 --all-modules 使用）

入口：run.py --discover 模式调用
"""

import sys
import logging
from pathlib import Path

from core.discovery.nav_discovery import crawl_menu_tree, save_discovery_result, load_discovery_result

LOG = logging.getLogger("discover_navigation")


async def discover_modules(page, context, base_url, login_url, profile, project_dir: Path, force=False) -> list:
    """
    登录后，展开侧边栏菜单，提取所有叶子菜单项的 模块名+URL。

    流程：
      1. 检查缓存（workspace/<project>/kb/navigation_discovered.json）
      2. 如果缓存有效且非 force → 直接返回
      3. 展开所有菜单 → 逐个点击叶子项 → 记录 URL
      4. 保存结果到缓存

    Args:
        page: Playwright Page
        context: Playwright BrowserContext
        base_url: 基础 URL
        login_url: 登录页 URL（已登录，不需要再登）
        profile: profile 配置
        project_dir: 项目目录路径（如 projects/ecm-compute）
        force: 强制重新发现（忽略缓存）

    Returns:
        [{"label": "角色管理", "url": "...", "group": "用户中心", "level": 2}, ...]
    """
    # 缓存路径
    from core.discovery.path_mapper import get_workspace_dir
    workspace_dir = get_workspace_dir(project_dir)

    # 检查缓存
    if not force:
        cached = load_discovery_result(workspace_dir, max_age_days=7)
        if cached:
            LOG.info(f"  使用缓存的导航发现结果（{len(cached)} 个模块，{workspace_dir / 'kb' / 'navigation_discovered.json'}）")
            # 缓存命中时也检查 modules.yaml 是否存在
            _generate_modules_yaml(cached, project_dir, base_url)
            return cached

    # 实际爬取
    LOG.info("  展开侧边栏菜单...")
    discovered = await crawl_menu_tree(page, wait_ms=1500)

    # 不再过滤非列表页，返回所有发现的菜单项（管线会自行判断是否有有效内容）
    LOG.info(f"  发现 {len(discovered)} 个菜单项")

    # 保存缓存
    save_discovery_result(discovered, workspace_dir)

    # 自动生成 modules.yaml（仅在文件不存在时）
    _generate_modules_yaml(discovered, project_dir, base_url)

    return discovered


def compare_with_existing(discovered_modules: list, version_dir: Path) -> list:
    """
    对比 v1.0.0/api/ 下已有脚本，标记每个模块的状态。

    返回: 原列表每个元素增加:
      - has_script: bool — 是否已有 API 脚本
      - script_files: list — 已存在的脚本文件名
    """
    api_dir = version_dir / "api"
    result = []

    for module in discovered_modules:
        label = module["label"]

        # 检查是否有对应的 API 脚本
        script_files = []
        if api_dir.exists():
            for f in api_dir.glob(f"*{label}*"):
                script_files.append(f.name)
            # 也检查精确匹配
            exact = api_dir / f"{label}_API测试.py"
            if exact.exists() and exact.name not in script_files:
                script_files.append(exact.name)

        module_copy = dict(module)
        module_copy["has_script"] = len(script_files) > 0
        module_copy["script_files"] = script_files
        result.append(module_copy)

    return result


def interactive_select(modules_with_status: list) -> list:
    """
    展示模块列表 + 状态，交互让用户选择。

    三种选择模式：
      1 - 指定模块（输入编号，逗号分隔）
      2 - 仅新模块
      3 - 全部模块（含重新发现）

    Returns:
        选中的模块列表（与输入格式相同）
    """
    if not modules_with_status:
        LOG.warning("  未发现任何有效模块")
        return []

    # 展示列表
    print()
    print("=" * 70)
    print("  发现的模块列表")
    print("=" * 70)
    print(f"  {'编号':<6} {'模块名称':<20} {'状态':<10}")
    print("  " + "-" * 60)

    for i, m in enumerate(modules_with_status, 1):
        label = m["label"]
        group = m.get("group", "")
        status = "✅ 已有" if m.get("has_script") else "🆕 新模块"
        print(f"  [{i}]   {label:<18} {status}")

    print()
    print("请选择要处理的模块:")
    print("  [1] 指定模块（输入编号，如: 1,3,5）")
    print("  [2] 仅新模块（排除已有脚本的）")
    print("  [3] 全部模块（含重新发现）")

    try:
        choice = input("请输入选择 [2]: ").strip() or "2"
    except (EOFError, KeyboardInterrupt):
        choice = "2"
        print()

    selected = []

    if choice == "1":
        # 指定模块
        try:
            nums_str = input("请输入模块编号（逗号分隔）: ").strip()
        except (EOFError, KeyboardInterrupt):
            nums_str = ""
            print()

        if not nums_str:
            LOG.warning("  未输入编号，返回空列表")
            return []

        nums = []
        for part in nums_str.replace("，", ",").split(","):
            part = part.strip()
            if part.isdigit():
                n = int(part)
                if 1 <= n <= len(modules_with_status):
                    nums.append(n)
                else:
                    LOG.warning(f"  编号 {n} 超出范围（1-{len(modules_with_status)}）")

        selected = [modules_with_status[n - 1] for n in sorted(set(nums))]

    elif choice == "2":
        # 仅新模块
        selected = [m for m in modules_with_status if not m.get("has_script")]
        if not selected:
            LOG.info("  所有模块已有脚本，无新模块需要处理")
        else:
            LOG.info(f"  选择了 {len(selected)} 个新模块")

    elif choice == "3":
        # 全部
        selected = list(modules_with_status)
        LOG.info(f"  选择了全部 {len(selected)} 个模块")

    else:
        LOG.warning(f"  无效选择: {choice}，默认选择新模块")
        selected = [m for m in modules_with_status if not m.get("has_script")]

    return selected



def _extract_top_group(raw_group: str) -> str:
    """从 group 面包屑字符串提取一级菜单名称。

    Examples:
        "项目管理 > 配额管理" → "项目管理"
        "访问控制" → "访问控制"
        "" → "未分类"
    """
    if not raw_group:
        return "未分类"
    return raw_group.split(" > ")[0].strip() or "未分类"


def sanitize_group_name(name: str) -> str:
    """清理分组名称，确保可安全用作目录名。

    替换文件系统不允许的字符为下划线。
    """
    import re
    # 替换 Windows/Linux 文件系统非法字符
    sanitized = re.sub(r'[/\\:*?"<>|]', '_', name)
    # 去除首尾空白
    return sanitized.strip() or "未分类"


def _generate_modules_yaml(discovered: list, project_dir: Path, base_url: str):
    """从导航发现结果自动生成 modules.yaml。

    仅在 modules.yaml 不存在时生成，不覆盖用户手动编辑的内容。

    Args:
        discovered: 导航发现结果 [{"label": ..., "url": ..., "group": ...}, ...]
        project_dir: 项目目录
        base_url: 基础 URL（用于将完整 URL 转为相对路径）
    """
    modules_yaml_path = project_dir / "modules.yaml"
    if modules_yaml_path.exists():
        return  # 不覆盖已有文件

    try:
        import yaml
    except ImportError:
        LOG.warning("PyYAML 未安装，无法生成 modules.yaml")
        return

    modules = []
    base = base_url.rstrip("/")

    for item in discovered:
        full_url = item.get("url", "")
        # 将完整 URL 转为相对路径（去掉 base_url 前缀）
        if full_url.startswith(base):
            relative_url = full_url[len(base):]
        elif full_url.startswith("http"):
            # 尝试提取路径部分
            from urllib.parse import urlparse
            parsed = urlparse(full_url)
            relative_url = parsed.path
        else:
            relative_url = full_url

        # 提取一级菜单作为 group
        raw_group = item.get("group", "")
        top_group = sanitize_group_name(_extract_top_group(raw_group))

        # group 原始值转为 tags（保留完整面包屑）
        tags = [raw_group] if raw_group else []

        modules.append({
            "name": item["label"],
            "url": relative_url,
            "enabled": True,
            "group": top_group,
            "tags": tags,
        })

    data = {"modules": modules}
    modules_yaml_path.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8"
    )
    LOG.info(f"  已生成 modules.yaml: {modules_yaml_path}（{len(modules)} 个模块）")


def export_modules_from_cache(project_dir: Path, workspace_dir: Path, base_url: str) -> bool:
    """从 navigation_discovered.json 缓存生成 modules.yaml。

    供 --export-modules 参数调用。

    Returns:
        True = 成功生成，False = 缓存不存在或 modules.yaml 已存在
    """
    modules_yaml_path = project_dir / "modules.yaml"
    if modules_yaml_path.exists():
        LOG.info(f"modules.yaml 已存在，跳过生成: {modules_yaml_path}")
        LOG.info("  如需重新生成，请先手动删除该文件")
        return False

    cached = load_discovery_result(workspace_dir, max_age_days=30)
    if not cached:
        LOG.error(f"导航发现缓存不存在或已过期: {workspace_dir / 'kb' / 'navigation_discovered.json'}")
        LOG.info("  请先运行 --discover-only 探测菜单")
        return False

    _generate_modules_yaml(cached, project_dir, base_url)
    return True


def format_discovery_result(modules_with_status: list) -> str:
    """
    格式化发现结果为 JSON 字符串（供 AI 客户端解析）。

    Args:
        modules_with_status: 带 has_script 状态的模块列表

    Returns:
        JSON 字符串
    """
    import json

    result = {
        "total": len(modules_with_status),
        "new_count": sum(1 for m in modules_with_status if not m.get("has_script")),
        "existing_count": sum(1 for m in modules_with_status if m.get("has_script")),
        "modules": [
            {
                "id": i + 1,
                "label": m["label"],
                "group": m.get("group", ""),
                "url": m["url"],
                "level": m.get("level", 1),
                "has_script": m.get("has_script", False),
                "script_files": m.get("script_files", [])
            }
            for i, m in enumerate(modules_with_status)
        ]
    }

    return json.dumps(result, ensure_ascii=False, indent=2)
