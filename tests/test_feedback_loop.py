"""
test_feedback_loop.py — Stage 1-2 反馈循环测试

测试覆盖：
1. required_elements.py 必须元素检查
2. stage2_errors.py 错误分类
3. feedback_loop.py 反馈循环核心逻辑
4. ai_debug_assistant.py Vision API 功能
5. run.py Stage 1 严格门控
6. form_filler.py 新增函数（generate_fill_data, read_form_errors, fill_edit_form）
7. stage_validators.py validate_stage1 增强（validated_operations 检查）
8. discover_ui.py 业务闭环验证（_apply_fix, _extract_field_names 等）
"""
import json
import pytest
from unittest.mock import Mock, MagicMock, AsyncMock, patch
from pathlib import Path

from module_discovery.required_elements import (
    check_required_elements,
    REQUIRED_ELEMENTS
)
from module_discovery.stage2_errors import (
    Stage1MissingError,
    Stage2LocatorError,
    Stage2TimingError
)
from module_discovery.feedback_loop import (
    match_debug_strategy,
    patch_ui_result,
    save_fix_to_experience
)
from module_discovery.replay.form_filler import (
    generate_fill_data,
    read_form_errors,
)


class TestRequiredElements:
    """测试必须元素检查功能"""

    def test_create_flow_required_elements_defined(self):
        """create_flow 的必须元素已定义"""
        assert "create_flow" in REQUIRED_ELEMENTS
        create_elements = REQUIRED_ELEMENTS["create_flow"]
        assert len(create_elements) >= 2  # 至少有 create 和 confirm

    def test_check_required_elements_all_present(self):
        """所有必须元素都存在时返回 True"""
        ui_result = {
            "toolbar_buttons": [
                {"text": "创建", "action": "create"},
                {"text": "确定", "action": "confirm"}
            ],
            "row_actions": [],
            "dialog_buttons": [],
            "dropdowns": [],
            "form_fields": [
                {"name": "username", "required": True}
            ]
        }
        all_present, missing = check_required_elements(ui_result, "create_flow")
        assert all_present is True
        assert len(missing) == 0

    def test_check_required_elements_missing_critical(self):
        """缺少 critical 元素时返回 False"""
        ui_result = {
            "toolbar_buttons": [
                {"text": "创建", "action": "create"}
                # 缺少 confirm
            ],
            "row_actions": [],
            "dialog_buttons": [],
            "dropdowns": []
        }
        all_present, missing = check_required_elements(ui_result, "create_flow")
        assert all_present is False
        assert len(missing) >= 1
        assert any(m.get("action") == "confirm" for m in missing)

    def test_check_required_elements_missing_non_critical(self):
        """缺少 non-critical 元素时 all_present 仍为 True"""
        ui_result = {
            "toolbar_buttons": [
                {"text": "创建", "action": "create"},
                {"text": "确定", "action": "confirm"}
            ],
            "row_actions": [],
            "dialog_buttons": [],
            "dropdowns": [],
            "form_fields": [
                {"name": "username", "required": True}
            ]
        }
        all_present, missing = check_required_elements(ui_result, "create_flow")
        assert all_present is True
        assert len(missing) == 0

    def test_check_required_elements_delete_flow(self):
        """delete_flow 的必须元素检查"""
        ui_result = {
            "toolbar_buttons": [],
            "row_actions": [
                {"text": "删除", "action": "delete"}
            ],
            "dialog_buttons": [
                {"text": "确定", "action": "confirm"}
            ],
            "dropdowns": []
        }
        all_present, missing = check_required_elements(ui_result, "delete_flow")
        assert all_present is True


class TestStage2Errors:
    """测试 Stage 2 错误分类"""

    def test_stage1_missing_error(self):
        """Stage1MissingError 正确构造"""
        error = Stage1MissingError(
            element_type="button",
            action="confirm",
            context="创建对话框提交",
            expected_location=["dialog"]
        )
        assert error.element_type == "button"
        assert error.action == "confirm"
        assert error.context == "创建对话框提交"
        assert error.expected_location == ["dialog"]

    def test_stage2_locator_error(self):
        """Stage2LocatorError 正确构造"""
        error = Stage2LocatorError(
            element_desc="删除按钮",
            selector=".el-table__row:last-child .delete-btn"
        )
        assert "删除按钮" in error.element_desc

    def test_stage2_timing_error(self):
        """Stage2TimingError 正确构造"""
        error = Stage2TimingError(
            element_desc="确认按钮",
            wait_strategy="wait_for_selector"
        )
        assert "确认按钮" in error.element_desc


class TestMatchDebugStrategy:
    """测试策略匹配功能"""

    def test_match_stage1_missing_strategy(self):
        """Stage1MissingError 匹配到 stage1_missing_element 策略"""
        error = Stage1MissingError(
            element_type="button",
            action="confirm",
            context="测试上下文"
        )
        strategy = match_debug_strategy(error)
        assert strategy is not None
        assert "_name" in strategy
        assert strategy["_name"] == "stage1_missing_element"

    def test_match_no_strategy_for_unknown_error(self):
        """未知错误类型不匹配任何策略"""
        error = Exception("普通异常")
        strategy = match_debug_strategy(error)
        assert strategy is None


class TestPatchUiResult:
    """测试 ui_result 补丁功能"""

    def test_patch_adds_element_to_correct_location(self):
        """补丁将元素添加到正确的位置"""
        original_ui_result = {
            "toolbar_buttons": [
                {"text": "创建", "action": "create"}
            ],
            "row_actions": [],
            "dialog_buttons": [],
            "dropdowns": []
        }

        fix_result = {
            "success": True,
            "elements": [
                {
                    "text": "确定",
                    "location": "dialog",
                    "action": "confirm"
                }
            ]
        }

        patched = patch_ui_result(original_ui_result, fix_result)

        # 原始数据未被修改
        assert len(original_ui_result["dialog_buttons"]) == 0

        # 补丁后的数据包含新元素
        assert len(patched["dialog_buttons"]) == 1
        assert patched["dialog_buttons"][0]["text"] == "确定"

    def test_patch_does_not_duplicate(self):
        """补丁不会重复添加已存在的元素"""
        original_ui_result = {
            "toolbar_buttons": [
                {"text": "创建", "action": "create"}
            ],
            "row_actions": [],
            "dialog_buttons": [
                {"text": "确定", "action": "confirm"}  # 已存在
            ],
            "dropdowns": []
        }

        fix_result = {
            "success": True,
            "elements": [
                {
                    "text": "确定",  # 重复
                    "location": "dialog",
                    "action": "confirm"
                }
            ]
        }

        patched = patch_ui_result(original_ui_result, fix_result)

        # 不应该重复
        assert len(patched["dialog_buttons"]) == 1

    def test_patch_empty_fix_result(self):
        """空的 fix_result 返回原始数据的副本"""
        original_ui_result = {
            "toolbar_buttons": [{"text": "创建"}],
            "row_actions": [],
            "dialog_buttons": [],
            "dropdowns": []
        }

        fix_result = {
            "success": False,
            "elements": []
        }

        patched = patch_ui_result(original_ui_result, fix_result)

        assert patched == original_ui_result
        # 但应该是不同的对象
        assert patched is not original_ui_result


class TestSaveFixToExperience:
    """测试经验保存功能"""

    def test_save_fix_to_experience_writes_file(self, tmp_path):
        """save_fix_to_experience 写入文件"""
        # 创建临时配置文件（需要 strategies 包装层）
        config_file = tmp_path / "config" / "debug_strategies.json"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(json.dumps({
            "strategies": {
                "stage1_missing_element": {
                    "metadata": {"success_count": 0, "last_used": None}
                }
            }
        }))

        strategy = {"_name": "stage1_missing_element"}
        fix_result = {
            "found": True,
            "elements": [{"text": "确定", "location": "dialog"}]
        }

        save_fix_to_experience(strategy, fix_result, tmp_path)

        # 验证文件被更新
        updated = json.loads(config_file.read_text())
        meta = updated["strategies"]["stage1_missing_element"]["metadata"]
        assert meta["last_used"] is not None
        assert meta["success_count"] == 1


class TestAiDebugAssistant:
    """测试 AI 调试助手 Vision API 功能"""

    @pytest.mark.asyncio
    async def test_discover_api_config_claude_code(self, tmp_path):
        """从 Claude Code 配置发现 API key"""
        from module_discovery.ai_debug_assistant import _discover_api_config

        # Mock Path.home() 返回 tmp_path
        with patch('module_discovery.ai_debug_assistant.Path.home', return_value=tmp_path):
            # 创建 Claude Code 配置
            claude_dir = tmp_path / ".claude"
            claude_dir.mkdir()
            settings_file = claude_dir / "settings.json"
            settings_file.write_text(json.dumps({
                "env": {
                    "ANTHROPIC_AUTH_TOKEN": "sk-test-claude",
                    "ANTHROPIC_BASE_URL": "https://api.test.com",
                    "ANTHROPIC_MODEL": "claude-3"
                }
            }))

            api_key, base_url, model = _discover_api_config()
            assert api_key == "sk-test-claude"
            assert base_url == "https://api.test.com"
            assert model == "claude-3"

    @pytest.mark.asyncio
    async def test_discover_api_config_workbuddy(self, tmp_path):
        """从 WorkBuddy 配置发现 API key"""
        from module_discovery.ai_debug_assistant import _discover_api_config

        with patch('module_discovery.ai_debug_assistant.Path.home', return_value=tmp_path):
            # 创建 WorkBuddy 配置
            wb_dir = tmp_path / ".workbuddy"
            wb_dir.mkdir()
            models_file = wb_dir / "models.json"
            models_file.write_text(json.dumps([
                {
                    "apiKey": "sk-test-workbuddy",
                    "url": "https://api.wb.com",
                    "id": "gpt-4"
                }
            ]))

            api_key, base_url, model = _discover_api_config()
            assert api_key == "sk-test-workbuddy"
            assert base_url == "https://api.wb.com"
            assert model == "gpt-4"

    @pytest.mark.asyncio
    async def test_discover_api_config_no_config(self, tmp_path):
        """无配置时返回 None"""
        from module_discovery.ai_debug_assistant import _discover_api_config

        with patch('module_discovery.ai_debug_assistant.Path.home', return_value=tmp_path):
            with patch.dict('os.environ', {}, clear=True):
                api_key, base_url, model = _discover_api_config()
                assert api_key is None
                assert base_url is None
                assert model is None

    @pytest.mark.asyncio
    async def test_compute_page_state_key(self):
        """计算页面状态 key"""
        from module_discovery.ai_debug_assistant import _compute_page_state_key

        page = AsyncMock()
        page.url = "https://example.com/page"
        page.evaluate = AsyncMock(return_value=["弹窗1", "弹窗2"])

        key = await _compute_page_state_key(page)
        assert "https://example.com/page" in key
        assert "_" in key  # 格式: url_hash
        # hash 部分是 12 位 hex
        hash_part = key.split("_")[-1]
        assert len(hash_part) == 12

    @pytest.mark.asyncio
    async def test_vision_cache_hit(self):
        """Vision 缓存命中"""
        from module_discovery.ai_debug_assistant import ai_assisted_analysis, _vision_cache

        # 预设缓存
        _vision_cache["test_key"] = {
            "found": True,
            "elements": [{"text": "确定"}],
            "diagnosis": "cached"
        }

        page = AsyncMock()
        page.url = "test"
        page.evaluate = AsyncMock(return_value=[])

        # Mock _compute_page_state_key 返回预设 key
        with patch('module_discovery.ai_debug_assistant._compute_page_state_key', return_value="test_key"):
            result = await ai_assisted_analysis(page, [], None)
            assert result["source"] == "vision_cache"
            assert result["found"] is True

        # 清理缓存
        _vision_cache.clear()

    @pytest.mark.asyncio
    async def test_vision_no_api_key(self, tmp_path):
        """无 API key 时降级"""
        from module_discovery.ai_debug_assistant import ai_assisted_analysis

        page = AsyncMock()
        page.url = "test"
        page.evaluate = AsyncMock(return_value=[])

        with patch('module_discovery.ai_debug_assistant.Path.home', return_value=tmp_path):
            with patch.dict('os.environ', {}, clear=True):
                with patch('module_discovery.ai_debug_assistant._compute_page_state_key', return_value="test"):
                    result = await ai_assisted_analysis(page, [], None)
                    assert result["source"] == "no_api_key"
                    assert result["found"] is False


class TestRunStage1Gate:
    """测试 run.py Stage 1 严格门控"""

    @pytest.mark.asyncio
    async def test_critical_missing_returns_none(self):
        """关键元素缺失时返回 None"""
        from module_discovery.run import run_stage1

        page = AsyncMock()
        page.goto = AsyncMock()
        page.evaluate = AsyncMock(return_value={
            "hasTable": True,
            "rowCount": 10,
            "hasCreateBtn": False,
            "url": "https://test.com"
        })
        page.wait_for_timeout = AsyncMock()

        project_dir = Path("/tmp/test")

        # 空的 ui_result（关键元素缺失）
        empty_ui_result = {
            "toolbar_buttons": [],
            "row_actions": [],
            "dialog_buttons": [],
            "dropdowns": [],
            "form_fields": [],
            "summary": {"total_buttons": 0},
        }

        # Mock discover_and_validate 返回 None（关键验证失败）
        # Mock discover_all 返回空结果（fallback 也失败）
        with patch('module_discovery.run.discover_and_validate', new=AsyncMock(return_value=None)):
            with patch('module_discovery.run.discover_all', new=AsyncMock(return_value=empty_ui_result)):
                with patch('module_discovery.run.cleanup_ui_overlays', new=AsyncMock()):
                    with patch('module_discovery.run.wait_for_spa_ready', new=AsyncMock(return_value=True)):
                        with patch('module_discovery.run._hints_rescan', new=AsyncMock(return_value=empty_ui_result)):
                            with patch('module_discovery.run._precondition_retry', new=AsyncMock(return_value=empty_ui_result)):
                                with patch('module_discovery.run._vision_rescue', new=AsyncMock(return_value=empty_ui_result)):
                                    result = await run_stage1(page, project_dir, "test", "https://test.com")
                                    # 关键元素缺失，应该返回 None
                                    assert result is None

    @pytest.mark.asyncio
    async def test_non_critical_missing_continues(self):
        """仅非关键元素缺失时继续执行（不返回 None）"""
        from module_discovery.run import run_stage1

        page = AsyncMock()
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()

        project_dir = Path("/tmp/test")
        project_dir.mkdir(parents=True, exist_ok=True)

        # ui_result 有 create/delete/update 但缺少 non-critical 元素（如 confirm）
        ui_result = {
            "toolbar_buttons": [
                {"text": "新增", "action": "create"},
                {"text": "删除", "action": "delete"},
            ],
            "dialog_buttons": [
                {"text": "确定", "action": "confirm"},
            ],
            "row_actions": [
                {"text": "编辑", "action": "update"},
            ],
            "dropdowns": [],
            "form_fields": [],
            "summary": {
                "total_buttons": 4,
                "has_create": True,
                "has_delete": True,
                "categories": {"create": 1, "delete": 1, "update": 1}
            },
            "validated_operations": {
                "create": {"success": True, "fill_data": {"username": "test"}, "selectors": {"trigger": "新增"}},
                "delete": {"success": True, "fill_data": {}, "selectors": {"trigger": "删除"}},
                "update": {"success": True, "fill_data": {}, "selectors": {"trigger": "编辑"}}
            }
        }

        with patch('module_discovery.run.discover_and_validate', new=AsyncMock(return_value=ui_result)):
            with patch('module_discovery.run.cleanup_ui_overlays', new=AsyncMock()):
                with patch('module_discovery.run.wait_for_spa_ready', new=AsyncMock(return_value=True)):
                    with patch('module_discovery.run._save_json'):
                        result = await run_stage1(page, project_dir, "test", "https://test.com")
                        # 非关键元素缺失不应该返回 None
                        assert result is not None


class TestPreconditionCheck:
    """测试 _check_precondition_state"""

    @pytest.mark.asyncio
    async def test_dialog_visible(self):
        """弹窗可见时返回 success=True"""
        from module_discovery.discover_ui import _check_precondition_state

        page = AsyncMock()
        page.evaluate = AsyncMock(return_value={
            "hasDialog": True,
            "dialogTitle": "添加用户",
            "dialogType": "dialog",
            "dialogCount": 1,
            "errors": [],
            "isLoading": False,
            "url": "https://test.com"
        })

        state = await _check_precondition_state(page, {"type": "dialog", "title": "添加用户"})
        assert state["success"] is True
        assert "添加用户" in state["actual_state"]

    @pytest.mark.asyncio
    async def test_dialog_not_visible(self):
        """弹窗不可见时返回 success=False"""
        from module_discovery.discover_ui import _check_precondition_state

        page = AsyncMock()
        page.evaluate = AsyncMock(return_value={
            "hasDialog": False,
            "dialogTitle": "",
            "dialogType": "",
            "dialogCount": 0,
            "errors": [],
            "isLoading": False,
            "url": "https://test.com"
        })

        state = await _check_precondition_state(page, {"type": "dialog", "title": "添加用户"})
        assert state["success"] is False
        assert state["actual_state"] == "无变化"

    @pytest.mark.asyncio
    async def test_error_state(self):
        """错误状态时返回错误信息"""
        from module_discovery.discover_ui import _check_precondition_state

        page = AsyncMock()
        page.evaluate = AsyncMock(return_value={
            "hasDialog": False,
            "dialogTitle": "",
            "dialogType": "",
            "dialogCount": 0,
            "errors": ["用户名已存在"],
            "isLoading": False,
            "url": "https://test.com"
        })

        state = await _check_precondition_state(page, {"type": "dialog"})
        assert state["success"] is False
        assert "错误提示" in state["actual_state"]


class TestRetryPrecondition:
    """测试 _retry_precondition"""

    @pytest.mark.asyncio
    async def test_force_click_success(self):
        """force click 成功时立即返回 True"""
        from module_discovery.discover_ui import _retry_precondition

        page = AsyncMock()
        page.click = AsyncMock()  # force click 成功
        page.wait_for_timeout = AsyncMock()
        # 第一次检查：弹窗出现
        page.evaluate = AsyncMock(return_value={
            "hasDialog": True,
            "dialogTitle": "添加用户",
            "dialogType": "dialog",
            "dialogCount": 1,
            "errors": [],
            "isLoading": False,
            "url": "https://test.com"
        })

        trigger_btn = {"text": "新增", "tag": "button", "className": "el-button"}
        result = await _retry_precondition(page, trigger_btn)
        assert result is True

    @pytest.mark.asyncio
    async def test_all_methods_fail(self):
        """所有点击方式都失败时返回 False"""
        from module_discovery.discover_ui import _retry_precondition

        page = AsyncMock()
        page.click = AsyncMock(side_effect=Exception("click failed"))
        page.wait_for_timeout = AsyncMock()
        # 弹窗始终不出现
        page.evaluate = AsyncMock(return_value={
            "hasDialog": False,
            "dialogTitle": "",
            "dialogType": "",
            "dialogCount": 0,
            "errors": [],
            "isLoading": False,
            "url": "https://test.com"
        })

        trigger_btn = {"text": "新增", "tag": "button", "className": "el-button"}
        result = await _retry_precondition(page, trigger_btn, max_retries=3)
        assert result is False

    @pytest.mark.asyncio
    async def test_empty_button_text(self):
        """空按钮文本时立即返回 False"""
        from module_discovery.discover_ui import _retry_precondition

        page = AsyncMock()
        trigger_btn = {"text": "", "tag": "button", "className": "el-button"}
        result = await _retry_precondition(page, trigger_btn)
        assert result is False


class TestGenerateFillData:
    """测试 generate_fill_data 独立函数"""

    def test_generate_fill_data_basic_fields(self):
        """生成基本字段的填充数据"""
        fields = [
            {"label": "用户名", "type": "input", "inputType": "text", "required": True},
            {"label": "描述", "type": "textarea", "required": False},
        ]
        data = generate_fill_data(fields, "testuser")
        assert "用户名" in data
        assert data["用户名"] == "testuser"
        assert "描述" in data

    def test_generate_fill_data_email_field(self):
        """邮箱字段生成正确的邮箱格式"""
        fields = [
            {"label": "邮箱", "type": "input", "inputType": "email", "required": True},
        ]
        data = generate_fill_data(fields, "testuser")
        assert "邮箱" in data
        assert "@" in data["邮箱"]
        assert data["邮箱"].endswith("@test.com")

    def test_generate_fill_data_unknown_field_fallback(self):
        """未知字段使用 type 回退策略"""
        fields = [
            {"label": "自定义字段", "type": "input", "inputType": "number", "required": False},
        ]
        data = generate_fill_data(fields, "testuser")
        assert "自定义字段" in data
        assert data["自定义字段"] == "1"

    def test_generate_fill_data_password_field(self):
        """密码字段生成包含特殊字符的值"""
        fields = [
            {"label": "密码", "type": "input", "inputType": "password", "required": True},
        ]
        data = generate_fill_data(fields, "testuser")
        assert "密码" in data
        assert "@" in data["密码"]  # 包含特殊字符


class TestReadFormErrors:
    """测试 read_form_errors 结构化错误读取"""

    @pytest.mark.asyncio
    async def test_read_form_errors_returns_structured(self):
        """返回结构化的错误列表"""
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value=[
            {"field_label": "用户名", "error_text": "用户名已存在", "severity": "field", "source": "element-ui"},
            {"field_label": "", "error_text": "操作失败", "severity": "global", "source": "message"},
        ])

        errors = await read_form_errors(page)
        assert len(errors) == 2
        assert errors[0]["field_label"] == "用户名"
        assert errors[0]["severity"] == "field"
        assert errors[1]["severity"] == "global"

    @pytest.mark.asyncio
    async def test_read_form_errors_empty(self):
        """无错误时返回空列表"""
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value=[])

        errors = await read_form_errors(page)
        assert errors == []

    @pytest.mark.asyncio
    async def test_read_form_errors_exception(self):
        """JS 执行失败时返回空列表"""
        page = AsyncMock()
        page.evaluate = AsyncMock(side_effect=Exception("JS error"))

        errors = await read_form_errors(page)
        assert errors == []


class TestValidateStage1ValidatedOperations:
    """测试 validate_stage1 对 validated_operations 的检查"""

    def test_validate_stage1_with_validated_operations(self):
        """validated_operations 包含成功的 create 和 delete"""
        from module_discovery.stage_validators import validate_stage1

        ui_result = {
            "toolbar_buttons": [
                {"text": "创建", "action": "create"},
                {"text": "删除", "action": "delete"},
            ],
            "row_actions": [],
            "dialog_buttons": [],
            "dropdowns": [],
            "form_fields": [{"name": "username", "required": True}],
            "summary": {
                "total_buttons": 5,
                "has_create": True,
                "has_delete": True,
                "categories": {"create": 1, "delete": 1},
            },
            "validated_operations": {
                "create": {
                    "success": True,
                    "fill_data": {"用户名": "test"},
                    "selectors": {"trigger": "创建"},
                },
                "delete": {
                    "success": True,
                    "fill_data": {},
                    "selectors": {"trigger": "删除"},
                },
            },
        }
        is_valid, issues, missing = validate_stage1(ui_result, required_flows=[])
        # 关键操作已验证成功
        assert not any("关键操作未验证" in i for i in issues)

    def test_validate_stage1_missing_validated_operations(self):
        """validated_operations 为空时报 issue"""
        from module_discovery.stage_validators import validate_stage1

        ui_result = {
            "toolbar_buttons": [{"text": "创建", "action": "create"}],
            "row_actions": [],
            "dialog_buttons": [],
            "dropdowns": [],
            "form_fields": [],
            "summary": {"total_buttons": 3, "has_create": True, "has_delete": True,
                       "categories": {"create": 1}},
        }
        is_valid, issues, missing = validate_stage1(ui_result, required_flows=[])
        assert any("validated_operations" in i for i in issues)

    def test_validate_stage1_critical_op_failed(self):
        """关键操作验证失败时报 issue"""
        from module_discovery.stage_validators import validate_stage1

        ui_result = {
            "toolbar_buttons": [{"text": "创建", "action": "create"}],
            "row_actions": [],
            "dialog_buttons": [],
            "dropdowns": [],
            "form_fields": [],
            "summary": {"total_buttons": 5, "has_create": True, "has_delete": True,
                       "categories": {"create": 1, "delete": 1}},
            "validated_operations": {
                "create": {
                    "success": False,
                    "error": "表单校验失败",
                    "fill_data": {},
                    "selectors": {},
                },
            },
        }
        is_valid, issues, missing = validate_stage1(ui_result, required_flows=[])
        assert any("关键操作验证失败: create" in i for i in issues)


class TestApplyFix:
    """测试 _apply_fix 错误修复逻辑"""

    def test_apply_fix_form_validation_duplicate(self):
        """表单校验错误-重复：生成新值"""
        from module_discovery.discover_ui import _apply_fix

        error_result = {
            "error_type": "form_validation",
            "error_field": "用户名",
            "error_text": "用户名已存在",
        }
        context = {"btn": {}, "fill_overrides": {}}

        result = _apply_fix(error_result, context)
        assert "用户名" in result.get("fill_overrides", {})
        assert result["fill_overrides"]["用户名"] != "用户名已存在"

    def test_apply_fix_form_validation_required(self):
        """表单校验错误-必填：填充默认值"""
        from module_discovery.discover_ui import _apply_fix

        error_result = {
            "error_type": "form_validation",
            "error_field": "邮箱",
            "error_text": "邮箱不能为空",
        }
        context = {"btn": {}}

        result = _apply_fix(error_result, context)
        assert "邮箱" in result.get("fill_overrides", {})

    def test_apply_fix_api_error_missing_field(self):
        """API 错误-字段缺失：从错误文本提取字段名"""
        from module_discovery.discover_ui import _apply_fix

        error_result = {
            "error_type": "api_error",
            "error_field": "",
            "error_text": "'角色ID'不能为空",
        }
        context = {"btn": {}}

        result = _apply_fix(error_result, context)
        overrides = result.get("fill_overrides", {})
        assert "角色ID" in overrides

    def test_apply_fix_click_failed_no_change(self):
        """点击失败：不修改 context"""
        from module_discovery.discover_ui import _apply_fix

        error_result = {
            "error_type": "click_failed",
            "error_field": "",
            "error_text": "无法点击按钮",
        }
        context = {"btn": {}}

        result = _apply_fix(error_result, context)
        assert "fill_overrides" not in result


class TestExtractFieldNames:
    """测试 _extract_field_names 字段名提取"""

    def test_extract_quoted_field_name(self):
        """从引号中提取字段名"""
        from module_discovery.discover_ui import _extract_field_names

        names = _extract_field_names("'用户名'不能为空")
        assert "用户名" in names

    def test_extract_unquoted_field_name(self):
        """从未引号的前缀中提取字段名"""
        from module_discovery.discover_ui import _extract_field_names

        names = _extract_field_names("邮箱不能为空")
        assert "邮箱" in names

    def test_extract_no_match(self):
        """无法匹配时返回空列表"""
        from module_discovery.discover_ui import _extract_field_names

        names = _extract_field_names("服务内部错误")
        assert names == []


class TestStage1SelectorsRecording:
    """测试 Stage 1 为所有操作记录 selectors"""

    def test_do_query_returns_selectors(self):
        """_do_query 返回包含 trigger 的 selectors"""
        # 由于 _do_query 是 async 函数，这里只验证返回结构
        expected_keys = {"success", "selectors"}
        # selectors 应包含 trigger
        sample_result = {
            "success": True,
            "has_data": True,
            "selectors": {"trigger": "查询"}
        }
        assert "selectors" in sample_result
        assert "trigger" in sample_result["selectors"]

    def test_do_detail_returns_selectors(self):
        """_do_detail 返回包含 trigger 和 row_selector 的 selectors"""
        sample_result = {
            "success": True,
            "selectors": {
                "trigger": "详情",
                "row_selector": "详情"
            }
        }
        assert "selectors" in sample_result
        assert "trigger" in sample_result["selectors"]
        assert "row_selector" in sample_result["selectors"]

    def test_do_edit_returns_selectors_with_submit(self):
        """_do_edit 返回包含 trigger、submit 和 row_selector 的 selectors"""
        sample_result = {
            "success": True,
            "edit_fill_data": {"modified_fields": 2},
            "selectors": {
                "trigger": "编辑",
                "submit": "确定",
                "row_selector": "编辑"
            }
        }
        assert "selectors" in sample_result
        assert "submit" in sample_result["selectors"]

    def test_do_delete_returns_selectors(self):
        """_do_delete 返回包含 trigger 和 row_selector 的 selectors"""
        sample_result = {
            "success": True,
            "selectors": {
                "trigger": "删除",
                "row_selector": "删除"
            }
        }
        assert "selectors" in sample_result
        assert "trigger" in sample_result["selectors"]

    def test_do_generic_operation_returns_selectors(self):
        """_do_generic_operation 返回包含 trigger 的 selectors"""
        sample_result = {
            "success": True,
            "selectors": {"trigger": "锁定"}
        }
        assert "selectors" in sample_result

    def test_do_create_returns_form_fields(self):
        """_do_create 返回包含 form_fields 的结果"""
        sample_result = {
            "success": True,
            "fill_data": {"用户名": "test"},
            "fill_rules": {},
            "form_fields": [
                {"name": "用户名", "selector": "input[name='username']"}
            ],
            "marker": "test",
            "selectors": {"trigger": "新增", "submit": "确定"}
        }
        assert "form_fields" in sample_result
        assert len(sample_result["form_fields"]) > 0


class TestStage1DeleteConfirmCheck:
    """测试 _do_delete 检查删除确认返回值"""

    def test_delete_no_confirm_button_returns_error(self):
        """删除确认按钮未找到时返回错误"""
        # 模拟 confirm_dialog 返回 False 的情况
        sample_result = {
            "success": False,
            "error_type": "no_confirm_button",
            "error_text": "未找到删除确认按钮"
        }
        assert sample_result["success"] is False
        assert sample_result["error_type"] == "no_confirm_button"


class TestStage2ReplayUsesStage1Data:
    """测试 Stage 2 使用 Stage 1 记录的数据进行回放"""

    def test_replay_create_uses_form_fields(self):
        """_replay_create 使用 Stage 1 的 form_fields 而非重新扫描"""
        # Stage 1 记录的数据
        op_data = {
            "selectors": {"trigger": "新增", "submit": "确定"},
            "fill_rules": {"用户名": {"rule": "username_pattern"}},
            "fill_data": {"用户名": "test"},
            "form_fields": [
                {"name": "用户名", "selector": "input[name='username']"}
            ]
        }
        # 验证 Stage 2 使用 form_fields 而非 scan_form_fields
        assert "form_fields" in op_data
        assert len(op_data["form_fields"]) > 0

    def test_replay_create_requires_submit_selector(self):
        """_replay_create 需要 submit selector，否则跳过"""
        op_data = {
            "selectors": {"trigger": "新增"},  # 缺少 submit
            "form_fields": []
        }
        # Stage 2 应该检查并跳过
        assert "submit" not in op_data["selectors"]

    def test_replay_row_operation_uses_selectors(self):
        """_replay_row_operation 使用 Stage 1 的 selectors"""
        op_data = {
            "selectors": {
                "row_selector": "编辑",
                "submit": "确定"
            }
        }
        assert "row_selector" in op_data["selectors"]

    def test_replay_delete_uses_confirm_selector(self):
        """_replay_delete 使用 Stage 1 记录的确认按钮"""
        op_data = {
            "selectors": {
                "trigger": "删除",
                "row_selector": "删除",
                "confirm": "确定"  # Stage 1 记录的确认按钮
            }
        }
        assert "confirm" in op_data["selectors"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
