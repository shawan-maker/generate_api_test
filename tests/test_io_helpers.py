"""
test_io_helpers.py — io_helpers 模块单元测试

测试目标:
- load_modules_yaml 双格式解析（三级 products / 平铺 modules）
- _dedupe_module_names 重名全局消歧（产品-模块复合、同产品二次 URL slug）
"""
import pytest
from pathlib import Path

from core.discovery.io_helpers import load_modules_yaml, _dedupe_module_names


# ============================================================
# _dedupe_module_names 消歧测试
# ============================================================

class TestDedupeModuleNames:
    """重名模块全局消歧规则测试。"""

    def test_unique_names_unchanged(self):
        """全局唯一 name 保持原样。"""
        modules = [
            {"name": "用户管理", "group": "访问控制", "url": "/users"},
            {"name": "角色管理", "group": "访问控制", "url": "/roles"},
        ]
        result = _dedupe_module_names(modules)
        assert [m["name"] for m in result] == ["用户管理", "角色管理"]

    def test_cross_product_dup_composite_name(self):
        """跨产品同名 → 产品-模块复合名。"""
        modules = [
            {"name": "概览", "group": "云主机", "url": "/vmOverview"},
            {"name": "概览", "group": "容器", "url": "/podsOverview"},
        ]
        result = _dedupe_module_names(modules)
        names = [m["name"] for m in result]
        assert names == ["云主机-概览", "容器-概览"]
        assert len(set(names)) == 2

    def test_same_product_dup_url_slug(self):
        """同产品内同名 → 复合后仍重名，追加 URL slug。"""
        modules = [
            {"name": "裸金属", "group": "裸金属服务器", "url": "/page/eis/baremetal"},
            {"name": "裸金属", "group": "裸金属服务器", "url": "/page/eis/BMServer"},
        ]
        result = _dedupe_module_names(modules)
        names = [m["name"] for m in result]
        assert names == ["裸金属服务器-裸金属-baremetal", "裸金属服务器-裸金属-BMServer"]

    def test_hash_url_slug(self):
        """hash 路由 URL 的 slug 取 # 后段。"""
        modules = [
            {"name": "资源池", "group": "安全", "url": "/page/vcloud/#/resourcepool"},
            {"name": "资源池", "group": "安全", "url": "/page/vcloud/#/pool2"},
        ]
        result = _dedupe_module_names(modules)
        names = [m["name"] for m in result]
        assert names == ["安全-资源池-resourcepool", "安全-资源池-pool2"]

    def test_mixed_single_and_dup(self):
        """重名与唯一名混合：唯一名不动，重名复合。"""
        modules = [
            {"name": "概览", "group": "云主机", "url": "/vmOverview"},
            {"name": "SSH密钥", "group": "云主机", "url": "/ssh"},
            {"name": "概览", "group": "容器", "url": "/podsOverview"},
        ]
        result = _dedupe_module_names(modules)
        by_url = {m["url"]: m["name"] for m in result}
        assert by_url["/vmOverview"] == "云主机-概览"
        assert by_url["/ssh"] == "SSH密钥"
        assert by_url["/podsOverview"] == "容器-概览"

    def test_not_mutate_input(self):
        """消歧不修改输入列表。"""
        modules = [
            {"name": "概览", "group": "云主机", "url": "/vmOverview"},
            {"name": "概览", "group": "容器", "url": "/podsOverview"},
        ]
        _dedupe_module_names(modules)
        assert modules[0]["name"] == "概览"


# ============================================================
# load_modules_yaml 双格式解析测试
# ============================================================

THREE_LEVEL_YAML = """
products:
- name: 云主机
  modules:
  - name: 概览
    url: /page/eis/vmOverview
    enabled: true
  - name: 回收站
    url: /page/eis/vmRecycle
    enabled: true
- name: 容器
  modules:
  - name: 概览
    url: /page/eis/podsOverview
    enabled: true
  - name: 禁用模块
    url: /page/eis/disabled
    enabled: false
"""

FLAT_YAML = """
modules:
- name: 用户管理
  url: /users
  enabled: true
  group: 账户管理
  tags:
  - 账户管理
"""


class TestLoadModulesYaml:
    """modules.yaml 双格式解析测试。"""

    def test_three_level_format(self, tmp_path):
        """三级格式：products 摊平注入 group=产品名，tags 合并。"""
        (tmp_path / "modules.yaml").write_text(THREE_LEVEL_YAML, encoding="utf-8")
        mods = load_modules_yaml(tmp_path)
        # enabled=false 被过滤
        assert len(mods) == 3
        by_url = {m["url"]: m for m in mods}
        assert by_url["/page/eis/vmOverview"]["group"] == "云主机"
        assert "云主机" in by_url["/page/eis/vmOverview"]["tags"]
        assert by_url["/page/eis/podsOverview"]["group"] == "容器"

    def test_three_level_dedup_applied(self, tmp_path):
        """三级格式跨产品同名 → 消歧为产品-模块复合名。"""
        (tmp_path / "modules.yaml").write_text(THREE_LEVEL_YAML, encoding="utf-8")
        mods = load_modules_yaml(tmp_path)
        names = [m["name"] for m in mods]
        assert len(names) == len(set(names))  # 全局唯一
        assert "云主机-概览" in names
        assert "容器-概览" in names

    def test_flat_format_backward_compatible(self, tmp_path):
        """平铺格式向后兼容（estack 等旧项目）。"""
        (tmp_path / "modules.yaml").write_text(FLAT_YAML, encoding="utf-8")
        mods = load_modules_yaml(tmp_path)
        assert len(mods) == 1
        assert mods[0]["name"] == "用户管理"
        assert mods[0]["group"] == "账户管理"

    def test_missing_file_returns_empty(self, tmp_path):
        """modules.yaml 不存在 → 空列表（含错误日志）。"""
        mods = load_modules_yaml(tmp_path)
        assert mods == []
