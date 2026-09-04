"""验证 post_confirm_state 和 close_dialog 步骤生成"""

from module_discovery.discover_ui import (
    _build_dropdown_steps,
    _build_delete_steps,
    _build_generic_steps,
)


def test_dropdown_steps_with_dialog_remains():
    """测试：reset 操作后对话框仍在时，应生成 close_dialog 步骤"""
    op_data = {
        "is_dropdown_operation": True,
        "dropdown_item_text": "重置密码",
        "confirmed": "确定",
        "post_confirm_state": {
            "dialog_remains": True,
            "has_form_in_dialog": False,
        },
        "success_locator": ".el-message--success",
    }

    steps = _build_dropdown_steps(op_data)
    actions = [s["action"] for s in steps]

    print("Dropdown steps with dialog_remains=True:")
    for i, step in enumerate(steps, 1):
        print(f"  {i}. {step['action']}: {step['description']}")

    assert "close_dialog" in actions, "close_dialog 步骤未生成"
    close_idx = actions.index("close_dialog")
    confirm_idx = actions.index("confirm_dialog")
    assert close_idx > confirm_idx, "close_dialog 应在 confirm_dialog 之后"
    print("[OK] close_dialog step inserted correctly\n")


def test_dropdown_steps_without_dialog_remains():
    """测试：reset 操作后对话框已关闭时，不应生成 close_dialog 步骤"""
    op_data = {
        "is_dropdown_operation": True,
        "dropdown_item_text": "冻结",
        "confirmed": "确定",
        "post_confirm_state": {
            "dialog_remains": False,
            "has_form_in_dialog": False,
        },
        "success_locator": ".el-message--success",
    }

    steps = _build_dropdown_steps(op_data)
    actions = [s["action"] for s in steps]

    print("Dropdown steps with dialog_remains=False:")
    for i, step in enumerate(steps, 1):
        print(f"  {i}. {step['action']}: {step['description']}")

    assert "close_dialog" not in actions, "dialog_remains=False 时不应生成 close_dialog"
    print("[OK] No close_dialog step (expected)\n")


def test_delete_steps_with_dialog_remains():
    """测试：delete 操作后对话框仍在时，应生成 close_dialog 步骤"""
    op_data = {
        "needs_checkbox": True,
        "checkbox_locator": ".el-checkbox__input",
        "selectors": {"trigger": "批量删除", "confirm": "确定"},
        "trigger_locator_verified": "button:has-text('批量删除')",
        "confirmed": True,
        "post_confirm_state": {
            "dialog_remains": True,
            "has_form_in_dialog": False,
        },
    }

    steps = _build_delete_steps(op_data)
    actions = [s["action"] for s in steps]

    print("Delete steps with dialog_remains=True:")
    for i, step in enumerate(steps, 1):
        print(f"  {i}. {step['action']}: {step['description']}")

    assert "close_dialog" in actions, "close_dialog 步骤未生成"
    print("[OK] close_dialog step inserted correctly\n")


def test_generic_steps_with_dialog_remains():
    """测试：generic 操作后对话框仍在时，应生成 close_dialog 步骤"""
    op_data = {
        "selectors": {"trigger": "授权"},
        "trigger_locator_verified": "button:has-text('授权')",
        "confirmed": "确定",
        "post_confirm_state": {
            "dialog_remains": True,
            "has_form_in_dialog": False,
        },
        "success_locator": ".el-message--success",
    }

    steps = _build_generic_steps(op_data)
    actions = [s["action"] for s in steps]

    print("Generic steps with dialog_remains=True:")
    for i, step in enumerate(steps, 1):
        print(f"  {i}. {step['action']}: {step['description']}")

    assert "close_dialog" in actions, "close_dialog 步骤未生成"
    print("[OK] close_dialog step inserted correctly\n")


def test_replay_engine_close_dialog():
    """测试：replay_engine 能正确处理 close_dialog 步骤"""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from module_discovery.replay_engine import replay_from_playbook
    from module_discovery.button_driver import ButtonDriver

    # Mock page 和 button_driver
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.click = AsyncMock()

    button_driver = ButtonDriver(page)

    # 模拟 close_dialog 调用
    with patch('module_discovery.replay_engine.close_dialog') as mock_close:
        mock_close.return_value = True

        steps = [
            {"action": "close_dialog", "description": "关闭残留对话框"}
        ]

        asyncio.run(replay_from_playbook(page, steps, button_driver))

        mock_close.assert_called_once_with(page)
        print("[OK] replay_engine executed close_dialog correctly\n")


if __name__ == "__main__":
    from unittest.mock import patch

    print("=" * 60)
    print("Verify post_confirm_state and close_dialog implementation")
    print("=" * 60)

    test_dropdown_steps_with_dialog_remains()
    test_dropdown_steps_without_dialog_remains()
    test_delete_steps_with_dialog_remains()
    test_generic_steps_with_dialog_remains()

    print("All tests passed!")
