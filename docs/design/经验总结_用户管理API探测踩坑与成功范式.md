# 经验总结：用户管理 API 探测 —— 踩坑与成功范式

> 沉淀自 ecm-compute 项目「用户管理 → 创建用户」链路的端到端探测实战（2026-08-20 ~ 08-21）。
> 目的：让后续探测**任意模块 / 任意项目**时，直接复用这些教训，避免重蹈覆辙。
> 配套机器可读知识库：`config/probe_lessons_kb.json`（行为/领域/反模式 + 真实契约）；选择器模板已并入 `config/base_nav_kb.json`。

---

## 0. 背景

- **工具**：`module_discovery`（模块级 API 自动发现与测试生成器）。目标四步：
  1. 前端探测按钮/输入属性 → 2. 模拟点击抓取请求 URL 与参数 → 3. 分析按钮功能逻辑顺序（增查改删）→ 4. 生成 API 脚本 + 运行后状态检查。
- **被测**：estack 控制台 `https://10.151.37.249/estack/web/estack/user-center/user-manage/user`
- **账号**：`jcyz213-test / 9smkKAq@4`（普通员 / 项目管理员，`isAdmin=false`）

---

## 1. 完整错误时间线（按发生顺序）

每个条目：**现象 → 根因 → 修复 → 教训**。

### 1.1 cv2 在 import auth 时崩溃
- **现象**：`import lib.auth` 直接抛 cv2 加载失败。
- **根因**：`lib/slider.py` 顶层 `import cv2 / import numpy`，但运行环境没有 cv2。
- **修复**：改为函数内延迟导入 `_ensure_cv2()`。
- **教训**：带原生依赖的模块必须延迟导入，别在包顶层 import。

### 1.2 run.py 把 JS 的 `.catch` 写进了 Python
- **现象**：`.catch(lambda: None)` 语法错误。
- **根因**：JS 习惯带进了 Python。
- **修复**：全部改成 `try/except`。
- **教训**：Python 与 JS 混写时，回调/异常处理语法要分清楚。

### 1.3 选择器出现双重逗号 `,,`
- **现象**：拼接后的 XPath 含 `,,`，定位失败。
- **根因**：`const.BUTTON_SELECTORS` 每行末尾已有逗号，再用 `",".join` 又加了分隔符。
- **修复**：改用空格 join + `replace(",", "")` 清洗。
- **教训**：拼接选择器统一分隔符，最后做一次清洗。

### 1.4 `_scan_candidates` 的 f-string 卡死
- **现象**：字符串模板嵌套引号导致脚本卡死/报错。
- **根因**：f-string 内又嵌了 JS 双引号/单引号，转义冲突。
- **修复**：简化为直接的选择器字符串，不在 f-string 里拼 JS。
- **教训**：Python f-string 与内嵌 JS 的引号转义极易冲突，优先参数化传值。

### 1.5 提交按钮找不到（中文按钮文本含空格）
- **现象**：点开"创建用户"后找不到确定按钮。
- **根因**：按钮文本是 `确 定`（中间有空格），且位于底部 `div.order-submit` 内。
- **修复**：locator 改为 `//div[contains(@class,'order-submit')]//button[contains(.,'确 定')]`。
- **教训**：Element UI 中文按钮文本常被布局拆出空格，定位要用 `contains` 包容空格。→ 已沉淀为 `base_nav_kb.json` 的 `submit-order-button` 模板。

### 1.6 系统角色下拉必填被跳过
- **现象**：提交无 HTTP 请求。
- **根因**：系统角色是必填 `el-select`，脚本跳过它 → 表单校验不过。
- **修复**：新增 `_select_dropdowns` 强制选"普通用户"。
- **教训**：凡是带 `*` 的必填项，填表逻辑必须覆盖，不能"测别的先跳过"。

### 1.7 select 元素 setter 报 Illegal invocation
- **现象**：用 `setter.call` 给 Vue 组件设值报 illegal invocation。
- **根因**：对原生 select 元素用 JS setter 姿势不对。
- **修复**：改用 Playwright 原生 `fill` + 按 label XPath 定位。
- **教训**：能用 Playwright 原生 API 就别手写 JS setter，尤其 select / 组件值。

### 1.8 【用户批评】重写登录逻辑，浪费约 2 小时
- **现象**：花大量时间在 Python 里重造登录 + 滑块逻辑。
- **根因**：没先确认 EcsCloud 已有现成 `login.js`（`loginAndSaveCookies`，cookie 优先、滑块兜底）。
- **修复**：直接拷贝 EcsCloud 的登录/cookie 逻辑集成进 `lib/auth.py`。
- **教训**：**有现成逻辑先复用，不要重写**。动手前先 grep 参考项目。

### 1.9 【用户批评】无限循环重跑看日志
- **现象**：一个问题反复重跑脚本、贴日志，进展缓慢。
- **根因**：没有先定位根因就盲目重跑。
- **修复**：每次失败先读 DOM 状态/接口响应定位根因，最多 1-2 次验证。
- **教训**：**不要无限循环重跑**；先把"为什么失败"想清楚再动手。

### 1.10 【用户批评】应自己截图分析，而非让用户截图
- **现象**：让客户提供正确字段格式，自己不去看。
- **根因**：把诊断责任推给用户。
- **修复**：脚本关键步骤 `page.screenshot()` 落盘，自己读 DOM/调接口判断。
- **教训**：**自己诊断**。自动化脚本要自带"可观测性"（截图 + 状态断言）。

### 1.11 【用户批评】臆测创建 API 是 /create 或 /save
- **现象**：默认创建接口叫 create/save。
- **根因**：主观假设，没看实际抓取。
- **修复**：以 `page.on('request')` 抓到的真实 URL 为准 → 实际是 `POST /estack/draco/v1/users`。
- **教训**：**以实测为准，不臆测路径命名**。

### 1.12 填表格式错误 → 前端校验无 HTTP 请求
- **现象**：填了账号/手机号后点确定，报"账号不合法/手机号不合法"，无请求发出。
- **根因**：账号带下划线 `atuser_xxx`、手机号 `138xxxx`（位数不足/含字母）。
- **修复**：账号用纯字母数字无下划线、手机号用 11 位纯数字。
- **教训**：前端校验在非法时根本不发包，必须先让前端校验通过，才能看到真实 API。

### 1.13 模型无法真正读图
- **现象**：用 Read 打开截图 PNG，返回"内容已过滤/不支持"。
- **根因**：当前环境 Read 对图片不可解析。
- **修复**：诊断完全依赖 DOM 状态（`page.evaluate`）与后端接口响应，不依赖读图。
- **教训**：**模型读图不可靠，诊断靠 DOM + API**；截图仍要落盘给用户看，但自己别指望能"看"。

### 1.14 【核心假象】`/users/check` 缺 tenantId → 所有账号都"非法"
- **现象**：任意用户名（autotest01、jcyz-test-001、ATUSER2026…）都返回 `Invalid.Parameter`。
- **根因**：该接口**必须在 body 带 `tenantId`**，否则整体参数校验失败；前端原生请求没带。
- **修复**：在浏览器内注入 XHR/fetch 包装器，给所有 `/users` 请求自动补 `tenantId`+`adminId`。
- **教训**：**"账号格式非法"往往是缺必填字段的假象**，要逐字段用接口验证，而不是直接信前端红框文案。

### 1.15 Bearer token 来源搞错
- **现象**：手动用 localStorage `estackToken` 调接口返回 401/403。
- **根因**：`estackToken` 实际为空；真正的 Bearer token 是 cookie 里的 `accessToken` 值。
- **修复**：Bearer 取 `accessToken` cookie 值。
- **教训**：鉴权 token 的存储位置要看实测（`document.cookie` / `localStorage`），别想当然。

### 1.16 【最终定位】权限网关
- **现象**：`POST /users` 响应 `{"success":false,"errorMessage":"您没有创建用户权限，请先授权"}`。
- **根因**：账号 `jcyz213-test` 是项目管理员但 `isAdmin=false`，后端硬拦创建。
- **结论**：这是**系统规则**，不是代码 bug；探测（捕获契约）已成功，端到端"创建成功"需租户管理员账号。
- **教训**：响应里出现"无权限/请先授权"时，标注**权限门禁**，而非继续调代码。

---

## 2. 最终成功范式（可复用标准流程）

1. **鉴权**：cookie 优先（复用 EcsCloud `login.js`），失效才滑块兜底。
2. **Stage1 探测**：扫描按钮/下拉/输入，按 label 定位 form-item（含 textarea）。
3. **Stage2 捕获**：`page.on('request')` 抓真实 URL/headers/body；用请求拦截注入前端漏发的必填字段（如 `tenantId`/`adminId`）。
4. **Stage3 分析**：按抓取端点归纳增删改查逻辑顺序。
5. **Stage4 生成 + 状态检查**：生成脚本；回查列表/接口确认资源是否创建；识别权限门禁。
6. **可观测性**：关键步骤截图落盘 + DOM 断言，不依赖读图。

---

## 3. 捕获到的真实 API 契约（estack/draco）

> 完整机器可读版见 `config/probe_lessons_kb.json` → `captured_contracts.create_user`。

```
POST /estack/api/estack/draco/v1/users
Headers:
  Authorization: Bearer <accessToken>     # accessToken 来自 cookie（非 localStorage）
  Estack-Language: zh-CN
  Content-Type: application/json
Body:
  userName      <string 账号, 纯字母数字无下划线>
  password      <RSA 加密后 base64>        # 明文会被拒
  name          <string 姓名>
  email         <RSA 加密后 base64>
  phone         <RSA 加密后 base64>
  description   <string 描述, textarea>
  policyIds     ["1f8e392309fc414c9d77d45d0315fedc"]   # 系统角色"普通用户"映射
  adminId       5f28642f3a2f483e988b8185a132477c
  countryCode   "+86"
  tenantId      ebc58456fb2d47e09ef258d37d2b8ee6
```

**要点**：
- `password/email/phone` 必须经前端 RSA 加密后发送。
- 系统角色"普通用户" → `policyIds[0] = 1f8e392309fc414c9d77d45d0315fedc`。
- `POST /users/check` 必须在 body 带 `tenantId` 才能通过校验。

---

## 4. 沉淀到知识库的可复用条目

- **选择器层**（已并入 `config/base_nav_kb.json`）：
  - `submit-order-button`：表单页底部提交按钮，文本包容内部空格（如 `确 定`）。
  - `create-user-entry`：创建/新增入口可能是整页跳转而非弹窗（点击后检查 URL 是否含 `create-*`）。
- **行为/领域层**（新建 `config/probe_lessons_kb.json`）：
  - `systems.estack_draco`：tenantId 必填、RSA 加密、权限门禁、Bearer=accessToken、check 端点语义。
  - `anti_patterns`：反模式清单（含来源与严重度）。
  - `success_playbook`：标准成功流程。
  - `captured_contracts`：真实 `POST /users` 契约。
- **工程纪律**（反模式）：
  - 复用现成登录逻辑，不要重写；
  - 不要无限循环重跑；
  - 自己诊断（截图 + DOM + API），不让用户截图；
  - 不臆测 API 路径，以实测为准；
  - 模型不读图，诊断靠 DOM/API。

---

## 5. 待办（需用户拍板）

- 提供**租户管理员**账号，以跑通"创建成功 + 列表回查"完整闭环（当前账号被权限网关拦截）。
- 是否把"tenantId 注入补丁 + textarea/无数据下拉处理 + 权限门禁标注"固化进 `module_discovery/capture_apis.py`。
