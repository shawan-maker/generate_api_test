#!/usr/bin/env python3
"""
角色管理 - UI 自动化测试脚本

生成时间: 2026-09-29 11:11:51
生成工具: API AI Test Framework - Stage 2
版本: v1.0.0

用法:
    python 角色管理.py                    # 运行所有操作
    python 角色管理.py create update      # 只运行 create 和 update
    python 角色管理.py --headless         # 无头模式

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
    "target_url": "https://10.151.61.248/estack/web/estack/user-center/user-manage/role",
    "headless": False,
    "slow_mo": 100,
    # 鉴权配置（cookie_client 统一使用）
    "auth_config": {
        "token_key": "accessToken",
        "token_storage": "localStorage",
        "cookie_token_key": "accessToken",
    },
}

AVAILABLE_OPERATIONS = ['创建角色', 'query', '编辑', '删除']

# Playbook 数据内嵌到脚本中（不依赖外部 JSON 文件）
_PLAYBOOK_JSON = '{"meta": {"module_name": "角色管理", "target_url": "https://10.151.61.248/estack/web/estack/user-center/user-manage/role", "base_url": "https://10.151.61.248", "login_url": "https://10.151.61.248/estack/web/estack/login", "framework": "element-ui", "generated_at": "2026-09-29 11:11:51", "version": "1.0", "auth_config": {"token_key": "accessToken", "token_storage": "localStorage", "cookie_token_key": "accessToken"}}, "page_structure": {"hasFixedLeft": true, "hasFixedRight": true, "mainBodyRows": 10, "fixedRightRows": 10, "fixedLeftRows": 10, "columnCount": 7, "operationColumnIndex": 6, "operationColumnLocation": "last", "tableWrappers": [{"cls": "el-table__header-wrapper", "rowCount": 0}, {"cls": "el-popover__reference-wrapper", "rowCount": 0}, {"cls": "el-popover__reference-wrapper", "rowCount": 0}, {"cls": "el-table__body-wrapper is-scrolling-none", "rowCount": 10}, {"cls": "el-table__fixed-header-wrapper", "rowCount": 0}, {"cls": "el-popover__reference-wrapper", "rowCount": 0}, {"cls": "el-popover__reference-wrapper", "rowCount": 0}, {"cls": "el-table__fixed-body-wrapper", "rowCount": 10}, {"cls": "el-table__fixed-header-wrapper", "rowCount": 0}, {"cls": "el-popover__reference-wrapper", "rowCount": 0}]}, "operations": {"创建角色": {"display_name": "创建角色", "description": "创建角色", "role": "create", "steps": [{"action": "click_button", "playwright_locator": "button:has-text(\\"创建角色\\")", "description": "点击创建按钮"}, {"action": "wait_for_dialog", "playwright_locator": ".el-dialog__wrapper", "interaction_mode": "page-nav", "description": "等待创建对话框"}, {"action": "fill_form", "fields": [{"label": "角色名称", "playwright_locator": "#app > div > section > section > main > div > div > div:nth-of-type(2) > div > form > div:nth-of-type(2) > div > div:nth-of-type(1) > input", "type": "input", "kb_category": "input-generic", "fill_rule": {"rule": "name_pattern", "params": {"prefix": "test"}}, "is_marker": true}, {"label": "描述", "playwright_locator": "#app > div > section > section > main > div > div > div:nth-of-type(2) > div > form > div:nth-of-type(4) > div > div > textarea:nth-of-type(1)", "type": "textarea", "kb_category": "textarea-generic", "fill_rule": {"rule": "description_pattern", "params": {"prefix": "auto"}}}, {"label": "策略内容", "playwright_locator": ".el-form-item:nth-child(8) .el-select", "type": "select", "kb_category": "el-select", "fill_rule": {}, "is_editable": true, "option_text": "WORKFLOW(工作流)"}, {"label": "产品/服务", "playwright_locator": ".el-form-item:nth-child(2) .el-select", "type": "select", "kb_category": "el-select", "fill_rule": {}, "is_editable": false, "option_text": "WORKFLOW(工作流)"}, {"label": "关联菜单", "playwright_locator": ".el-form-item:nth-child(10) .el-checkbox", "type": "form-checkbox", "kb_category": "form-checkbox", "fill_rule": {}, "is_editable": false, "option_text": ""}, {"label": "角色范畴", "playwright_locator": ".el-form-item:nth-child(4) .el-radio-group", "type": "radio", "kb_category": "radio", "fill_rule": {}, "option_text": ""}, {"label": "配置模式", "playwright_locator": ".el-form-item:nth-child(7) .el-radio-group", "type": "radio", "kb_category": "radio", "fill_rule": {}, "option_text": ""}, {"label": "效果", "playwright_locator": ".el-form-item:nth-child(1) .el-radio-group", "type": "radio", "kb_category": "radio", "fill_rule": {}, "option_text": ""}, {"label": "操作", "playwright_locator": ".el-form-item:nth-child(3) .el-radio-group", "type": "radio", "kb_category": "radio", "fill_rule": {}, "option_text": ""}], "description": "填充表单字段"}, {"action": "click_button", "playwright_locator": "button:has-text(\\"确定\\")", "text": "确定", "description": "点击提交按钮", "click_strategy": "js"}, {"action": "assert_success", "playwright_locator": ".el-message--success", "description": "验证创建成功"}], "marker": "AT_test_651406", "detection_status": "success", "replayable": true}, "query": {"display_name": "query", "description": "query", "role": "query", "steps": [{"action": "fill_input", "playwright_locator": "input[placeholder=\\"按角色名称搜索\\"]", "value": "{marker_name}", "description": "搜索框输入: 按角色名称搜索"}, {"action": "press_key", "key": "Enter", "description": "回车触发搜索"}, {"action": "wait_for_table_ready", "description": "等待表格数据刷新"}], "marker": null, "detection_status": "success", "replayable": true}, "编辑": {"display_name": "编辑", "description": "编辑", "role": "update", "steps": [{"action": "find_row", "description": "定位目标数据行"}, {"action": "click_row_button", "button_text": "编辑", "playwright_locator": "button:has-text(\'编辑\')", "description": "点击行内编辑按钮"}, {"action": "wait_for_dialog", "playwright_locator": ".el-dialog__wrapper", "interaction_mode": "page-nav", "description": "等待编辑对话框"}, {"action": "fill_form", "fields": [{"label": "角色名称", "playwright_locator": "#app > div > section > section > main > div > div > div:nth-of-type(2) > div > div:nth-of-type(3) > div > div:nth-of-type(2) > form > div:nth-of-type(1) > div > div:nth-of-type(1) > input", "type": "input", "kb_category": "input-generic", "fill_rule": {"rule": "name_pattern", "params": {"prefix": "test"}}, "is_marker": true}, {"label": "描述", "playwright_locator": "#app > div > section > section > main > div > div > div:nth-of-type(2) > div > div:nth-of-type(3) > div > div:nth-of-type(2) > form > div:nth-of-type(2) > div > div > textarea:nth-of-type(1)", "type": "textarea", "kb_category": "textarea-generic", "fill_rule": {"rule": "description_pattern", "params": {"prefix": "auto"}}}], "description": "填充编辑表单"}, {"action": "click_button", "playwright_locator": "button:has-text(\\"确 定\\")", "text": "确 定", "description": "点击提交按钮"}, {"action": "assert_success", "playwright_locator": ".el-message--success", "description": "验证更新成功"}], "marker": null, "detection_status": "success", "replayable": false}, "删除": {"display_name": "删除", "description": "删除", "role": "delete", "steps": [{"action": "find_row", "description": "定位目标数据行"}, {"action": "click_button", "text": "删除", "playwright_locator": "button:has-text(\'删除\')", "description": "点击删除按钮"}, {"action": "confirm_dialog", "description": "点击确认对话框"}, {"action": "assert_row_disappeared", "description": "验证数据行已消失"}], "marker": "AT_test_651406", "detection_status": "success", "replayable": true}}}'

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
_OPERATION_STATUS = {}

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

    parser = argparse.ArgumentParser(description="角色管理 UI 自动化测试脚本")
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

        report_path = generate_html_report(results, "角色管理")
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
