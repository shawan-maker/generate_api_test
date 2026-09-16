"""
menu_tools.py — 菜单树工具。提供从 menu_tree.json 到两级目录的映射，
供 gen_api_layer.py / gen_pytest.py 使用。

estack 菜单结构: 一级 = 分组(group)，二级 = 功能项(label)
例如:
  访问控制/用户管理
  访问控制/角色管理
  账户管理/AccessKey设置
  容量管理/资源池容量
"""
import json
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]


def load_menu_tree(proj_dir: str) -> list:
    """加载 menu_tree.json。"""
    p = Path(proj_dir) / "kb" / "menu_tree.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def _keyword_match(menu_label: str, resource: str) -> bool:
    """
    菜单项名 ↔ resource 关键词匹配（忽略大小写）。
    如 "用户管理" ↔ "users"、"AccessKey设置" ↔ "accesskey"、"角色管理" ↔ "roles"
    """
    # 中文菜单名 → 英文 resource 的常规模糊映射
    mappings = {
        "账户": "account",
        "密码": "password",
        "accesskey": "accesskey", "ak": "accesskey",
        "key": "accesskey",
        "单位": "tenant",
        "代维": "maintain",
        "部门": "group",
        "用户组": "group",
        "用户": "user",
        "角色": "role",
        "授权": "authority",
        "黑白名单": "blacklist",
        "项目": "project",
        "配额": "quota",
        "容量": "capacity",
        "资源": "resource",
        "日志": "log",
        "告警": "alarm",
        "预测": "forecast",
        "公告": "notice",
        "菜单": "menu",
        "主题": "theme",
        "产品": "product",
        "云主机": "ecs", "ecs": "ecs",
        "规格": "flavor",
        "镜像": "image",
        "网络": "network",
        "vpc": "vpc",
        "安全": "security",
        "硬盘": "volume",
        "备份": "backup",
        "弹性伸缩": "auto_scaling",
        "启动模板": "launch_template",
        "ssh": "ssh_key",
        "密钥": "ssh_key",
    }

    ml = menu_label.lower().replace(" ", "").replace("-", "")
    rl = resource.lower().replace(" ", "").replace("-", "").replace("_", "")

    # 直接匹配
    if rl in ml or ml in rl:
        return True

    # 中文映射
    for cn, en in mappings.items():
        if cn in ml or cn in menu_label:
            if rl in en or en in rl:
                return True

    return False


def _build_resource_groups(proj_dir: str, catalog: dict) -> dict:
    """
    构建 {resource_key: {"group": "一级", "label": "二级", "resource": "..."}} 的映射。

    策略:
      1. 遍历 catalog 每个 endpoint 的 x-kb-resource
      2. 在 menu_tree 中找最佳匹配的菜单项
      3. 未匹配的归入"未归类"分组
    """
    menu = load_menu_tree(proj_dir)

    # 从 menu_tree 提取所有叶子节点(非 group)
    menu_items = [m for m in menu if not m.get("is_group")]

    resource_groups = {}
    matched_resources = set()

    # 遍历 catalog 中的 resource
    for p, methods in catalog.get("paths", {}).items():
        for method, ep in methods.items():
            resource = ep.get("x-kb-resource") or ""
            if not resource:
                continue
            key = f"{ep.get('x-kb-service', '')}/{resource}"
            if key in resource_groups:
                continue

            # 在菜单项中找最佳匹配
            best_match = None
            for item in menu_items:
                label = item.get("label", "")
                if _keyword_match(label, resource):
                    best_match = item
                    break

            if best_match:
                group = best_match.get("group") or "默认"
                label = best_match.get("label", "")
                resource_groups[key] = {
                    "group": group,
                    "label": label,
                    "resource": resource,
                }
                matched_resources.add(key)
            else:
                resource_groups[key] = {
                    "group": "未归类",
                    "label": resource,
                    "resource": resource,
                }

    return resource_groups


def get_resource_menu_mapping(proj_dir: str, catalog: dict) -> dict:
    """
    对外接口: 返回 {group_name: {label_name: [resource_keys]}} 的两级目录结构。
    例如:
    {
      "访问控制": {"用户管理": ["draco/users"], "角色管理": ["draco/roles"]},
      "账户管理": {"AccessKey设置": ["draco/accesskey"]},
      "未归类": {"policies": ["draco/policies"]},
    }
    """
    rg = _build_resource_groups(proj_dir, catalog)

    # 反转: group → label → [resource_keys]
    tree = {}
    for key, info in rg.items():
        g = info["group"]
        lbl = info["label"]
        tree.setdefault(g, {})
        tree[g].setdefault(lbl, [])
        tree[g][lbl].append(key)

    return tree


def get_resource_dir(proj_dir: str, catalog: dict, resource_key: str) -> str:
    """
    获取 resource 的二级目录路径: "访问控制/用户管理"
    用于 api/ 和 tests/ 目录结构。
    """
    rg = _build_resource_groups(proj_dir, catalog)
    info = rg.get(resource_key, {})
    if info.get("group") and info.get("group") != "未归类":
        return f"{info['group']}/{info['label']}"
    return "未归类"


if __name__ == "__main__":
    # 测试 estack
    catalog = json.load(open(ROOT / "projects" / "estack" / "kb" / "api_catalog.json"))
    tree = get_resource_menu_mapping(str(ROOT / "projects" / "estack"), catalog)
    print("=== estack 两级目录结构 ===")
    for group, labels in sorted(tree.items()):
        print(f"\n📁 {group}")
        for label, resources in sorted(labels.items()):
            print(f"  📄 {label} ({', '.join(resources)})")

    # 测试 ecm-compute
    print("\n\n=== ecm-compute 两级目录结构 ===")
    catalog2 = json.load(open(ROOT / "projects" / "ecm-compute" / "kb" / "api_catalog.json"))
    tree2 = get_resource_menu_mapping(str(ROOT / "projects" / "ecm-compute"), catalog2)
    for group, labels in sorted(tree2.items()):
        print(f"\n📁 {group}")
        for label, resources in sorted(labels.items()):
            print(f"  📄 {label} ({', '.join(resources)})")
