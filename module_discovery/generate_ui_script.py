"""
generate_ui_script.py — UI 自动化脚本生成器

从 Stage 1 生成的 playbook.json 生成独立的 Playwright UI 自动化脚本。

生成的脚本特点：
- 纯 Playwright，不依赖项目内部库
- 包含内置 Helper 函数（约 150 行）
- 支持命令行参数（--headless, --data）
- 分离数据文件（{module_name}_data.json）
- 支持单个操作或全部操作执行
"""

import json
import logging
from pathlib import Path
from datetime import datetime

LOG = logging.getLogger("generate_ui_script")


def generate_ui_script(playbook: dict, module_name: str, project_dir: Path) -> tuple[Path, Path]:
    """生成 UI 自动化脚本和数据文件

    Args:
        playbook: Stage 1 生成的 playbook 数据
        module_name: 模块名称
        project_dir: 项目目录

    Returns:
        (script_path, data_path)
    """
    # 1. 生成数据文件
    data = _extract_test_data(playbook)
    data_path = project_dir / "scripts" / "v1.0.0" / "ui" / f"{module_name}_data.json"
    data_path.parent.mkdir(parents=True, exist_ok=True)

    with open(data_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    LOG.info(f"数据文件已生成: {data_path}")

    # 2. 生成脚本
    script_content = _render_script(playbook, module_name, data_path.name)
    script_path = project_dir / "scripts" / "v1.0.0" / "ui" / f"{module_name}.py"

    with open(script_path, 'w', encoding='utf-8') as f:
        f.write(script_content)
    LOG.info(f"UI 脚本已生成: {script_path}")

    return script_path, data_path


def _extract_test_data(playbook: dict) -> dict:
    """从 playbook 中提取测试数据

    生成简单的测试数据，动态数据（如用户名、时间戳）使用代码生成。
    """
    data = {}

    # 创建操作数据
    create_op = playbook.get("operations", {}).get("create", {})
    if create_op:
        create_data = {}
        steps = create_op.get("steps", [])

        for step in steps:
            if step.get("action") == "fill_form":
                fields = step.get("fields", [])
                for field in fields:
                    label = field.get("label")
                    field_type = field.get("type", "input")

                    if not label:
                        continue

                    # 根据字段类型生成测试数据
                    if "密码" in label or "password" in label.lower():
                        create_data[label] = "Test@123456"
                    elif "手机" in label or "phone" in label.lower():
                        create_data[label] = "13800138000"
                    elif "邮箱" in label or "email" in label.lower():
                        create_data[label] = "test@example.com"
                    elif "描述" in label or "备注" in label or "description" in label.lower():
                        create_data[label] = "UI 自动化测试数据"
                    elif "名称" in label or "name" in label.lower():
                        # 动态生成，使用代码
                        create_data[label] = None  # 标记为动态
                    elif "编码" in label or "code" in label.lower():
                        create_data[label] = None  # 标记为动态
                    else:
                        create_data[label] = f"test_{label}"

        data["create"] = create_data

    # 更新操作数据
    update_op = playbook.get("operations", {}).get("update", {})
    if update_op:
        update_data = {
            "描述": "auto_edited",
            "备注": "UI 自动化更新测试"
        }
        data["update"] = update_data

    return data


def _render_script(playbook: dict, module_name: str, data_filename: str) -> str:
    """渲染完整的 Playwright 脚本"""

    # 提取配置信息
    meta = playbook.get("meta", {})
    base_url = meta.get("base_url", "")
    login_url = meta.get("login_url", "")
    target_url = meta.get("target_url", "")
    framework = meta.get("framework", "element-ui")

    # 提取操作列表
    operations = playbook.get("operations", {})
    op_list = []
    for op_name, op_data in operations.items():
        op_list.append({
            "name": op_name,
            "description": op_data.get("description", op_name)
        })

    # 构建脚本内容
    script = f'''#!/usr/bin/env python3
"""
{module_name} - UI 自动化测试脚本

生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
生成工具: API AI Test Framework - Stage 2

用法:
    python {module_name}.py                    # 运行所有操作
    python {module_name}.py create update      # 只运行 create 和 update
    python {module_name}.py --headless         # 无头模式
    python {module_name}.py --data custom.json # 使用自定义数据文件

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

CONFIG = {{
    "base_url": "{base_url}",
    "login_url": "{login_url}",
    "target_url": "{target_url}",
    "username": "estack-yy",  # 可从环境变量 APP_USER 读取
    "password": "R@9eDuck$!mpleM00n",  # 可从环境变量 APP_PASS 读取
    "headless": False,
    "slow_mo": 100,
}}

# Playbook 操作定义（从 Stage 1 生成）
PLAYBOOK_OPERATIONS = {json.dumps(operations, ensure_ascii=False, indent=4)}

# ==================== 内置 Helper ====================

async def wait_for_dialog(page, locator=".el-dialog__wrapper", timeout=5000):
    """等待对话框出现"""
    await page.wait_for_selector(
        f"{{locator}}:not([style*='display: none'])",
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
            print(f"  ⚠️ 填充 {{label}} 失败: {{e}}")

    print(f"  ✓ 已填充 {{filled}}/{{len(fields)}} 个字段")
    return filled


def apply_fill_rule(rule):
    """应用填充规则生成值"""
    rule_type = rule.get("rule", "")
    params = rule.get("params", {{}})
    ts = int(time.time())

    if rule_type == "username_pattern":
        prefix = params.get("prefix", "autotest")
        return f"{{prefix}}_{{ts}}"
    elif rule_type == "password_fixed":
        return params.get("value", "Test@123456")
    elif rule_type == "email_pattern":
        domain = params.get("domain", "test.com")
        return f"auto_{{ts}}@{{domain}}"
    elif rule_type == "phone_pattern":
        prefix = params.get("prefix", "138")
        import random
        return f"{{prefix}}{{random.randint(10000000, 99999999)}}"
    elif rule_type == "code_pattern":
        prefix = params.get("prefix", "code")
        return f"{{prefix}}_{{ts}}"
    elif rule_type == "description_pattern":
        prefix = params.get("prefix", "auto")
        return f"{{prefix}}_{{ts}}"
    elif rule_type == "first_option":
        return None  # 标记为动态选择
    else:
        return f"test_{{ts}}"


async def click_confirm_dialog(page, confirm_text="确定"):
    """点击确认对话框"""
    # 检测三种确认框类型
    for _ in range(4):
        has_confirm = await page.evaluate("""() => {{
            const msgBox = document.querySelector('.el-message-box__wrapper:not([style*="display: none"])');
            if (msgBox && msgBox.offsetWidth > 0) return 'message-box';

            const popconfirm = document.querySelector('.el-popconfirm:not([style*="display: none"])');
            if (popconfirm && popconfirm.offsetWidth > 0) return 'popconfirm';

            return null;
        }}""")

        if has_confirm:
            break

        await page.wait_for_timeout(500)

    if not has_confirm:
        print("  ⚠️ 未检测到确认框")
        return False

    # 点击确认按钮
    if has_confirm == "message-box":
        locator = f".el-message-box__btns button:has-text('{{confirm_text}}')"
    else:
        locator = f".el-popconfirm__action button:has-text('{{confirm_text}}')"

    await page.click(locator, timeout=3000)
    return True


async def find_row_and_hover_dropdown(page, marker, dropdown_parent=".el-dropdown"):
    """定位数据行并展开下拉菜单"""
    # 找到包含 marker 的行
    row = await page.query_selector(f".el-table__body-wrapper tr:has-text('{{marker}}')")
    if not row:
        print(f"  ⚠️ 未找到数据行: {{marker}}")
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
    expanded = await page.evaluate("""() => {{
        const items = document.querySelectorAll('.el-dropdown-menu__item');
        return Array.from(items).some(el => el.offsetWidth > 0);
    }}""")

    if not expanded:
        await more_btn.click()
        await page.wait_for_timeout(600)

    return True


async def select_row_checkbox(page, marker):
    """勾选数据行的 checkbox"""
    row = await page.query_selector(f".el-table__body-wrapper tr:has-text('{{marker}}')")
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
        print(f"⚠️ 操作不存在: {{operation_name}}")
        return None

    print(f"\\n▶ 执行: {{op.get('description', operation_name)}}")

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
                await page.click(f"button:has-text('{{text}}')", timeout=5000)

        elif action == "wait_for_dialog":
            locator = step.get("playwright_locator", ".el-dialog__wrapper")
            timeout = step.get("timeout_ms", 5000)
            await wait_for_dialog(page, locator, timeout)

        elif action == "fill_form":
            fields = step.get("fields", [])
            op_data = data.get(operation_name, {{}})
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
    parser = argparse.ArgumentParser(description="{module_name} UI 自动化测试脚本")
    parser.add_argument("operations", nargs="*", help="要执行的操作列表")
    parser.add_argument("--headless", action="store_true", help="无头模式")
    parser.add_argument("--data", help="数据文件路径")
    args = parser.parse_args()

    # 加载数据
    data_file = Path(args.data) if args.data else Path(__file__).parent / "{data_filename}"
    if data_file.exists():
        with open(data_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    else:
        print(f"⚠️ 数据文件不存在: {{data_file}}")
        data = {{}}

    # 确定要执行的操作
    operations = args.operations if args.operations else list(PLAYBOOK_OPERATIONS.keys())

    # 启动浏览器
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=args.headless or CONFIG["headless"],
            slow_mo=CONFIG["slow_mo"]
        )
        context = await browser.new_context(
            viewport={{"width": 1920, "height": 1080}},
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
        print(f"导航到: {{CONFIG['target_url']}}")
        await page.goto(CONFIG["target_url"], wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # 执行操作
        marker = None
        for op_name in operations:
            marker = await run_operation(page, op_name, data, marker)

        # 等待用户确认
        input("\\n按 Enter 关闭浏览器...")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
'''

    return script
