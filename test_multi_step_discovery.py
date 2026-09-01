"""
测试 MultiStepExecutor 选项自动发现功能
"""
import pytest
from unittest.mock import Mock, AsyncMock, MagicMock
from module_discovery.form_filler import MultiStepExecutor


class TestDiscoverFirstOption:
    """测试 _discover_first_option() 方法"""

    @pytest.mark.asyncio
    async def test_element_ui_returns_first_visible_option(self):
        """Element UI: 返回第一个可见选项文本"""
        page = Mock()
        page.evaluate = AsyncMock(return_value="选项1")

        executor = MultiStepExecutor(page, Mock(), "element-ui")
        result = await executor._discover_first_option()

        assert result == "选项1"
        page.evaluate.assert_called_once()
        # 验证 JS 脚本包含正确的选择器
        js_script = page.evaluate.call_args[0][0]
        assert ".el-select-dropdown__item" in js_script
        assert ":not(.is-disabled)" in js_script

    @pytest.mark.asyncio
    async def test_ant_design_returns_first_visible_option(self):
        """Ant Design: 返回第一个可见选项文本"""
        page = Mock()
        page.evaluate = AsyncMock(return_value="选项A")

        executor = MultiStepExecutor(page, Mock(), "ant-design")
        result = await executor._discover_first_option()

        assert result == "选项A"
        js_script = page.evaluate.call_args[0][0]
        assert ".ant-select-dropdown" in js_script
        assert ".ant-select-item-option" in js_script

    @pytest.mark.asyncio
    async def test_returns_empty_string_when_no_options(self):
        """无选项时返回空串"""
        page = Mock()
        page.evaluate = AsyncMock(return_value="")

        executor = MultiStepExecutor(page, Mock(), "element-ui")
        result = await executor._discover_first_option()

        assert result == ""

    @pytest.mark.asyncio
    async def test_handles_exception(self):
        """异常时返回空串"""
        page = Mock()
        page.evaluate = AsyncMock(side_effect=Exception("JS error"))

        executor = MultiStepExecutor(page, Mock(), "element-ui")
        result = await executor._discover_first_option()

        assert result == ""


class TestReadCascaderCurrentItems:
    """测试 _read_cascader_current_items() 方法"""

    @pytest.mark.asyncio
    async def test_element_ui_returns_items_with_hasChildren(self):
        """Element UI: 返回菜单项及子级标记"""
        page = Mock()
        page.evaluate = AsyncMock(return_value=[
            {"text": "第一级", "hasChildren": True},
            {"text": "第二级", "hasChildren": False}
        ])

        executor = MultiStepExecutor(page, Mock(), "element-ui")
        result = await executor._read_cascader_current_items()

        assert len(result) == 2
        assert result[0]["text"] == "第一级"
        assert result[0]["hasChildren"] is True
        assert result[1]["text"] == "第二级"
        assert result[1]["hasChildren"] is False

        js_script = page.evaluate.call_args[0][0]
        assert ".el-cascader-menu" in js_script
        assert 'li[role="menuitem"]' in js_script
        assert ".el-icon-arrow-right" in js_script

    @pytest.mark.asyncio
    async def test_ant_design_returns_items(self):
        """Ant Design: 返回菜单项"""
        page = Mock()
        page.evaluate = AsyncMock(return_value=[
            {"text": "分类A", "hasChildren": True}
        ])

        executor = MultiStepExecutor(page, Mock(), "ant-design")
        result = await executor._read_cascader_current_items()

        assert len(result) == 1
        assert result[0]["text"] == "分类A"

        js_script = page.evaluate.call_args[0][0]
        assert ".ant-cascader-menu" in js_script
        assert ".ant-cascader-menu-item" in js_script

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_menus(self):
        """无菜单时返回空列表"""
        page = Mock()
        page.evaluate = AsyncMock(return_value=[])

        executor = MultiStepExecutor(page, Mock(), "element-ui")
        result = await executor._read_cascader_current_items()

        assert result == []

    @pytest.mark.asyncio
    async def test_handles_exception(self):
        """异常时返回空列表"""
        page = Mock()
        page.evaluate = AsyncMock(side_effect=Exception("JS error"))

        executor = MultiStepExecutor(page, Mock(), "element-ui")
        result = await executor._read_cascader_current_items()

        assert result == []


class TestCascaderDiscoverAndSelect:
    """测试 _cascader_discover_and_select() 方法"""

    @pytest.mark.asyncio
    async def test_single_level_leaf_selection(self):
        """单层叶子节点：直接选择"""
        page = Mock()
        page.evaluate = AsyncMock(return_value=[
            {"text": "选项1", "hasChildren": False}
        ])
        page.wait_for_timeout = AsyncMock()

        kb = Mock()
        kb.get_conditional_branch = Mock(return_value={})
        kb.expand_step = Mock(side_effect=lambda p, **kw: p.format(**kw))

        executor = MultiStepExecutor(page, kb, "element-ui")
        executor._try_step_patterns = AsyncMock(return_value=True)
        executor._check_element_visible_raw = AsyncMock(return_value=False)

        steps = {"select-last-text": {"patterns": ["//select/{option_text}"]}}
        result = await executor._cascader_discover_and_select(steps, "字段")

        assert result is True
        executor._try_step_patterns.assert_called()

    @pytest.mark.asyncio
    async def test_multi_level_traversal(self):
        """多层级：逐级展开到叶子"""
        call_count = [0]

        async def mock_read_items():
            call_count[0] += 1
            if call_count[0] == 1:
                return [{"text": "第一级", "hasChildren": True}]
            elif call_count[0] == 2:
                return [{"text": "第二级", "hasChildren": True}]
            else:
                return [{"text": "第三级", "hasChildren": False}]

        page = Mock()
        page.wait_for_timeout = AsyncMock()

        kb = Mock()
        kb.get_conditional_branch = Mock(return_value={})
        kb.expand_step = Mock(side_effect=lambda p, **kw: p.format(**kw))

        executor = MultiStepExecutor(page, kb, "element-ui")
        executor._read_cascader_current_items = mock_read_items
        executor._try_step_patterns = AsyncMock(return_value=True)
        executor._check_element_visible_raw = AsyncMock(return_value=False)

        steps = {
            "expand-level": {"patterns": ["//expand/{option_text}"]},
            "select-last-text": {"patterns": ["//select/{option_text}"]}
        }
        result = await executor._cascader_discover_and_select(steps, "字段")

        assert result is True
        assert call_count[0] == 3  # 读取了3次

    @pytest.mark.asyncio
    async def test_max_depth_protection(self):
        """最大深度保护：超过10级时返回False"""
        page = Mock()
        page.wait_for_timeout = AsyncMock()
        page.evaluate = AsyncMock(return_value=[
            {"text": "层级", "hasChildren": True}
        ])

        kb = Mock()
        kb.expand_step = Mock(side_effect=lambda p, **kw: p.format(**kw))

        executor = MultiStepExecutor(page, kb, "element-ui")
        executor._try_step_patterns = AsyncMock(return_value=True)

        steps = {"expand-level": {"patterns": ["//expand/{option_text}"]}}
        result = await executor._cascader_discover_and_select(steps, "字段")

        assert result is False  # 超过最大深度

    @pytest.mark.asyncio
    async def test_no_items_returns_false(self):
        """无菜单项时返回False"""
        page = Mock()
        page.evaluate = AsyncMock(return_value=[])

        executor = MultiStepExecutor(page, Mock(), "element-ui")

        steps = {}
        result = await executor._cascader_discover_and_select(steps, "字段")

        assert result is False


class TestExecuteSelectWithAutoDiscovery:
    """测试 _execute_select() 自动发现集成"""

    @pytest.mark.asyncio
    async def test_auto_discovers_when_option_text_empty(self):
        """option_text 为空时触发自动发现"""
        page = Mock()
        page.wait_for_timeout = AsyncMock()

        kb = Mock()
        kb.get_steps = Mock(return_value={
            "expand": {"patterns": ["//expand/{label}"]},
            "editable-check": {"patterns": ["//check/{label}"]},
            "first-option": {"patterns": ["//first/{label}"]}
        })
        kb.expand_step = Mock(side_effect=lambda p, **kw: p.format(**kw))

        executor = MultiStepExecutor(page, kb, "element-ui")
        executor._try_step_patterns = AsyncMock(return_value=True)
        executor._check_element_visible = AsyncMock(return_value=False)
        executor._discover_first_option = AsyncMock(return_value="自动发现的选项")

        result = await executor._execute_select("字段", "", None)

        assert result is True
        executor._discover_first_option.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_discovery_when_option_text_provided(self):
        """option_text 不为空时跳过自动发现"""
        page = Mock()
        page.wait_for_timeout = AsyncMock()

        kb = Mock()
        kb.get_steps = Mock(return_value={
            "expand": {"patterns": ["//expand/{label}"]},
            "editable-check": {"patterns": ["//check/{label}"]},
            "fill": {"patterns": ["//fill/{label}"]},
            "select": {"patterns": ["//select/{option_text}"]}
        })
        kb.expand_step = Mock(side_effect=lambda p, **kw: p.format(**kw))

        executor = MultiStepExecutor(page, kb, "element-ui")
        executor._try_step_patterns = AsyncMock(return_value=True)
        executor._check_element_visible = AsyncMock(return_value=True)
        executor._discover_first_option = AsyncMock()

        result = await executor._execute_select("字段", "指定选项", None)

        assert result is True
        executor._discover_first_option.assert_not_called()


class TestExecuteCascaderWithAutoDiscovery:
    """测试 _execute_cascader() 自动发现集成"""

    @pytest.mark.asyncio
    async def test_auto_discovers_when_options_none(self):
        """options 为 None 时触发自动发现"""
        page = Mock()
        page.wait_for_timeout = AsyncMock()

        kb = Mock()
        kb.get_steps = Mock(return_value={
            "expand": {"patterns": ["//expand/{label}"]},
        })
        kb.expand_step = Mock(side_effect=lambda p, **kw: p.format(**kw))

        executor = MultiStepExecutor(page, kb, "element-ui")
        executor._try_step_patterns = AsyncMock(return_value=True)
        executor._cascader_discover_and_select = AsyncMock(return_value=True)

        result = await executor._execute_cascader("字段", "", None)

        assert result is True
        executor._cascader_discover_and_select.assert_called_once()

    @pytest.mark.asyncio
    async def test_uses_path_when_options_provided(self):
        """options 不为 None 时使用已知路径"""
        page = Mock()
        page.wait_for_timeout = AsyncMock()

        kb = Mock()
        kb.get_steps = Mock(return_value={
            "expand": {"patterns": ["//expand/{label}"]},
        })
        kb.expand_step = Mock(side_effect=lambda p, **kw: p.format(**kw))

        executor = MultiStepExecutor(page, kb, "element-ui")
        executor._try_step_patterns = AsyncMock(return_value=True)
        executor._cascader_select_by_path = AsyncMock(return_value=True)
        executor._cascader_discover_and_select = AsyncMock()

        result = await executor._execute_cascader("字段", "", ["第一级", "第二级"])

        assert result is True
        executor._cascader_select_by_path.assert_called_once()
        executor._cascader_discover_and_select.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
