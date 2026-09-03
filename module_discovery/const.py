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

# 按优先级排序的 CRUD 执行顺序
CRUD_EXECUTION_ORDER = [
    "create",    # 必须第一
    "query",     # 查初始数据
    "detail",    # 查看详情
    "update",    # 修改
    "lock",      # 锁定/冻结（互斥）
    "unlock",    # 解锁/启用
    "reset",     # 重置密码
    "import",    # 批量导入
    "export",    # 导出
    "execute",   # 其他操作
    "authorize", # 授权
    "migrate",   # 迁移
    "delete",    # 必须最后
]

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

# 辅助 API 路径关键词（出现在所有按钮点击场景中的基础设施 API）
SUPPORTING_API_KEYWORDS = [
    "/menu/", "/theme", "/favorite", "/dynamic-dictionary",
    "/notice/", "/access-log", "/system-theme",
    "/current-user", "/authority/",
    "/dictionary", "/role/", "/permission/",
]

# ============================================================
# Stage 3: API → CRUD 类别映射
# ============================================================
API_CRUD_KEYWORDS = {
    "create":    ["/create", "/add", "/save", "/register", "/apply", "/order"],
    "delete":    ["/delete", "/remove", "/destroy", "/release"],
    "update":    ["/update", "/edit", "/modify", "/change", "/rename"],
    "detail":    ["/detail", "/get", "/info", "/view"],
    "query":     ["/list", "/page", "/search", "/query", "/find",
                  "/all", "/select"],
    "lock":      ["/lock", "/freeze", "/disable", "/stop", "/suspend"],
    "unlock":    ["/unlock", "/enable", "/activate", "/resume", "/start"],
    "export":    ["/export", "/download"],
    "import":    ["/import", "/upload"],
    "batch":     ["/batch", "/batch-delete", "/batch-update"],
    "reset":     ["/reset", "/reset-password"],
    # 角色/策略管理相关（estack 风格的命名）
    "role":      ["/policies", "/roles", "/permissions", "/authorities"],
}

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
COMMON_ID_FIELDS = [
    "id", "userId", "ids", "resourceId", "projectId",
    "tenantId", "groupId", "roleId", "policyId",
    "instanceId", "volumeId", "networkId",
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
NAME_FIELD_KEYWORDS = ["name", "title", "label", "displayname", "username", "account"]
# 可变字段标识符
MUTABLE_FIELD_KEYWORDS = ["description", "remark", "memo", "note", "comment", "desc"]


# ============================================================
# Stage 3: 信封键回退默认值
# （当响应样本中无法自动发现时使用，不偏向任何特定项目）
# ============================================================
ENVELOPE_KEY_DEFAULTS = ["entity", "data", "result", "payload"]
LIST_KEY_DEFAULTS = ["list", "records", "rows", "items"]
TOTAL_KEY_DEFAULTS = ["total", "totalCount", "count"]
DEFAULT_ID_FIELD = "id"
