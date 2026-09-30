"""
const.py — module_discovery 常量定义

按钮探测策略、CRUD 关键词表、表单填充规则、API 分类关键词。
"""

import json as _json
import re as _re
from pathlib import Path as _Path

# ============================================================
# Stage 1: 按钮探测 — CSS 选择器集合
# ============================================================
BUTTON_SELECTORS = """
    button,
    a[href],
    .el-button,
    .el-button--text,
    .el-dropdown-menu__item,
    .el-table__body-wrapper a,
    .el-table__body-wrapper .el-button,
    .el-table__body-wrapper span[class*="action"],
    .el-menu-item,
    .el-submenu__title,
    [role="button"],
    [role="menuitem"],
    [role="link"],
    [class*="btn"],
    [class*="Btn"],
    [class*="action"],
    [class*="Action"],
    .ant-btn,
    .ant-menu-item,
    .ant-dropdown-menu-item,
    span[onclick]
"""

# 统一的选择器字符串（供 _scan_hints / _scan_iframes 使用）
# 从 BUTTON_SELECTORS 多行格式派生，去除空白并合并为逗号分隔
BUTTON_SELECTORS_STR = ", ".join(
    s.strip().rstrip(",") for s in BUTTON_SELECTORS.split("\n") if s.strip()
)

# ============================================================
# 按钮文本常量（去空格归一化匹配）
# 所有文本均为无空格的标准形式，匹配时统一去空格后比较
# ============================================================

# 提交按钮文本（表单提交时按优先级尝试）
SUBMIT_TEXTS = ['确定', '保存', '提交', '确认', '立即创建',
                '完成', '更新', '修改', 'OK', 'Update', 'Save']

# 确认对话框按钮文本（通用确认，不含业务操作名）
CONFIRM_TEXTS = ['确定', '确认', '是', 'OK', 'Yes']

# 取消对话框按钮文本
CANCEL_TEXTS = ['取消', 'Cancel', '否']

# 预构建 set（供 Python 侧 in 操作，避免重复构建）
SUBMIT_TEXTS_SET = set(SUBMIT_TEXTS)
CONFIRM_TEXTS_SET = set(CONFIRM_TEXTS)
CANCEL_TEXTS_SET = set(CANCEL_TEXTS)


# ============================================================
# 按钮文本匹配 — 跨语言表达式生成器
# 统一策略：去空格后与标准文本列表比较
# ============================================================
def normalize_button_text(text: str) -> str:
    """Python 侧：去除所有空白字符，返回归一化文本。

    用于 Python 侧按钮文本比较：
        if normalize_button_text(btn_text) in SUBMIT_TEXTS_SET:
    """
    return _re.sub(r'\s+', '', text)


def js_normalize_in(texts: list, js_var: str = "btnText") -> str:
    """生成 JS 表达式：检查去空格后的变量是否在文本列表中。

    Args:
        texts: 标准文本列表
        js_var: JS 中要比较的变量名（默认 "btnText"）

    Returns:
        JS 表达式字符串，可直接嵌入 page.evaluate() 的 JS 代码中。
        调用方需确保 js_var 已被赋值为 textContent.trim()。

    Example:
        >>> js_normalize_in(['确定', '保存'], 'txt')
        "['确定','保存'].includes(txt.replace(/\\s+/g, ''))"
    """
    texts_json = _json.dumps(texts, ensure_ascii=False)
    return f"{texts_json}.includes({js_var}.replace(/\\s+/g, ''))"


def xpath_normalize_in(texts: list) -> str:
    """生成 XPath 谓词：检查去空格后的文本是否匹配列表中任一。

    使用 XPath 1.0 translate() 函数去除空格后做 contains 匹配。

    Args:
        texts: 标准文本列表

    Returns:
        XPath 谓词字符串（不含外层 []），可直接嵌入 XPath 表达式中。

    Example:
        >>> xpath_normalize_in(['确定', '确认'])
        "(contains(translate(normalize-space(.), ' ', ''), '确定') or contains(translate(normalize-space(.), ' ', ''), '确认'))"
    """
    # normalize-space(.) 合并连续空白 + 去首尾，translate 去掉剩余空格
    ns = "translate(normalize-space(.), ' ', '')"
    parts = [f"contains({ns}, '{t}')" for t in texts]
    return "(" + " or ".join(parts) + ")"

# 写操作 HTTP 方法（通用规则，不依赖操作名）
WRITE_HTTP_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# 写操作类别（行为驱动分类结果，用于 Stage 3 依赖推导）
# 任何触发 POST/PUT/PATCH/DELETE 的分类结果都算写操作
WRITE_CATEGORIES = {"create", "update", "delete", "state_change"}


# ============================================================
# Stage 1: 表单元素类型 → KB category 映射
# ============================================================
ELEMENT_TYPE_MAP = {
    "input": "input-generic",
    "textarea": "textarea-generic",
    "select": "el-select",
    "cascader": "el-cascader",
    "date-picker": "date-picker",
    "radio": "radio",
    "checkbox": "form-checkbox",
    "tree": "el-tree",
}

# 需要多步操作的组件类型（由 MultiStepExecutor 处理）
MULTI_STEP_TYPES = ["el-select", "el-cascader", "date-picker", "list-selector"]

# ============================================================
# 统一 Locator 增强 — 隐藏过滤器
# ============================================================
HIDDEN_FILTERS = {
    'element-ui': (
        "not(ancestor-or-self::*[contains(@class,'is-hidden')])"
        " and not(ancestor-or-self::*[contains(@style,'display: none')])"
        " and not(@disabled)"
        " and not(ancestor-or-self::*[contains(@class,'is-disabled')])"
    ),
    'ant-design': (
        "not(ancestor-or-self::*[contains(@class,'ant-drawer-hidden')])"
        " and not(ancestor-or-self::*[contains(@class,'ant-modal-hidden')])"
        " and not(ancestor-or-self::*[contains(@style,'display: none')])"
        " and not(ancestor-or-self::*[@aria-hidden='true'])"
        " and not(@disabled)"
        " and not(ancestor-or-self::*[contains(@class,'ant-btn-disabled')])"
        " and not(ancestor-or-self::*[contains(@class,'ant-select-disabled')])"
    ),
    '_universal': (
        "not(ancestor-or-self::*[contains(@style,'display: none')])"
        " and not(@disabled)"
    ),
}

# CSS 选择器等价隐藏过滤（用于 Playwright CSS locator）
HIDDEN_FILTERS_CSS = {
    'element-ui': ':not(.is-hidden):not(.is-disabled):not([disabled]):not([style*="display: none"])',
    'ant-design': ':not(.ant-drawer-hidden):not(.ant-modal-hidden):not([aria-hidden="true"]):not([disabled]):not([style*="display: none"])',
    '_universal': ':not([disabled]):not([style*="display: none"])',
}

# ============================================================
# 统一 Locator 增强 — 覆盖层前缀（弹窗/抽屉）
# ============================================================
OVERLAY_SELECTORS = {
    'element-ui': [
        ('el-dialog',   "//div[contains(@class,'el-dialog') and not(contains(@style,'display: none'))]"),
        ('el-drawer',   "//div[contains(@class,'el-drawer') and not(contains(@style,'display: none'))]"),
        ('el-message-box', "//div[contains(@class,'el-message-box') and not(contains(@style,'display: none'))]"),
    ],
    'ant-design': [
        ('ant-modal',   "//div[contains(@class,'ant-modal-content')]"),
        ('ant-drawer',  "//div[contains(@class,'ant-drawer-content')]"),
    ],
}

# ============================================================
# Stage 2: API 过滤规则
# ============================================================
SKIP_STATIC_EXTENSIONS = (".js", ".css", ".png", ".jpg", ".svg",
                          ".woff", ".woff2", ".ttf", ".ico", ".map",
                          ".gif", ".webp", ".mp4", ".pdf")

# ============================================================
# Stage 3: 依赖注入字段名
# ============================================================


# ============================================================
# Stage 3: 响应约定发现 — 候选常量
# （集中管理，避免 analyze_flow.py / test_runtime.py 各自定义）
# ============================================================

# 响应信封键候选（按常见度排序）
ENVELOPE_KEY_CANDIDATES = [
    "entity", "data", "result", "payload", "body", "content", "response",
]

# 成功判断相关字段
SUCCESS_FIELD_CANDIDATES = ["success", "ok", "code", "status"]
ERROR_FIELD_CANDIDATES = ["errorCode", "error_code", "errCode", "err_code", "error"]

# 列表项键候选
LIST_KEY_CANDIDATES = ["list", "records", "rows", "items", "data", "content", "results"]
TOTAL_KEY_CANDIDATES = ["total", "totalCount", "totalElements", "count", "total_count"]

# 名称字段标识符（字段名包含这些关键词 且 值是短字符串）
# 注：仅作初始分类辅助，最终由值模式分析（_analyze_value_pattern）决定
NAME_FIELD_KEYWORDS = ["name", "title", "label", "displayname", "username", "account"]
# 可变字段标识符（测试时需要生成唯一值，避免与已有数据冲突）
# 注：仅作初始分类辅助，最终由值模式分析（_analyze_value_pattern）决定
MUTABLE_FIELD_KEYWORDS = ["description", "remark", "memo", "note", "comment", "desc"]


# ============================================================
# Stage 3: 信封键回退默认值
# （当响应样本中无法自动发现时使用，不偏向任何特定项目）
# ============================================================
ENVELOPE_KEY_DEFAULTS = ["entity", "data", "result", "payload"]
LIST_KEY_DEFAULTS = ["list", "records", "rows", "items"]
TOTAL_KEY_DEFAULTS = ["total", "totalCount", "count"]
DEFAULT_ID_FIELD = "id"

# ============================================================
# 登录表单选择器默认值（可通过 profile.yaml 的 login_flow 覆盖）
# ============================================================
DEFAULT_LOGIN_USERNAME_SELECTOR = 'input[type="text"], input[name*="user"], input[name*="account"]'
DEFAULT_LOGIN_PASSWORD_SELECTOR = 'input[type="password"]'

# 响应成功检查默认值（当 response_contract 未提供时使用）
SUCCESS_CHECK_DEFAULT = {
    "type": "field_and_absence",
    "success_field": "success",
    "error_field": "errorCode",
}

# 搜索操作名称关键词（用于验证端点选择）
# 匹配逻辑：操作名包含这些关键词即视为搜索操作
SEARCH_ACTION_KEYWORDS = frozenset({
    "搜索", "查询", "search", "query", "find", "list", "列表"
})

# 批量响应子项失败状态值（大写匹配）
# 当 entity 是数组时，检查每个子项的 state 字段是否包含这些值
BATCH_ITEM_FAILURE_STATES = frozenset({"ERROR", "FAILED", "FAIL"})

# 批量响应子项状态字段候选名（按优先级排列）
# 用于检查批量操作中每个子项的执行状态
BATCH_ITEM_STATE_FIELDS = ("state", "status", "resultState")

# 值链提取排除字段（不参与值链匹配的字段名）
# 精确匹配（非子串），因此 "order" 不会误排除 "orderId"
EXTRACT_EXCLUDE_KEYS = frozenset({
    # 分页字段
    "pageNum", "pageSize", "page", "size", "offset", "limit",
    "startRow", "endRow", "pages",
    # 排序字段
    "sort", "order",
    # 通用元数据（通常是噪声而非值链候选）
    "status", "name", "userName", "displayName",
    "description", "remark", "memo",
    # 安全字段（不应出现在值链中）
    "secretKey", "password", "token", "accessToken",
})

# ============================================================
# 通知消息语义分类（用于 assert_success / _verify_operation_success）
# ============================================================

# 成功关键词
NOTIFICATION_SUCCESS_KEYWORDS = frozenset({
    "成功", "完成", "已保存", "已添加", "已删除", "已更新",
    "操作成功", "success", "done", "completed",
})

# 失败关键词
NOTIFICATION_FAILURE_KEYWORDS = frozenset({
    "失败", "错误", "异常", "error", "failed",
    "无权", "权限不足", "不存在", "不允许", "无法",
    "超时", "timeout",
})

# 进行中关键词（操作尚未完成，不作为最终结果）
NOTIFICATION_PROCESSING_KEYWORDS = frozenset({
    "处理中", "请稍后", "正在", "processing", "loading",
})


def classify_notification(text: str) -> str:
    """对通知文本做语义分类。

    用于 assert_success (Stage 2 replay) 和 _verify_operation_success (Stage 1 detect)
    统一判断通知消息的含义。

    Args:
        text: 通知文本内容

    Returns:
        'success'    — 明确的成功信号
        'failure'    — 明确的失败信号
        'processing' — 操作进行中（非最终结果）
        'neutral'    — 无法判断（信息提示等）
    """
    if not text:
        return "neutral"
    t = text.lower()
    # 失败优先（"操作失败"不应被其他关键词覆盖）
    if any(kw.lower() in t for kw in NOTIFICATION_FAILURE_KEYWORDS):
        return "failure"
    if any(kw.lower() in t for kw in NOTIFICATION_SUCCESS_KEYWORDS):
        return "success"
    if any(kw.lower() in t for kw in NOTIFICATION_PROCESSING_KEYWORDS):
        return "processing"
    return "neutral"


# ============================================================
# UI 选择器注册表加载器
# ============================================================

_UI_SELECTORS_DIR = _Path(__file__).parent / "ui_selectors"
_ui_selectors_cache = {}


def get_ui_selectors(framework: str = "element-ui") -> dict:
    """
    从 ui_selectors/ 目录加载指定框架的选择器配置。

    Args:
        framework: UI 框架名称，如 "element-ui" 或 "ant-design"

    Returns:
        选择器配置字典

    Raises:
        FileNotFoundError: 如果指定的框架配置文件不存在
    """
    if framework in _ui_selectors_cache:
        return _ui_selectors_cache[framework]

    config_path = _UI_SELECTORS_DIR / f"{framework}.json"
    if not config_path.exists():
        raise FileNotFoundError(f"UI 选择器配置文件不存在：{config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        selectors = _json.load(f)

    _ui_selectors_cache[framework] = selectors
    return selectors


def get_button_selectors_str(framework: str = "element-ui") -> str:
    """
    获取按钮选择器字符串（用于 Playwright page.locator）。

    Args:
        framework: UI 框架名称

    Returns:
        逗号分隔的选择器字符串
    """
    selectors = get_ui_selectors(framework)
    generic = selectors.get("button", {}).get("generic_selectors", [])
    base = selectors.get("button", {}).get("base", "")
    text = selectors.get("button", {}).get("text", "")

    all_selectors = [base, text] + generic
    return ", ".join(s for s in all_selectors if s)

