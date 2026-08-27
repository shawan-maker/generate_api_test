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
    "export",    # 导出
    "execute",   # 其他操作
    "authorize", # 授权
    "migrate",   # 迁移
    "delete",    # 必须最后
]

# ============================================================
# Stage 1: 表单填充规则
# ============================================================
FORM_FILL_RULES = {
    "input[type='text']":           "AT_test_%s",
    "input[type='email']":          "at_%s@test.com",
    "input[type='tel']":            "138%s",
    "input[type='password']":       "Test@123456",
    "textarea":                     "自动创建于%s",
}

PLACEHOLDER_RULES = {
    "名称":  "AT_名称_%s",
    "用户名": "atuser_%s",
    "邮箱":  "at_%s@test.com",
    "手机":  "138%s",
    "电话":  "138%s",
    "描述":  "自动创建于%s",
    "备注":  "自动创建于%s",
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
