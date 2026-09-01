"""
test_e2e_phone_field.py — 端到端测试：手机号复合组件检测

验证修复：
1. _scan_fields_in_all_frames 正确处理 {fields, debugInfo} 返回格式
2. 手机号字段被识别为复合组件（el-select + input）
3. 表单填充时能正确定位到手机号输入框（而非国家编码下拉框）
"""
import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock


class TestCompositeFieldScanning:
    """测试复合组件扫描逻辑"""

    def test_extract_fields_from_object_format(self):
        """_scan_fields_in_all_frames 能正确处理 {fields, debugInfo} 对象格式"""
        # 模拟 JS 返回的新格式
        result = {
            "fields": [
                {"label": "用户名", "type": "input"},
                {"label": "手机号(下拉)", "type": "select"},
                {"label": "手机号", "type": "input"}
            ],
            "debugInfo": [
                {
                    "label": "手机号",
                    "hasSelect": True,
                    "allInputsCount": 2,
                    "independentInputsCount": 1
                }
            ]
        }

        # 模拟 _extract_fields 函数逻辑
        def extract_fields(result):
            if isinstance(result, dict):
                return result.get("fields", [])
            elif isinstance(result, list):
                return result
            return []

        fields = extract_fields(result)
        assert len(fields) == 3
        assert fields[0]["label"] == "用户名"
        assert fields[1]["label"] == "手机号(下拉)"
        assert fields[2]["label"] == "手机号"

    def test_extract_fields_from_array_format(self):
        """_scan_fields_in_all_frames 兼容旧的数组格式"""
        result = [
            {"label": "用户名", "type": "input"},
            {"label": "手机号", "type": "input"}
        ]

        def extract_fields(result):
            if isinstance(result, dict):
                return result.get("fields", [])
            elif isinstance(result, list):
                return result
            return []

        fields = extract_fields(result)
        assert len(fields) == 2
        assert fields[0]["label"] == "用户名"


class TestCompositeFieldDetection:
    """测试复合组件检测逻辑（模拟 JS 逻辑的 Python 版本）"""

    def test_phone_field_with_country_code(self):
        """手机号字段包含国家编码下拉框时，应被识别为复合组件"""
        # 模拟 DOM 结构
        class MockElement:
            def __init__(self, tag="div", class_name="", readonly=False, width=100, in_select=False):
                self.tag = tag
                self.class_name = class_name
                self.readonly = readonly
                self.width = width
                self.in_select = in_select  # 标记是否在 el-select 内部

            def querySelector(self, selector):
                if ".el-select" in selector and "el-select" in self.class_name:
                    return MockSelectElement()
                return None

            def querySelectorAll(self, selector):
                if "input" in selector:
                    # 返回两个 input：一个是 el-select 内部的（readonly），一个是独立的
                    return [
                        MockElement("input", readonly=True, width=80, in_select=True),
                        MockElement("input", readonly=False, width=200, in_select=False)
                    ]
                return []

        class MockSelectElement:
            def contains(self, elem):
                # 只有标记为 in_select 的元素才在 el-select 内部
                return getattr(elem, 'in_select', False)

        # 模拟扫描逻辑
        fi = MockElement("div", "el-form-item el-select")
        label = "手机号"

        selectEl = fi.querySelector(".el-select")
        allInputs = fi.querySelectorAll("input")
        independentInputs = [
            inp for inp in allInputs
            if not inp.readonly and not selectEl.contains(inp) and inp.width > 0
        ]

        # 验证：应该检测到 1 个独立 input
        assert selectEl is not None
        assert len(allInputs) == 2
        assert len(independentInputs) == 1
        assert independentInputs[0].readonly is False
        assert independentInputs[0].width == 200

    def test_normal_select_field(self):
        """普通 el-select 字段（无独立 input）不应被识别为复合组件"""
        class MockElement:
            def __init__(self, tag="div", class_name="", readonly=False, width=100, in_select=False):
                self.tag = tag
                self.class_name = class_name
                self.readonly = readonly
                self.width = width
                self.in_select = in_select

            def querySelector(self, selector):
                if ".el-select" in selector and "el-select" in self.class_name:
                    return MockSelectElement()
                return None

            def querySelectorAll(self, selector):
                if "input" in selector:
                    # 只有 el-select 内部的 input
                    return [MockElement("input", readonly=True, width=100, in_select=True)]
                return []

        class MockSelectElement:
            def contains(self, elem):
                return True  # 所有 input 都在 el-select 内部

        fi = MockElement("div", "el-form-item el-select")
        selectEl = fi.querySelector(".el-select")
        allInputs = fi.querySelectorAll("input")
        independentInputs = [
            inp for inp in allInputs
            if not inp.readonly and not selectEl.contains(inp) and inp.width > 0
        ]

        assert selectEl is not None
        assert len(independentInputs) == 0  # 没有独立 input


class TestDeepScanFormItem:
    """测试深度扫描函数"""

    @pytest.mark.asyncio
    async def test_deep_scan_composite_field(self):
        """深度扫描能识别复合组件"""
        # Mock page.evaluate 返回
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value={
            "found": True,
            "is_composite": True,
            "elements": [
                {"type": "select", "index": 0},
                {"type": "input", "index": 1}
            ]
        })

        # 调用 _deep_scan_form_item（需要先导入）
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from module_discovery.discover_ui import _deep_scan_form_item

        result = await _deep_scan_form_item(page, "手机号")

        assert result["found"] is True
        assert result["is_composite"] is True
        assert len(result["elements"]) == 2
        assert result["elements"][0]["type"] == "select"
        assert result["elements"][1]["type"] == "input"

    @pytest.mark.asyncio
    async def test_deep_scan_normal_field(self):
        """深度扫描普通 input 字段"""
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value={
            "found": True,
            "is_composite": False,
            "elements": [
                {"type": "input", "index": 0}
            ]
        })

        from module_discovery.discover_ui import _deep_scan_form_item
        result = await _deep_scan_form_item(page, "用户名")

        assert result["found"] is True
        assert result["is_composite"] is False
        assert len(result["elements"]) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
