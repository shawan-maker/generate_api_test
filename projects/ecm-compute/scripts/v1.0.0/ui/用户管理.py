#!/usr/bin/env python3
"""
用户管理 - UI 自动化测试脚本

生成时间: 2026-09-03 09:09:01
生成工具: API AI Test Framework - Stage 2

用法:
    python 用户管理.py                    # 运行所有操作
    python 用户管理.py create update      # 只运行 create 和 update
    python 用户管理.py --headless         # 无头模式
    python 用户管理.py --data custom.json # 使用自定义数据文件

依赖:
    pip install playwright
    playwright install chromium
"""

import json
import asyncio
import argparse
import time
from pathlib import Path
from playwright.async_api import async_playwright

# ==================== 配置 ====================

CONFIG = {
    "base_url": "",
    "login_url": "",
    "target_url": "https://10.151.61.248/estack/web/estack/user-center/user-manage/user",
    "username": "estack-yy",  # 可从环境变量 APP_USER 读取
    "password": "R@9eDuck$!mpleM00n",  # 可从环境变量 APP_PASS 读取
    "headless": False,
    "slow_mo": 100,
}

# Playbook 操作定义（从 Stage 1 生成）
PLAYBOOK_OPERATIONS = {
    "create": {
        "description": "create",
        "steps": [
            {
                "action": "click_button",
                "playwright_locator": "button:has-text('创建用户')",
                "description": "点击创建按钮"
            },
            {
                "action": "wait_for_dialog",
                "playwright_locator": ".el-dialog__wrapper",
                "interaction_mode": "page-nav",
                "description": "等待创建对话框"
            },
            {
                "action": "fill_form",
                "fields": [
                    {
                        "label": "账号",
                        "playwright_locator": "div > div:nth-of-type(2) > div > div > input",
                        "type": "input",
                        "kb_category": "input-generic",
                        "fill_rule": {
                            "rule": "username_pattern",
                            "params": {
                                "prefix": "autotest"
                            }
                        },
                        "is_marker": true
                    },
                    {
                        "label": "姓名",
                        "playwright_locator": "div > div:nth-of-type(3) > div > div > input",
                        "type": "input",
                        "kb_category": "input-generic",
                        "fill_rule": {
                            "rule": "name_pattern",
                            "params": {
                                "prefix": "test"
                            }
                        }
                    },
                    {
                        "label": "密码",
                        "playwright_locator": "div > div:nth-of-type(4) > div > div > input",
                        "type": "input",
                        "kb_category": "input-generic",
                        "fill_rule": {
                            "rule": "password_fixed",
                            "params": {
                                "value": "Test@123456"
                            }
                        }
                    },
                    {
                        "label": "确认密码",
                        "playwright_locator": "div > div:nth-of-type(5) > div > div > input",
                        "type": "input",
                        "kb_category": "input-generic",
                        "fill_rule": {
                            "rule": "password_fixed",
                            "params": {
                                "value": "Test@123456"
                            }
                        }
                    },
                    {
                        "label": "手机号",
                        "playwright_locator": "div > div:nth-of-type(6) > div > div > input",
                        "type": "input",
                        "kb_category": "input-generic",
                        "fill_rule": {
                            "rule": "phone_pattern",
                            "params": {
                                "prefix": "138"
                            }
                        }
                    },
                    {
                        "label": "邮箱",
                        "playwright_locator": "div > div:nth-of-type(7) > div > div > input",
                        "type": "input",
                        "kb_category": "input-generic",
                        "fill_rule": {
                            "rule": "email_pattern",
                            "params": {
                                "domain": "test.com"
                            }
                        }
                    },
                    {
                        "label": "描述",
                        "playwright_locator": "div > div:nth-of-type(8) > div > div > textarea",
                        "type": "textarea",
                        "kb_category": "textarea-generic",
                        "fill_rule": {
                            "rule": "description_pattern",
                            "params": {
                                "prefix": "auto"
                            }
                        }
                    },
                    {
                        "label": "系统角色",
                        "playwright_locator": ".el-form-item:nth-child(2) .el-select",
                        "type": "select",
                        "kb_category": "el-select",
                        "fill_rule": {},
                        "is_editable": true,
                        "option_text": "普通用户"
                    },
                    {
                        "label": "部门角色",
                        "playwright_locator": ".el-form-item:nth-child(3) .el-select",
                        "type": "select",
                        "kb_category": "el-select",
                        "fill_rule": {},
                        "is_editable": false,
                        "option_text": ""
                    }
                ],
                "description": "填充表单字段"
            },
            {
                "action": "click_button",
                "playwright_locator": "button:has-text('确 定')",
                "text": "确 定",
                "description": "点击提交按钮"
            },
            {
                "action": "assert_success",
                "playwright_locator": ".el-message--success",
                "description": "验证创建成功"
            }
        ],
        "marker": "test",
        "status": "success"
    },
    "update": {
        "description": "update",
        "steps": [
            {
                "action": "find_row",
                "description": "定位目标数据行"
            },
            {
                "action": "click_row_button",
                "button_text": "编辑",
                "description": "点击行内编辑按钮"
            },
            {
                "action": "wait_for_dialog",
                "playwright_locator": ".el-dialog__wrapper",
                "interaction_mode": "page-nav",
                "description": "等待编辑对话框"
            },
            {
                "action": "fill_form",
                "fields": [
                    {
                        "label": "姓名",
                        "playwright_locator": "#app > div > section > section > main > div > div > div:nth-of-type(2) > div > div:nth-of-type(3) > div > div:nth-of-type(2) > form > div:nth-of-type(2) > div > div > input",
                        "type": "input",
                        "kb_category": "input-generic",
                        "fill_rule": {
                            "rule": "name_pattern",
                            "params": {
                                "prefix": "test"
                            }
                        }
                    },
                    {
                        "label": "邮箱",
                        "playwright_locator": "#app > div > section > section > main > div > div > div:nth-of-type(2) > div > div:nth-of-type(3) > div > div:nth-of-type(2) > form > div:nth-of-type(3) > div > div > input",
                        "type": "input",
                        "kb_category": "input-generic",
                        "fill_rule": {
                            "rule": "email_pattern",
                            "params": {
                                "domain": "test.com"
                            }
                        }
                    },
                    {
                        "label": "手机号",
                        "playwright_locator": "#app > div > section > section > main > div > div > div:nth-of-type(2) > div > div:nth-of-type(3) > div > div:nth-of-type(2) > form > div:nth-of-type(4) > div > div > input",
                        "type": "input",
                        "kb_category": "input-generic",
                        "fill_rule": {
                            "rule": "phone_pattern",
                            "params": {
                                "prefix": "138"
                            }
                        }
                    },
                    {
                        "label": "描述",
                        "playwright_locator": "#app > div > section > section > main > div > div > div:nth-of-type(2) > div > div:nth-of-type(3) > div > div:nth-of-type(2) > form > div:nth-of-type(5) > div > div > textarea:nth-of-type(1)",
                        "type": "textarea",
                        "kb_category": "textarea-generic",
                        "fill_rule": {
                            "rule": "description_pattern",
                            "params": {
                                "prefix": "auto"
                            }
                        }
                    }
                ],
                "description": "填充编辑表单"
            },
            {
                "action": "click_button",
                "playwright_locator": "button:has-text('确 定')",
                "text": "确 定",
                "description": "点击提交按钮"
            },
            {
                "action": "assert_success",
                "playwright_locator": ".el-message--success",
                "description": "验证更新成功"
            }
        ],
        "marker": null,
        "status": "success"
    },
    "lock": {
        "description": "lock",
        "steps": [
            {
                "action": "find_row",
                "description": "定位目标数据行"
            },
            {
                "action": "click_row_more",
                "item_text": "冻结",
                "description": "点击更多菜单项: 冻结"
            },
            {
                "action": "confirm_dialog",
                "description": "点击确认对话框"
            },
            {
                "action": "assert_success",
                "playwright_locator": ".el-message--success",
                "description": "验证操作成功"
            }
        ],
        "marker": null,
        "status": "success"
    },
    "unlock": {
        "description": "unlock",
        "steps": [
            {
                "action": "find_row",
                "description": "定位目标数据行"
            },
            {
                "action": "click_row_more",
                "item_text": "启用",
                "description": "点击更多菜单项: 启用"
            },
            {
                "action": "confirm_dialog",
                "description": "点击确认对话框"
            },
            {
                "action": "assert_success",
                "playwright_locator": ".el-message--success",
                "description": "验证操作成功"
            }
        ],
        "marker": null,
        "status": "success"
    },
    "reset": {
        "description": "reset",
        "steps": [
            {
                "action": "find_row",
                "description": "定位目标数据行"
            },
            {
                "action": "click_row_more",
                "item_text": "重置密码",
                "description": "点击更多菜单项: 重置密码"
            },
            {
                "action": "confirm_dialog",
                "description": "点击确认对话框"
            },
            {
                "action": "assert_success",
                "playwright_locator": ".el-message--success",
                "description": "验证操作成功"
            }
        ],
        "marker": null,
        "status": "success"
    },
    "migrate": {
        "description": "migrate",
        "steps": [
            {
                "action": "click_button",
                "text": "批量迁移",
                "playwright_locator": "button:has-text('批量迁移')",
                "description": "点击操作按钮"
            },
            {
                "action": "assert_success",
                "playwright_locator": ".el-message--success",
                "description": "验证操作成功"
            }
        ],
        "marker": null,
        "status": "success"
    },
    "delete": {
        "description": "delete",
        "steps": [
            {
                "action": "find_row",
                "description": "定位目标数据行"
            },
            {
                "action": "select_row_checkbox",
                "checkbox_locator": ".el-checkbox__input",
                "description": "勾选行 checkbox"
            },
            {
                "action": "click_button",
                "text": "批量删除",
                "playwright_locator": "button:has-text('批量删除')",
                "description": "点击删除按钮"
            },
            {
                "action": "confirm_dialog",
                "description": "点击确认对话框"
            },
            {
                "action": "assert_row_disappeared",
                "description": "验证数据行已消失"
            }
        ],
        "marker": null,
        "status": "success"
    }
}

# ==================== 内置 Helper ====================

async def wait_for_dialog(page, locator=".el-dialog__wrapper", timeout=5000):
    """等待对话框出现"""
    await page.wait_for_selector(
        f"{locator}:not([style*='display: none'])",
        state="visible",
        timeout=timeout
    )
    await page.wait_for_timeout(500)


async def fill_form(page, fields, data):
    """填充表单字段"""
    filled = 0
    for field in fields:
        locator = field.get("playwright_locator")
        label = field.get("label")
        fill_rule = field.get("fill_rule")

        if not locator:
            continue

        # 确定填充值
        value = None
        if label in data and data[label] is not None:
            value = data[label]
        elif fill_rule:
            value = apply_fill_rule(fill_rule)

        if value is None:
            continue

        try:
            await page.fill(locator, str(value), timeout=3000)
            filled += 1
        except Exception as e:
            print(f"  ⚠️ 填充 {label} 失败: {e}")

    print(f"  ✓ 已填充 {filled}/{len(fields)} 个字段")
    return filled


def apply_fill_rule(rule):
    """应用填充规则生成值"""
    rule_type = rule.get("rule", "")
    params = rule.get("params", {})
    ts = int(time.time())

    if rule_type == "username_pattern":
        prefix = params.get("prefix", "autotest")
        return f"{prefix}_{ts}"
    elif rule_type == "password_fixed":
        return params.get("value", "Test@123456")
    elif rule_type == "email_pattern":
        domain = params.get("domain", "test.com")
        return f"auto_{ts}@{domain}"
    elif rule_type == "phone_pattern":
        prefix = params.get("prefix", "138")
        import random
        return f"{prefix}{random.randint(10000000, 99999999)}"
    elif rule_type == "code_pattern":
        prefix = params.get("prefix", "code")
        return f"{prefix}_{ts}"
    elif rule_type == "description_pattern":
        prefix = params.get("prefix", "auto")
        return f"{prefix}_{ts}"
    elif rule_type == "first_option":
        return None  # 标记为动态选择
    else:
        return f"test_{ts}"


async def click_confirm_dialog(page, confirm_text="确定"):
    """点击确认对话框"""
    # 检测三种确认框类型
    for _ in range(4):
        has_confirm = await page.evaluate("""() => {
            const msgBox = document.querySelector('.el-message-box__wrapper:not([style*="display: none"])');
            if (msgBox && msgBox.offsetWidth > 0) return 'message-box';

            const popconfirm = document.querySelector('.el-popconfirm:not([style*="display: none"])');
            if (popconfirm && popconfirm.offsetWidth > 0) return 'popconfirm';

            return null;
        }""")

        if has_confirm:
            break

        await page.wait_for_timeout(500)

    if not has_confirm:
        print("  ⚠️ 未检测到确认框")
        return False

    # 点击确认按钮
    if has_confirm == "message-box":
        locator = f".el-message-box__btns button:has-text('{confirm_text}')"
    else:
        locator = f".el-popconfirm__action button:has-text('{confirm_text}')"

    await page.click(locator, timeout=3000)
    return True


async def find_row_and_hover_dropdown(page, marker, dropdown_parent=".el-dropdown"):
    """定位数据行并展开下拉菜单"""
    # 找到包含 marker 的行
    row = await page.query_selector(f".el-table__body-wrapper tr:has-text('{marker}')")
    if not row:
        print(f"  ⚠️ 未找到数据行: {marker}")
        return False

    # 找到行内的"更多"按钮
    more_btn = await row.query_selector(dropdown_parent)
    if not more_btn:
        print(f"  ⚠️ 未找到下拉菜单触发器")
        return False

    # Hover 展开
    await more_btn.hover()
    await page.wait_for_timeout(600)

    # 验证展开
    expanded = await page.evaluate("""() => {
        const items = document.querySelectorAll('.el-dropdown-menu__item');
        return Array.from(items).some(el => el.offsetWidth > 0);
    }""")

    if not expanded:
        await more_btn.click()
        await page.wait_for_timeout(600)

    return True


async def select_row_checkbox(page, marker):
    """勾选数据行的 checkbox"""
    row = await page.query_selector(f".el-table__body-wrapper tr:has-text('{marker}')")
    if not row:
        return False

    checkbox = await row.query_selector(".el-checkbox__input")
    if not checkbox:
        return False

    await checkbox.click()
    await page.wait_for_timeout(300)
    return True


async def assert_success_message(page, locator=".el-message--success"):
    """验证成功消息"""
    try:
        await page.wait_for_selector(locator, state="visible", timeout=5000)
        print(f"  ✓ 操作成功")
        return True
    except:
        print(f"  ⚠️ 未检测到成功消息")
        return False


async def auto_login(page, context, login_url, username, password):
    """简化版自动登录（用户名+密码+点击登录）"""
    await page.goto(login_url, wait_until="networkidle")
    await page.wait_for_timeout(1000)

    # 填充用户名
    await page.fill("input[placeholder*='用户名'], input[placeholder*='账号']", username)

    # 填充密码
    await page.fill("input[type='password']", password)

    # 点击登录
    await page.click("button:has-text('登录'), button:has-text('Login')")

    # 等待登录完成
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(2000)

    # 检查是否成功登录
    if "login" in page.url:
        print("  ⚠️ 登录可能失败，请检查凭证")
        return False

    return True


# ==================== 操作执行器 ====================

async def run_operation(page, operation_name, data, marker=None):
    """执行单个操作"""
    op = PLAYBOOK_OPERATIONS.get(operation_name)
    if not op:
        print(f"⚠️ 操作不存在: {operation_name}")
        return None

    print(f"\n▶ 执行: {op.get('description', operation_name)}")

    steps = op.get("steps", [])
    new_marker = marker

    for step in steps:
        action = step.get("action")

        if action == "click_button":
            locator = step.get("playwright_locator")
            text = step.get("text")

            if locator:
                await page.click(locator, timeout=5000)
            else:
                await page.click(f"button:has-text('{text}')", timeout=5000)

        elif action == "wait_for_dialog":
            locator = step.get("playwright_locator", ".el-dialog__wrapper")
            timeout = step.get("timeout_ms", 5000)
            await wait_for_dialog(page, locator, timeout)

        elif action == "fill_form":
            fields = step.get("fields", [])
            op_data = data.get(operation_name, {})
            await fill_form(page, fields, op_data)

        elif action == "find_row":
            if not marker:
                print("  ⚠️ 缺少 marker，跳过")
                continue

        elif action == "hover_dropdown":
            if not marker:
                print("  ⚠️ 缺少 marker，跳过")
                continue
            parent = step.get("parent_selector", ".el-dropdown")
            await find_row_and_hover_dropdown(page, marker, parent)

        elif action == "click_dropdown_item":
            locator = step.get("playwright_locator")
            if locator:
                await page.click(locator, timeout=3000)

        elif action == "click_confirm_dialog":
            confirm_text = step.get("confirm_text", "确定")
            await click_confirm_dialog(page, confirm_text)

        elif action == "select_row_checkbox":
            if marker:
                await select_row_checkbox(page, marker)

        elif action == "assert_success":
            locator = step.get("playwright_locator", ".el-message--success")
            await assert_success_message(page, locator)

        await page.wait_for_timeout(500)

    return new_marker


# ==================== 主程序 ====================

async def main():
    parser = argparse.ArgumentParser(description="用户管理 UI 自动化测试脚本")
    parser.add_argument("operations", nargs="*", help="要执行的操作列表")
    parser.add_argument("--headless", action="store_true", help="无头模式")
    parser.add_argument("--data", help="数据文件路径")
    args = parser.parse_args()

    # 加载数据
    data_file = Path(args.data) if args.data else Path(__file__).parent / "用户管理_data.json"
    if data_file.exists():
        with open(data_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    else:
        print(f"⚠️ 数据文件不存在: {data_file}")
        data = {}

    # 确定要执行的操作
    operations = args.operations if args.operations else list(PLAYBOOK_OPERATIONS.keys())

    # 启动浏览器
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

        # 登录
        print("登录...")
        await auto_login(
            page, context,
            CONFIG["login_url"],
            CONFIG["username"],
            CONFIG["password"]
        )

        # 导航到目标页面
        print(f"导航到: {CONFIG['target_url']}")
        await page.goto(CONFIG["target_url"], wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # 执行操作
        marker = None
        for op_name in operations:
            marker = await run_operation(page, op_name, data, marker)

        # 等待用户确认
        input("\n按 Enter 关闭浏览器...")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
