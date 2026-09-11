"""
const.py — module_discovery 常量定义

按钮探测策略、CRUD 关键词表、表单填充规则、API 分类关键词。
"""

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
BUTTON_SELECTORS_STR = "button, a[href], .el-button, .el-button--text, .el-dropdown-menu__item, .el-table__body-wrapper a, .el-table__body-wrapper .el-button, .el-table__body-wrapper span[class*='action'], .el-menu-item, [role='button'], [role='menuitem'], [role='link'], [class*='btn'], [class*='Btn'], [class*='action'], [class*='Action'], .ant-btn, .ant-menu-item, .ant-dropdown-menu-item, span[onclick]"

# ============================================================
# Stage 1: 按钮文本→CRUD 类别映射
# ============================================================
ACTION_KEYWORDS = {
    "create":    ["新增", "创建", "添加", "新建", "增加", "录入", "登记",
                  "注册", "申请", "开通", "购买", "订购", "下达", "新規"],
    "delete":    ["删除", "移除", "清除", "销毁", "释放", "回收", "撤销"],
    "update":    ["编辑", "修改", "更改", "更新", "变更", "设置", "配置",
                  "调整", "改名", "重命名"],
    "query":     ["搜索", "查询", "查找", "筛选", "过滤", "检索",
                  "刷新", "翻页", "下一页", "上一页"],
    "lock":      ["锁定", "冻结", "封禁", "暂停", "停用", "禁用",
                  "关机", "关闭", "下架"],
    "unlock":    ["解锁", "解冻", "启用", "恢复", "开机", "开启", "激活",
                  "上架", "启用"],
    "reset":     ["重置", "重置密码", "修改密码", "初始化"],
    "authorize": ["授权", "赋予权限", "分配角色", "分配权限"],
    "migrate":   ["迁移", "迁移用户", "转移"],
    "export":    ["导出", "下载", "批量导出"],
    "import":    ["导入", "上传", "批量导入"],
    "batch":     ["批量", "批量操作", "批量删除", "批量编辑"],
    "approve":   ["审批", "通过", "同意", "驳回", "拒绝", "审核"],
    "confirm":   ["确定", "确认", "提交", "保存", "完成", "下一步",
                  "立即创建", "立即购买"],
    "reject":    ["驳回", "拒绝", "不同意"],
    "detail":    ["详情", "查看", "查看详情", "明细"],
    "execute":   ["执行", "运行", "触发", "操作", "更多"],
}

# 提交按钮文本（表单提交时按优先级尝试）
SUBMIT_BUTTON_TEXTS = ['确定', '保存', '提交', '确认', '立即创建',
                       '完成', '更新', '修改', 'OK', 'Update', 'Save']

# 提交按钮文本子集（Playwright locator 回退用）
SUBMIT_BUTTON_TEXTS_FALLBACK = ['确定', '保存', '提交', '确认', '完成', '更新', 'OK']

# 确认对话框按钮文本
CONFIRM_BUTTON_TEXTS = ['确定', '确认', '是', 'OK', 'Yes', '迁移', '授权', '提交', '保存']

# 取消对话框按钮文本
CANCEL_BUTTON_TEXTS = ['取消', 'Cancel', '否']

# 写操作集合（用于判断是否需要后置验证）
WRITE_OPERATIONS = ("create", "update", "delete", "lock", "unlock", "reset")


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
MULTI_STEP_TYPES = ["el-select", "el-cascader", "date-picker"]

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
# Stage 3: 状态字段检测
# ============================================================
STATE_FIELD_NAMES = [
    "state", "status", "enabled", "locked", "frozen",
    "状态", "启用状态", "锁定状态", "运行状态",
    "instanceStatus", "serviceStatus", "runStatus",
    "phase", "stage", "lifecycle",
]

STATE_LIKE_VALUES = {
    "ENABLE", "DISABLE", "ACTIVE", "INACTIVE",
    "LOCKED", "UNLOCKED", "NORMAL", "FROZEN",
    "CREATING", "DELETING", "DELETED",
    "RUNNING", "STOPPED",
    "Running", "Stopped",
    "运行中", "已停止", "正常", "停用", "锁定", "已删除",
    True, False,
}

# ============================================================
# Stage 3: 依赖注入字段名
# ============================================================
# 只保留通用 ID 字段，业务特定字段（tenantId, roleId, policyId 等）
# 通过策略 1（精确值匹配）自动发现，不再硬编码
COMMON_ID_FIELDS = [
    "id", "ids",
]


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
DEFAULT_LOGIN_USERNAME_SELECTOR = 'input[placeholder="用户名"]'
DEFAULT_LOGIN_PASSWORD_SELECTOR = 'input[placeholder="登录密码"]'

# ============================================================
# 测试数据生成 — 默认值
# （可通过 profile.yaml 的 test_data 字段覆盖）
# ============================================================
DEFAULT_TEST_PASSWORD = "Test@123456"
DEFAULT_TEST_EMAIL_DOMAIN = "test.com"
DEFAULT_TEST_PHONE_PREFIX = "138"
DEFAULT_TEST_NAME_PREFIX = "AT_"

# 响应成功检查默认值（当 response_contract 未提供时使用）
SUCCESS_CHECK_DEFAULT = {
    "type": "field_and_absence",
    "success_field": "success",
    "error_field": "errorCode",
}


# ============================================================
# UI 选择器注册表加载器
# ============================================================
import json as _json
from pathlib import Path as _Path

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

