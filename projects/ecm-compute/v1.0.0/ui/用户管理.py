#!/usr/bin/env python3
"""
用户管理 - UI 自动化测试脚本

生成时间: 2026-09-29 11:15:30
生成工具: API AI Test Framework - Stage 2
版本: v1.0.0

用法:
    python 用户管理.py                    # 运行所有操作
    python 用户管理.py create update      # 只运行 create 和 update
    python 用户管理.py --headless         # 无头模式

依赖:
    pip install playwright
    playwright install chromium
"""

import json
import os
import sys
import io
import asyncio
import argparse
import base64
import time
from pathlib import Path
from playwright.async_api import async_playwright

# ==================== Bootstrap ====================

_lib = Path(__file__).resolve().parent / "lib"
sys.path.insert(0, str(_lib.parent))

from lib.replay_engine import replay_from_playbook
from lib.button_driver import ButtonDriver

# ==================== 配置 ====================

CONFIG = {
    "base_url": "https://10.151.61.248",
    "login_url": "https://10.151.61.248/estack/web/estack/login",
    "target_url": "https://10.151.61.248/estack/web/estack/user-center/user-manage/user",
    "headless": False,
    "slow_mo": 100,
    # 鉴权配置（cookie_client 统一使用）
    "auth_config": {
        "token_key": "accessToken",
        "token_storage": "localStorage",
        "cookie_token_key": "accessToken",
    },
}

AVAILABLE_OPERATIONS = ['创建用户', 'query', '批量导入', '编辑', '授权', '冻结', '启用', '锁定', '重置密码', '迁移', '删除']

# Playbook 数据内嵌到脚本中（不依赖外部 JSON 文件）
_PLAYBOOK_JSON = '{"meta": {"module_name": "用户管理", "target_url": "https://10.151.61.248/estack/web/estack/user-center/user-manage/user", "base_url": "https://10.151.61.248", "login_url": "https://10.151.61.248/estack/web/estack/login", "framework": "element-ui", "generated_at": "2026-09-29 11:15:29", "version": "1.0", "auth_config": {"token_key": "accessToken", "token_storage": "localStorage", "cookie_token_key": "accessToken"}}, "page_structure": {"hasFixedLeft": true, "hasFixedRight": true, "mainBodyRows": 7, "fixedRightRows": 7, "fixedLeftRows": 7, "columnCount": 9, "operationColumnIndex": 8, "operationColumnLocation": "last", "tableWrappers": [{"cls": "el-table__header-wrapper", "rowCount": 0}, {"cls": "el-table__body-wrapper is-scrolling-left", "rowCount": 7}, {"cls": "el-table__fixed-header-wrapper", "rowCount": 0}, {"cls": "el-table__fixed-body-wrapper", "rowCount": 7}, {"cls": "el-table__fixed-header-wrapper", "rowCount": 0}, {"cls": "el-table__fixed-body-wrapper", "rowCount": 7}]}, "operations": {"创建用户": {"display_name": "创建用户", "description": "创建用户", "role": "create", "steps": [{"action": "click_button", "playwright_locator": "span:has-text(\\"创建用户\\")", "description": "点击创建按钮"}, {"action": "wait_for_dialog", "playwright_locator": ".el-dialog__wrapper", "interaction_mode": "page-nav", "description": "等待创建对话框"}, {"action": "fill_form", "fields": [{"label": "账号", "playwright_locator": "#app > div > section > section > main > div > div > div:nth-of-type(2) > form > div:nth-of-type(1) > div > div:nth-of-type(2) > div > div:nth-of-type(1) > input", "type": "input", "kb_category": "input-generic", "fill_rule": {"rule": "name_pattern", "params": {"prefix": "test"}}, "is_marker": true}, {"label": "姓名", "playwright_locator": "#app > div > section > section > main > div > div > div:nth-of-type(2) > form > div:nth-of-type(1) > div > div:nth-of-type(3) > div > div:nth-of-type(1) > input", "type": "input", "kb_category": "input-generic", "fill_rule": {"rule": "name_pattern", "params": {"prefix": "test"}}}, {"label": "密码", "playwright_locator": "#app > div > section > section > main > div > div > div:nth-of-type(2) > form > div:nth-of-type(1) > div > div:nth-of-type(4) > div > div > input:nth-of-type(1)", "type": "input", "kb_category": "input-generic", "fill_rule": {"rule": "password_fixed", "params": {"value": "Test@123456"}}}, {"label": "确认密码", "playwright_locator": "#app > div > section > section > main > div > div > div:nth-of-type(2) > form > div:nth-of-type(1) > div > div:nth-of-type(5) > div > div > input:nth-of-type(1)", "type": "input", "kb_category": "input-generic", "fill_rule": {"rule": "password_fixed", "params": {"value": "Test@123456"}}}, {"label": "手机号", "playwright_locator": "#app > div > section > section > main > div > div > div:nth-of-type(2) > form > div:nth-of-type(1) > div > div:nth-of-type(6) > div > div > input", "type": "input", "kb_category": "input-generic", "fill_rule": {"rule": "phone_pattern", "params": {"prefix": "138"}}}, {"label": "邮箱", "playwright_locator": "#app > div > section > section > main > div > div > div:nth-of-type(2) > form > div:nth-of-type(1) > div > div:nth-of-type(7) > div > div:nth-of-type(1) > input", "type": "input", "kb_category": "input-generic", "fill_rule": {"rule": "email_pattern", "params": {"domain": "test.com"}}}, {"label": "描述", "playwright_locator": "#app > div > section > section > main > div > div > div:nth-of-type(2) > form > div:nth-of-type(1) > div > div:nth-of-type(8) > div > div > textarea:nth-of-type(1)", "type": "textarea", "kb_category": "textarea-generic", "fill_rule": {"rule": "description_pattern", "params": {"prefix": "auto"}}}, {"label": "系统角色", "playwright_locator": ".el-form-item:nth-child(2) .el-select", "type": "select", "kb_category": "el-select", "fill_rule": {}, "is_editable": false, "option_text": "普通用户"}, {"label": "部门角色", "playwright_locator": ".el-form-item:nth-child(3) .el-select", "type": "select", "kb_category": "el-select", "fill_rule": {}, "is_editable": false, "option_text": "AT_test_058767"}], "description": "填充表单字段"}, {"action": "click_button", "playwright_locator": "button:has-text(\\"确 定\\")", "text": "确 定", "description": "点击提交按钮", "click_strategy": "js"}, {"action": "assert_success", "playwright_locator": ".el-message--success", "description": "验证创建成功"}], "marker": "test", "detection_status": "success", "replayable": true}, "query": {"display_name": "query", "description": "query", "role": "query", "steps": [{"action": "fill_input", "playwright_locator": "input[placeholder=\\"请输入\\"]", "value": "{marker_name}", "description": "搜索框输入: 请输入"}, {"action": "press_key", "key": "Enter", "description": "回车触发搜索"}, {"action": "wait_for_table_ready", "description": "等待表格数据刷新"}], "marker": null, "detection_status": "success", "replayable": true}, "批量导入": {"display_name": "批量导入", "description": "批量导入", "role": "unknown", "steps": [], "marker": null, "detection_status": "failed", "replayable": false, "error_type": "env_dependency", "error_text": "文件上传操作无法自动完成"}, "编辑": {"display_name": "编辑", "description": "编辑", "role": "update", "steps": [{"action": "find_row", "description": "定位目标数据行"}, {"action": "click_row_button", "button_text": "编辑", "playwright_locator": "button:has-text(\'编辑\')", "description": "点击行内编辑按钮"}, {"action": "wait_for_dialog", "playwright_locator": ".el-dialog__wrapper", "interaction_mode": "page-nav", "description": "等待编辑对话框"}, {"action": "fill_form", "fields": [{"label": "姓名", "playwright_locator": "#app > div > section > section > main > div > div > div:nth-of-type(2) > div > div:nth-of-type(3) > div > div:nth-of-type(2) > form > div:nth-of-type(2) > div > div > input", "type": "input", "kb_category": "input-generic", "fill_rule": {"rule": "name_pattern", "params": {"prefix": "test"}}}, {"label": "邮箱", "playwright_locator": "#app > div > section > section > main > div > div > div:nth-of-type(2) > div > div:nth-of-type(3) > div > div:nth-of-type(2) > form > div:nth-of-type(3) > div > div > input", "type": "input", "kb_category": "input-generic", "fill_rule": {"rule": "email_pattern", "params": {"domain": "test.com"}}}, {"label": "手机号", "playwright_locator": "#app > div > section > section > main > div > div > div:nth-of-type(2) > div > div:nth-of-type(3) > div > div:nth-of-type(2) > form > div:nth-of-type(4) > div > div > input", "type": "input", "kb_category": "input-generic", "fill_rule": {"rule": "phone_pattern", "params": {"prefix": "138"}}}, {"label": "描述", "playwright_locator": "#app > div > section > section > main > div > div > div:nth-of-type(2) > div > div:nth-of-type(3) > div > div:nth-of-type(2) > form > div:nth-of-type(5) > div > div > textarea:nth-of-type(1)", "type": "textarea", "kb_category": "textarea-generic", "fill_rule": {"rule": "description_pattern", "params": {"prefix": "auto"}}}], "description": "填充编辑表单"}, {"action": "click_button", "playwright_locator": "button:has-text(\\"确 定\\")", "text": "确 定", "description": "点击提交按钮"}, {"action": "assert_success", "playwright_locator": ".el-message--success", "description": "验证更新成功"}], "marker": null, "detection_status": "success", "replayable": false}, "授权": {"display_name": "授权", "description": "授权", "role": "create", "steps": [{"action": "wait_for_dialog", "playwright_locator": ".el-dialog__wrapper", "interaction_mode": "dialog", "description": "等待创建对话框"}, {"action": "assert_success", "playwright_locator": ".el-message--success", "description": "验证创建成功"}], "marker": null, "detection_status": "success", "replayable": true, "nav_info": {"navigated_url": "https://10.151.61.248/estack/web/estack/user-center/user-manage/authority-manage/add-authority?tenantId=42ffdba38c58484f9be2bc1adf1672e6&type=userList&rowId=c89b97a302234e18b1aa0a7077254ae0", "depth": 0, "page_title": "eStack Enterprise", "has_form": false, "form_fields": [], "fill_data": {"授权用户": "AT_AT_test_auto_651583", "授权用户组": "AT_AT_test_auto_651583", "授权角色": "AT_AT_test_auto_651583", "授权范围": "AT_AT_test_auto_651583"}, "submit_result": {"success": true, "button_text": "确 定"}, "error_type": "", "error_text": "", "field_states": [{"label": "授权用户", "type": "select", "value": "AT_test_651551, AT_test_651551", "isDisabled": true, "hasValue": true, "isMultiSelect": true}, {"label": "授权用户组", "type": "select", "value": "AT_test_645675, AT_test_645675", "isDisabled": false, "hasValue": true, "isMultiSelect": true}, {"label": "授权角色", "type": "radio", "value": "组织架构", "isDisabled": false, "hasValue": true}]}}, "冻结": {"display_name": "冻结", "description": "冻结", "role": "generic", "steps": [{"action": "find_row", "description": "定位目标数据行"}, {"action": "click_row_more", "item_text": "冻结", "expand_strategy": "click", "description": "点击下拉菜单项: 冻结"}, {"action": "confirm_dialog", "description": "点击确认对话框"}, {"action": "assert_success", "playwright_locator": ".el-message--success, .el-notification__content:has-text(\'成功\'), [role=\'alert\']:has-text(\'成功\')", "description": "验证操作成功"}], "marker": "test", "detection_status": "success", "replayable": true}, "启用": {"display_name": "启用", "description": "启用", "role": "generic", "steps": [{"action": "find_row", "description": "定位目标数据行"}, {"action": "click_row_more", "item_text": "启用", "expand_strategy": "click", "description": "点击下拉菜单项: 启用"}, {"action": "confirm_dialog", "description": "点击确认对话框"}, {"action": "assert_success", "playwright_locator": ".el-message--success, .el-notification__content:has-text(\'成功\'), [role=\'alert\']:has-text(\'成功\')", "description": "验证操作成功"}], "marker": "test", "detection_status": "success", "replayable": true}, "锁定": {"display_name": "锁定", "description": "锁定", "role": "generic", "steps": [{"action": "find_row", "description": "定位目标数据行"}, {"action": "click_row_more", "item_text": "锁定", "expand_strategy": "click", "description": "点击下拉菜单项: 锁定"}, {"action": "confirm_dialog", "description": "点击确认对话框"}, {"action": "assert_success", "playwright_locator": ".el-message--success, .el-notification__content:has-text(\'成功\'), [role=\'alert\']:has-text(\'成功\')", "description": "验证操作成功"}], "marker": "test", "detection_status": "success", "replayable": true}, "重置密码": {"display_name": "重置密码", "description": "重置密码", "role": "generic", "steps": [{"action": "find_row", "description": "定位目标数据行"}, {"action": "click_row_more", "item_text": "重置密码", "expand_strategy": "click", "description": "点击下拉菜单项: 重置密码"}, {"action": "fill_form", "fields": [{"label": "通知方式", "playwright_locator": ".el-form-item:nth-child(3) .el-select", "type": "select", "kb_category": "el-select", "fill_rule": {}, "is_editable": false, "option_text": "邮箱通知"}], "description": "填充弹窗中的下拉选择字段"}, {"action": "click_button", "text": "确认", "playwright_locator": "button:has-text(\\"确认\\")", "description": "点击确认按钮"}, {"action": "assert_success", "playwright_locator": ".el-message--success, .el-notification__content:has-text(\'成功\'), [role=\'alert\']:has-text(\'成功\')", "description": "验证操作成功"}], "marker": "test", "detection_status": "success", "replayable": true}, "迁移": {"display_name": "迁移", "description": "迁移", "role": "unknown", "steps": [], "marker": null, "detection_status": "failed", "replayable": false, "error_type": "no_success_signal", "error_text": "迁移 操作后未检测到成功信号（成功提示/数据变化）"}, "删除": {"display_name": "删除", "description": "删除", "role": "delete", "steps": [{"action": "find_row", "description": "定位目标数据行"}, {"action": "click_button", "text": "删除", "playwright_locator": "button:has-text(\'删除\')", "description": "点击删除按钮"}, {"action": "confirm_dialog", "description": "点击确认对话框"}, {"action": "assert_row_disappeared", "description": "验证数据行已消失"}], "marker": "test", "detection_status": "success", "replayable": true}}}'

# ==================== Cookie 鉴权 ====================

async def require_cookie_auth(context, page):
    """使用 cookie_client 统一鉴权，失败则报错退出。"""
    auth_cfg = CONFIG.get("auth_config", {})
    full_auth_cfg = {**auth_cfg, "login_url": CONFIG["login_url"], "target_url": CONFIG["target_url"]}

    from lib.cookie_client import require_auth_for_ui, apply_auth_to_playwright

    cookies, token = await require_auth_for_ui(
        config_dir=Path(__file__).parent / "config",
        auth_config=full_auth_cfg,
    )

    await apply_auth_to_playwright(context, page, cookies, token, full_auth_cfg)

# ==================== 操作执行 ====================

async def _cleanup_dialogs(page):
    """关闭操作间可能残留的对话框/弹窗，并等待表格恢复就绪。"""
    # --- Phase 0: 释放钉住的通知 ---
    # confirm_dialog 会钉住通知元素以保持截图时可见，
    # 清理前需要先释放，让通知可以正常消失
    try:
        await page.evaluate("() => { if (window.__unpin_notifications) window.__unpin_notifications(); }")
    except Exception:
        pass

    # --- Phase 1: 关闭残留弹窗 ---
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(300)
    except Exception:
        pass
    # Element UI / Ant Design / 通用关闭按钮
    close_selectors = [
        ".el-dialog__close",
        ".el-message-box__btns button:not(.el-button--primary)",
        ".el-drawer__close-btn",
        ".ant-modal-close",
        ".el-dialog__headerbtn",
    ]
    for sel in close_selectors:
        try:
            els = await page.query_selector_all(sel)
            for el in els:
                if await el.is_visible():
                    await el.click()
                    await page.wait_for_timeout(200)
        except Exception:
            pass
    # 最终兜底 Escape
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)
    except Exception:
        pass

    # --- Phase 2: 等待表格恢复就绪 ---
    try:
        from lib.wait_helpers import wait_for_table_ready
        await wait_for_table_ready(page, timeout=8000)
    except Exception:
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        await page.wait_for_timeout(1000)

# 操作失败原因（由 Stage 1 标记）
_OPERATION_STATUS = {
    "批量导入": {
        "status": "failed",
        "error_type": "env_dependency",
        "error_text": "文件上传操作无法自动完成"
    },
    "迁移": {
        "status": "failed",
        "error_type": "no_success_signal",
        "error_text": "迁移 操作后未检测到成功信号（成功提示/数据变化）"
    }
}

async def run_operation(page, operation_name, operations_data, marker=None):
    """使用回放引擎执行单个操作"""
    op = operations_data.get(operation_name)
    if not op:
        print(f"⚠️ 操作不存在: {operation_name}")
        return marker, {"operation": operation_name, "status": "failed", "error": "操作不存在", "steps": []}

    # 使用 playbook 中定义的 marker（如果有），否则使用上一个操作传递的 marker
    op_marker = op.get("marker") or marker

    # 跳过 Stage 1 标记为失败的操作
    st = _OPERATION_STATUS.get(operation_name, {})
    if st.get("status") == "failed":
        reason = st.get("error_text", "Stage 1 标记为失败")
        err_type = st.get("error_type", "unknown")
        display_name = op.get("display_name", op.get("description", operation_name))
        print(f"\n⏭ 跳过: {display_name} ({err_type}: {reason})")
        return marker, {
            "operation": operation_name,
            "display_name": display_name,
            "status": "skipped",
            "error": f"[{err_type}] {reason}",
            "steps": [],
            "duration": 0,
        }

    print(f"\n▶ 执行: {op.get('display_name', op.get('description', operation_name))}")

    steps = op.get("steps", [])
    button_driver = ButtonDriver(page)
    op_start = time.time()

    try:
        result = await replay_from_playbook(page, steps, button_driver, op_marker)
        duration = time.time() - op_start

        new_marker = result.get("marker", op_marker)
        # 截图（成功）
        screenshot = None
        try:
            raw = await page.screenshot(type="png")
            screenshot = base64.b64encode(raw).decode("ascii")
        except Exception:
            pass

        # 构建详细步骤信息（含 locator 和测试数据）
        detailed_steps = []
        for s in steps:
            step_info = {
                "action": s.get("action", ""),
                "status": "passed",
                "locator": s.get("playwright_locator", ""),
                "description": s.get("description", ""),
            }
            # 填充表单的字段数据
            if s.get("action") == "fill_form" and "fields" in s:
                fields_summary = []
                for field in s["fields"]:
                    field_info = {
                        "label": field.get("label", ""),
                        "locator": field.get("playwright_locator", ""),
                        "type": field.get("type", ""),
                    }
                    # 测试数据
                    fill_rule = field.get("fill_rule", {})
                    if fill_rule:
                        params = fill_rule.get("params", {})
                        if "value" in params:
                            field_info["test_data"] = params["value"]
                        elif "prefix" in params:
                            field_info["test_data"] = f"{params['prefix']}*"
                        else:
                            field_info["test_data"] = str(fill_rule.get("rule", ""))
                    elif field.get("option_text"):
                        field_info["test_data"] = field["option_text"]
                    fields_summary.append(field_info)
                step_info["fields"] = fields_summary

            detailed_steps.append(step_info)

        op_result = {
            "operation": operation_name,
            "display_name": op.get("display_name", operation_name),
            "status": "passed",
            "steps": detailed_steps,
            "duration": duration,
            "screenshot": screenshot,
        }
        print(f"  ✅ 操作成功 (耗时 {duration:.2f}s)")

        # 操作成功后清理残留弹窗
        await _cleanup_dialogs(page)
        return new_marker, op_result

    except Exception as e:
        duration = time.time() - op_start
        # 截图（失败）
        screenshot = None
        try:
            raw = await page.screenshot(type="png")
            screenshot = base64.b64encode(raw).decode("ascii")
        except Exception:
            pass
        op_result = {
            "operation": operation_name,
            "display_name": op.get("display_name", operation_name),
            "status": "failed",
            "error": str(e),
            "steps": [],
            "duration": duration,
            "screenshot": screenshot,
        }
        print(f"  ❌ 操作失败: {e}")

        # 失败后也尝试清理弹窗
        await _cleanup_dialogs(page)
        return marker, op_result

# ==================== 报告 ====================

from lib.ui_report import generate_ui_report as generate_html_report

# ==================== 主程序 ====================

async def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="用户管理 UI 自动化测试脚本")
    parser.add_argument("operations", nargs="*", help="要执行的操作列表")
    parser.add_argument("--headless", action="store_true", help="无头模式")
    args = parser.parse_args()

    # 加载 playbook（内嵌在脚本中）
    playbook = json.loads(_PLAYBOOK_JSON)

    operations_data = playbook.get("operations", {})

    # 确定要执行的操作
    ops = args.operations if args.operations else AVAILABLE_OPERATIONS

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=args.headless or CONFIG["headless"],
            slow_mo=CONFIG["slow_mo"]
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            ignore_https_errors=True
        )
        page = await context.new_page()

        # Cookie 鉴权（不做登录）
        print("Cookie 鉴权...")
        await require_cookie_auth(context, page)

        print(f"导航到: {CONFIG['target_url']}")
        await page.goto(CONFIG["target_url"], wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # 等待表格渲染完成（与 Stage 1 发现环境一致）
        from lib.wait_helpers import wait_for_table_ready
        try:
            await wait_for_table_ready(page, timeout=15000)
            print("  ✅ 表格已就绪")
        except Exception as e:
            print(f"  \u26a0\ufe0f \u7b49\u5f85\u8868\u683c\u8d85\u65f6: {e}")

        marker = None
        results = []
        for op_name in ops:
            marker, op_result = await run_operation(page, op_name, operations_data, marker)
            results.append(op_result)

        report_path = generate_html_report(results, "用户管理")
        print(f"\n{'='*60}")
        print(f"  ✅ 测试完成")
        print(f"{'='*60}")
        print(f"  📊 报告: {report_path}")

        if not (args.headless or CONFIG["headless"]):
            try:
                input("\n按 Enter 关闭浏览器...")
            except (EOFError, KeyboardInterrupt):
                pass  # 非交互模式自动跳过
        await browser.close()


if __name__ == "__main__":
    from datetime import datetime
    asyncio.run(main())
