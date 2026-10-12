# 登录模块统一重构方案

> 版本：v1.0  
> 日期：2026-10-12  
> 状态：已实施完成（2026-10-12）

---

## 目录

1. [问题分析](#1-问题分析)
2. [设计原则](#2-设计原则)
3. [目标架构](#3-目标架构)
4. [详细设计](#4-详细设计)
5. [迁移方案](#5-迁移方案)
6. [实施计划](#6-实施计划)
7. [验收标准](#7-验收标准)
8. [风险评估](#8-风险评估)

---

## 1. 问题分析

### 1.1 当前架构的核心问题

**登录逻辑严重分散，存在 7+ 个入口点：**

| 入口点 | 文件位置 | 行数 | 问题 |
|--------|---------|------|------|
| `AuthSession` | `lib/auth/auth.py` | ~600 行 | 被绕过，未成为唯一入口 |
| `_ensure_login()` | `core/discovery/run.py:860` | 140 行 | 重新实现 cookie 加载、token 提取、localStorage 注入 |
| `_refresh_cookie_mid_discovery()` | `core/discovery/run.py:75` | 28 行 | 重复 token 提取 + localStorage 注入逻辑 |
| `refresh_cookies()` | `core/discovery/shared_runner.py:122` | 45 行 | 手动启动浏览器 + 保存 cookie，绕过 AuthSession |
| `ensure_cookie_valid()` | `core/discovery/shared_runner.py:90` | 30 行 | 手动解析凭据 + 创建 AuthSession |
| `login_with_playwright()` | `core/discovery/auth_runner.py:15` | 20 行 | 薄包装器，委托给 AuthSession |
| `ensure_browser_login()` | `core/discovery/auth_runner.py:39` | 50 行 | **死代码**，硬编码错误路径 |

**代码重复统计：**

- Cookie 读取：**13 个位置**，至少 6 种不同实现
- Cookie 写入：**7 个位置**，4 处绕过 `AuthSession.save_cookies()`
- Token 提取：**7 个位置**，4 种不同实现
- 凭据解析：**4 个位置**，3 种不同实现
- localStorage 注入：**4 个位置**，逻辑几乎相同

### 1.2 根本原因

1. **历史演进**：`AuthSession` 早期存在，后来 `run.py` 和 `shared_runner.py` 为快速实现功能直接内联了相同逻辑
2. **缺乏强制约束**：没有架构层面的强制要求必须通过 `AuthSession`
3. **生成脚本的特殊性**：`cookie_client.py` 被复制到生成脚本中，导致看起来像是重复，但这是**正确的设计**（为了可移植性）

### 1.3 关键洞察

**生成脚本的可移植性需求：**

```
projects/estack/v1.0.0/api/
├── config/cookies.json          ← 只需复制这个文件
├── lib/
│   ├── cookie_client.py         ← 轻量级 cookie 读取器（无登录逻辑）
│   ├── runtime/test_runtime.py
│   └── ...
└── 01_用户组管理_API测试.py
```

**设计要点：**
- 生成脚本**不应该包含登录逻辑**（无法在其他机器执行滑块验证码）
- 只需复制 `cookies.json` 即可运行
- `cookie_client.py` 是**只读消费者**，从 cookie 提取 token，构建 session
- 这是**正确的设计**，不是重复

**框架本身的登录逻辑：**
- 应该**统一管理**，只有一个入口
- 支持多策略（ecscloud, bcov, ecso, tianshu）
- 所有 cookie 读写必须通过统一接口

---

## 2. 设计原则

### 2.1 核心原则

1. **单一入口**：框架的所有登录必须通过 `LoginSession`
2. **策略模式**：不同项目的登录差异通过 `LoginStrategy` 接口抽象
3. **生成脚本纯净**：生成脚本只包含 `cookie_client.py`（只读），不包含登录逻辑
4. **彻底清理**：删除所有重复的登录代码，不留后路

### 2.2 设计约束

1. **Cookie 格式不变**：继续使用 Playwright 原生 cookie 数组格式
2. **生成脚本兼容**：`cookie_client.py` 的公共 API 保持不变
3. **配置向后兼容**：`profile.yaml` 格式不变，新增字段可选

### 2.3 参考架构

采用 `login-kit` 的成熟架构：

```
cookie 优先 → 新鲜度缓存(跳过探测) → 服务端探测 → 证伪才登录(带重试) → 写回 cookie
```

---

## 3. 目标架构

### 3.1 目录结构

```
lib/auth/
├── __init__.py
├── session.py                    # 统一登录会话（骨架）
├── strategy_base.py              # 策略基类
├── strategies/
│   ├── __init__.py
│   ├── registry.py               # 策略注册表
│   ├── ecscloud.py               # EcsCloud/estack 策略
│   ├── bcov.py                   # BCov 策略
│   ├── ecso.py                   # ECSO 策略
│   └── tianshu.py                # 天枢策略
├── captcha/
│   ├── __init__.py
│   ├── slider.py                 # 滑块验证码（OpenCV 缺口检测）
│   ├── puzzle.py                 # 拼图验证码（BCov flood-fill）
│   ├── arithmetic.py             # 算术验证码（ECSO）
│   └── ocr.py                    # 字符 OCR（天枢）
├── human_drag.py                 # 人类化拖拽模拟（共享）
├── cookie_client.py              # 轻量级 cookie 读取器（生成脚本用）
└── cookie_store.py               # Cookie 存储管理（统一读写）

config/
├── credentials.yaml              # 多站点凭据配置
└── sites.yaml                    # 站点配置（新增）

core/discovery/
├── run.py                        # 简化：调用 LoginSession
├── shared_runner.py              # 简化：调用 LoginSession
└── auth_runner.py                # 删除（不再需要）

lib/auth/
└── auth.py                       # 删除（被 session.py 替代）
```

### 3.2 核心组件

#### 3.2.1 LoginSession（统一入口）

```python
class LoginSession:
    """统一登录会话 — 所有框架登录的唯一入口"""
    
    async def ensure_auth(self, page, context, strategy) -> bool:
        """
        确保认证有效
        
        流程：
        1. 检查本地 cookie 新鲜度（TTL 内）
        2. 服务端探测（访问 probe_url）
        3. 执行登录（strategy.login，带重试）
        4. 保存 cookies + localStorage + sessionStorage
        """
```

**职责：**
- Cookie 读写（通过 `CookieStore`）
- 新鲜度检查
- 服务端探测
- 登录重试编排
- Cookie 注入到 Playwright context
- localStorage/sessionStorage 持久化

#### 3.2.2 LoginStrategy（策略接口）

```python
class LoginStrategy(ABC):
    """登录策略基类"""
    
    @abstractmethod
    async def login(self, page, context, config) -> LoginResult:
        """执行登录流程"""
    
    @abstractmethod
    async def probe(self, page, probe_url) -> bool:
        """检测会话是否有效"""
    
    @abstractmethod
    def get_selectors(self) -> dict:
        """返回登录表单选择器"""
    
    @abstractmethod
    def get_defaults(self) -> dict:
        """返回默认配置"""
```

**四个策略实现：**

| 策略 | 验证码类型 | 核心算法 | 来源 |
|------|-----------|---------|------|
| `EcsCloudStrategy` | 滑块 | OpenCV 多算法投票 | 从 `auth.py` 抽取 |
| `BcovStrategy` | 拼图 | Flood-fill 封闭区域检测 | 从 `login-kit/bcov.js` 移植 |
| `EcsoStrategy` | 算术 | OCR + 表达式解析 | 从 `login-kit/ecso.js` 移植 |
| `TianshuStrategy` | 字符 OCR | ddddocr 识别 | 从 `login-kit/tianshu.js` 移植 |

#### 3.2.3 CookieStore（统一存储）

```python
class CookieStore:
    """Cookie 存储管理 — 所有 cookie 读写的唯一接口"""
    
    def load(self, project_dir: Path) -> list:
        """加载 cookies"""
    
    def save(self, project_dir: Path, cookies: list):
        """保存 cookies + meta.json"""
    
    def is_fresh(self, project_dir: Path, ttl_seconds: int) -> bool:
        """检查新鲜度"""
    
    def filter_by_host(self, cookies: list, host: str) -> list:
        """域名过滤"""
```

**职责：**
- Cookie 文件读写
- Meta 文件管理（savedAt, count）
- 域名过滤（防止跨地址污染）
- 路径解析（workspace vs projects）

#### 3.2.4 cookie_client（生成脚本用）

```python
# 保持不变，仅用于生成脚本
def require_auth(cookie_path: str = None, token_key: str = "accessToken") -> requests.Session:
    """读取 cookie，构建 requests.Session（无登录能力）"""

def require_auth_for_ui(cookie_path: str = None, token_key: str = "accessToken") -> Tuple[list, str]:
    """读取 cookie，返回 (cookies, token)（无登录能力）"""
```

**关键点：**
- **不包含登录逻辑**
- 只读 cookie，提取 token
- 被复制到生成脚本中（为了可移植性）

---

## 4. 详细设计

### 4.1 LoginSession 完整流程

```python
async def ensure_auth(self, page, context, strategy):
    """
    确保认证有效的主流程
    
    Returns:
        bool: 是否成功（已认证或登录成功）
    """
    # Step 1: 检查本地 cookie 新鲜度
    if self.store.is_fresh(self.project_dir, self.config.fresh_ttl_seconds):
        cookies = self.store.load(self.project_dir)
        filtered = self.store.filter_by_host(cookies, self.config.host)
        await context.add_cookies(filtered)
        
        if self.config.persist_localStorage:
            await self._inject_localStorage(context)
        
        # Step 2: 服务端探测
        await page.goto(self.config.probe_url, wait_until="domcontentloaded")
        if await strategy.probe(page, self.config.probe_url):
            log.info("✅ Cookie 有效，复用现有会话")
            return True
    
    # Step 3: 执行登录（带重试）
    for attempt in range(self.config.max_login_retries):
        await page.goto(self.config.login_url, wait_until="domcontentloaded")
        
        result = await strategy.login(page, context, self.config)
        
        if result == LoginResult.SUCCESS:
            # Step 4: 保存 cookies + storage
            cookies = await context.cookies()
            self.store.save(self.project_dir, cookies)
            
            if self.config.persist_localStorage:
                await self._save_localStorage(page)
            if self.config.persist_sessionStorage:
                await self._save_sessionStorage(page)
            
            log.info("✅ 登录成功，cookies 已保存")
            return True
        
        elif result == LoginResult.LOCKED:
            log.error("❌ 账号被锁定，停止重试")
            return False
        
        # FAILED: 等待后重试
        if attempt < self.config.max_login_retries - 1:
            log.warning(f"登录失败，{2}秒后重试...")
            await asyncio.sleep(2)
    
    log.error("❌ 登录失败，已达最大重试次数")
    return False
```

### 4.2 策略实现要点

#### 4.2.1 EcsCloudStrategy

**核心流程：**
1. 填充用户名/密码（通过 Vue 组件注入）
2. 点击"认证"按钮 → 等待滑块出现
3. 捕获验证码素材（response interceptor）
4. OpenCV 缺口检测（多算法投票）
5. 人类化拖拽
6. 轮询验证结果
7. 点击"登录"

**关键代码：**
```python
async def login(self, page, context, config):
    # 1. 填充凭据（Vue 组件注入）
    await page.evaluate(FIND_LC_SRC)
    await page.evaluate(f"""
        const lc = findLC_smart();
        lc.loginForm.username = {config.username!r};
        lc.loginForm.password = {config.password!r};
    """)
    
    # 2. 点击认证按钮
    await page.click('button:has-text("点击完成认证")')
    
    # 3. 等待滑块
    await page.wait_for_selector('#slideVerify', timeout=10000)
    
    # 4. 缺口检测 + 拖拽（最多 4 次重试）
    for _ in range(4):
        material = await self._capture_material(page)
        gap_x = await self._detect_gap(material)
        await self._drag_slider(page, gap_x)
        
        if await self._poll_verification(page):
            break
    
    # 5. 点击登录
    await page.click('button:has-text("登录")')
    
    # 6. 验证成功
    await page.wait_for_url("**/portal**", timeout=5000)
    return LoginResult.SUCCESS
```

#### 4.2.2 BcovStrategy

**核心流程：**
1. 填充用户名/密码
2. 点击"点击完成验证"
3. 捕获拼图素材
4. Flood-fill 封闭区域检测
5. 人类化拖拽
6. 点击"登录"

#### 4.2.3 EcsoStrategy

**核心流程：**
1. 填充用户名/密码
2. 点击"登录" → 触发算术验证码
3. OCR 识别算术表达式
4. 计算答案并填充
5. 点击"确定"（注意：不是登录按钮）
6. 检测锁定状态

#### 4.2.4 TianshuStrategy

**核心流程：**
1. 填充用户名/密码
2. OCR 识别字符验证码
3. 填充验证码
4. 点击"登录"
5. 检测锁定状态
6. **必须持久化 localStorage**（SPA 检查登录状态）

### 4.3 CookieStore 实现

```python
class CookieStore:
    def __init__(self):
        self.cookie_file = "cookies.json"
        self.meta_file = "cookies.meta.json"
        self.localstorage_file = "cookies.localStorage.json"
        self.sessionstorage_file = "cookies.sessionStorage.json"
    
    def load(self, project_dir: Path) -> list:
        cookie_path = project_dir / "output" / "config" / self.cookie_file
        if not cookie_path.exists():
            return []
        return json.loads(cookie_path.read_text(encoding="utf-8"))
    
    def save(self, project_dir: Path, cookies: list):
        config_dir = project_dir / "output" / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        
        cookie_path = config_dir / self.cookie_file
        cookie_path.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
        
        meta_path = config_dir / self.meta_file
        meta = {"savedAt": int(time.time() * 1000), "count": len(cookies)}
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    
    def is_fresh(self, project_dir: Path, ttl_seconds: int) -> bool:
        meta_path = project_dir / "output" / "config" / self.meta_file
        if not meta_path.exists():
            return False
        
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        saved_at = meta.get("savedAt", 0)
        age_ms = int(time.time() * 1000) - saved_at
        return age_ms < (ttl_seconds * 1000)
    
    def filter_by_host(self, cookies: list, host: str) -> list:
        """域名过滤（RFC 6265）"""
        host = host.split(":")[0]  # 去除端口
        return [c for c in cookies if self._domain_matches(c.get("domain", ""), host)]
    
    def _domain_matches(self, cookie_domain: str, host: str) -> bool:
        cookie_domain = cookie_domain.lstrip(".")
        if cookie_domain == host:
            return True
        if host.endswith("." + cookie_domain):
            return True
        return False
```

### 4.4 配置文件设计

#### 4.4.1 config/sites.yaml（新增）

```yaml
sites:
  estack:
    label: "EcsCloud 移动云控制台"
    strategy: "ecscloud"
    base_url: "https://10.151.61.248"
    login_url: "/estack/web/estack/login"
    probe_url: "/estack/web/estack/portal"
    
    auth:
      header_name: "Authorization"
      header_prefix: "Bearer "
      token_key: "estackToken"
      token_storage: "localStorage"
      cookie_token_key: "accessToken"
      cookie_keys: ["estack_lang", "accessToken"]
    
    captcha:
      type: "slider"
      material_url_regex: "pictures-verification"
      auth_button_text: "点击完成认证"
    
    selectors:
      username: 'input[placeholder="用户名"]'
      password: 'input[placeholder="登录密码"]'
      login_button: 'button:has-text("登录")'
    
    session:
      fresh_ttl_seconds: 1800
      max_login_retries: 2
      persist_localStorage: true
      persist_sessionStorage: true

  bcov:
    label: "BCov 移动云运维平台"
    strategy: "bcov"
    base_url: "https://10.251.132.52:30105"
    login_url: "/bcov/login"
    probe_url: "/bcov/overview/index"
    
    auth:
      cookie_token_key: "BCOV-Token"
      cookie_keys: ["bcov_lang", "BCOV-Token", "bcov_Salt"]
    
    captcha:
      type: "puzzle"
    
    session:
      fresh_ttl_seconds: 1800

  ecso:
    label: "ECSO 全栈专属云"
    strategy: "ecso"
    base_url: "https://console.ecso.example.com"
    login_url: "/ecso/login/ecso-login-static/login"
    probe_url: "/ecso/operation/ecso-operation-admin-static/"
    
    auth:
      cookie_token_key: "ECSOTOKEN"
      cookie_keys: ["ECSOTOKEN", "CMECSTOKEN"]
    
    captcha:
      type: "arithmetic"
    
    session:
      fresh_ttl_seconds: 1800
      detect_lock: true

  tianshu:
    label: "天枢运维平台"
    strategy: "tianshu"
    base_url: "https://tianshu.example.com"
    login_url: "/#/login"
    probe_url: "/#/"
    
    auth:
      cookie_token_key: "ud_token"
      cookie_keys: ["ud_token"]
    
    captcha:
      type: "ocr"
    
    session:
      fresh_ttl_seconds: 1800
      persist_localStorage: true  # 必须
      detect_lock: true
```

#### 4.4.2 config/credentials.yaml（扩展现有）

```yaml
estack:
  username: "estack-yy"
  password: "R@9eDuck$!mpleM00n"
  base_url: "https://10.151.61.248"
  auth:
    header_name: "Authorization"
    header_prefix: "Bearer "
    token_key: "estackToken"
    cookie_token_key: "accessToken"

bcov:
  username: "bcov-admin"
  password: "BCOV_PASSWORD"
  base_url: "https://10.251.132.52:30105"

ecso:
  username: "ecso-admin"
  password: "ECSO_PASSWORD"
  base_url: "https://console.ecso.example.com"

tianshu:
  username: "admin"
  password: "TIANSHU_PASSWORD"
  base_url: "https://tianshu.example.com"
```

#### 4.4.3 projects/<project>/profile.yaml（新增 strategy 字段）

```yaml
# projects/estack/profile.yaml
strategy: "ecscloud"                    # ← 新增
base_url: "https://10.151.61.248"
login_url: "/estack/web/estack/login"
probe_url: "/estack/web/estack/portal"
auth:
  header_name: "Authorization"
  header_prefix: "Bearer "
  token_key: "estackToken"
  token_storage: "localStorage"
  cookie_token_key: "accessToken"
captcha:
  type: "slider"
  material_url_regex: "pictures-verification"
credentials:
  username_env: "APP_USER"
  password_env: "APP_PASS"
```

### 4.5 调用方迁移

#### 4.5.1 run.py._ensure_login()

**删除 140 行重复代码，替换为 20 行：**

```python
async def _ensure_login(page, context, project_dir, base_url, login_url, profile):
    """确保浏览器已登录（委托给 LoginSession）"""
    from lib.auth.session import LoginSession
    from lib.auth.strategies.registry import get_strategy
    from lib.auth.strategy_base import LoginConfig
    
    # 构建 config
    config = LoginConfig.from_profile(profile, base_url, login_url)
    
    # 获取策略
    strategy_name = profile.get("strategy", "ecscloud")
    strategy = get_strategy(strategy_name)
    
    # 执行登录
    session = LoginSession(project_dir, config)
    success = await session.ensure_auth(page, context, strategy)
    
    if not success:
        raise RuntimeError("登录失败")
    
    return True
```

#### 4.5.2 shared_runner.refresh_cookies()

**删除 45 行重复代码，替换为 10 行：**

```python
async def refresh_cookies(project_dir, workspace_dir, base_url, login_url, strategy_name="ecscloud"):
    """刷新 cookies（委托给 LoginSession）"""
    from lib.auth.session import LoginSession
    from lib.auth.strategies.registry import get_strategy
    from lib.auth.strategy_base import LoginConfig
    
    config = LoginConfig(site_key=strategy_name, base_url=base_url, login_url=login_url)
    strategy = get_strategy(strategy_name)
    session = LoginSession(workspace_dir, config)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        
        success = await session.ensure_auth(page, context, strategy)
        
        await browser.close()
    
    return success
```

#### 4.5.3 auth_runner.py

**整个文件删除**（不再需要薄包装器）

---

## 5. 迁移方案

### 5.1 删除清单

#### 5.1.1 代码文件

| 文件 | 操作 | 原因 |
|------|------|------|
| `lib/auth/auth.py` | **删除** | 被 `session.py` 替代 |
| `core/discovery/auth_runner.py` | **删除** | 薄包装器，不再需要 |
| `lib/auth/slider.py` | **移动** | 改为 `lib/auth/captcha/slider.py` |

#### 5.1.2 函数

| 函数 | 文件 | 操作 | 原因 |
|------|------|------|------|
| `_ensure_login()` | `run.py:860` | **重写** | 委托给 LoginSession |
| `_refresh_cookie_mid_discovery()` | `run.py:75` | **重写** | 委托给 LoginSession |
| `refresh_cookies()` | `shared_runner.py:122` | **重写** | 委托给 LoginSession |
| `ensure_cookie_valid()` | `shared_runner.py:90` | **重写** | 委托给 LoginSession |
| `ensure_browser_login()` | `auth_runner.py:39` | **删除** | 死代码 |
| `resolve_credentials()` | `auth_runner.py:90` | **删除** | 死代码 |

#### 5.1.3 测试文件

| 文件 | 操作 | 原因 |
|------|------|------|
| `tests/test_auth.py` | **删除** | 测试旧的 AuthSession |
| `tests/test_session.py` | **新增** | 测试新的 LoginSession |
| `tests/test_strategy_*.py` | **新增** | 测试四个策略 |
| `tests/test_cookie_store.py` | **新增** | 测试 CookieStore |

#### 5.1.4 生成脚本数据

| 目录 | 操作 | 原因 |
|------|------|------|
| `projects/estack/` | **删除** | 用户明确允许，重新生成 |

### 5.2 新增清单

#### 5.2.1 核心文件

```
lib/auth/
├── session.py                    # 统一登录会话
├── strategy_base.py              # 策略基类
├── strategies/
│   ├── __init__.py
│   ├── registry.py               # 策略注册表
│   ├── ecscloud.py               # EcsCloud 策略
│   ├── bcov.py                   # BCov 策略
│   ├── ecso.py                   # ECSO 策略
│   └── tianshu.py                # 天枢策略
├── captcha/
│   ├── __init__.py
│   ├── slider.py                 # 滑块验证码
│   ├── puzzle.py                 # 拼图验证码
│   ├── arithmetic.py             # 算术验证码
│   └── ocr.py                    # 字符 OCR
├── human_drag.py                 # 人类化拖拽
└── cookie_store.py               # Cookie 存储管理
```

#### 5.2.2 配置文件

```
config/
└── sites.yaml                    # 站点配置（新增）
```

#### 5.2.3 测试文件

```
tests/
├── test_session.py               # LoginSession 测试
├── test_cookie_store.py          # CookieStore 测试
├── test_strategy_ecscloud.py     # EcsCloud 策略测试
├── test_strategy_bcov.py         # BCov 策略测试
├── test_strategy_ecso.py         # ECSO 策略测试
└── test_strategy_tianshu.py      # 天枢策略测试
```

### 5.3 修改清单

#### 5.3.1 核心文件

| 文件 | 修改内容 |
|------|---------|
| `core/discovery/run.py` | 重写 `_ensure_login()` 和 `_refresh_cookie_mid_discovery()` |
| `core/discovery/shared_runner.py` | 重写 `refresh_cookies()` 和 `ensure_cookie_valid()` |
| `config/credentials.yaml` | 添加 bcov, ecso, tianshu 凭据 |
| `requirements.txt` | 添加 `ddddocr>=1.4.0` |
| `pyproject.toml` | 添加 `ddddocr` 可选依赖 |

#### 5.3.2 文档

| 文件 | 修改内容 |
|------|---------|
| `PROJECT_GUIDE.md` | 更新登录相关章节 |
| `skill.md` | 更新参数说明 |
| `docs/debug/login-module-refactor-design.md` | 本文档 |

---

## 6. 实施计划

### 6.1 分阶段实施

```
Phase 0: 准备工作（0.5 天）
  ├─ 创建目录结构
  ├─ 创建 strategy_base.py + registry.py
  ├─ 创建 session.py 骨架
  └─ 创建 config/sites.yaml

Phase 1: EcsCloud 策略（2 天）
  ├─ 从 auth.py 抽取 EcsCloudStrategy
  ├─ 实现 captcha/slider.py（从 slider.py 迁移）
  ├─ 实现 human_drag.py
  ├─ 实现 CookieStore
  ├─ 重写 run.py._ensure_login()
  ├─ 重写 shared_runner.refresh_cookies()
  ├─ 删除 auth_runner.py
  ├─ 删除 auth.py
  ├─ 手动测试 estack 登录
  └─ 打 tag: phase-1-ecscloud

Phase 2: BCov 策略（1.5 天）
  ├─ 移植 bcov.js → bcov.py
  ├─ 实现 captcha/puzzle.py
  ├─ 创建 bcov 项目配置
  ├─ 测试 bcov 登录
  └─ 打 tag: phase-2-bcov

Phase 3: ECSO 策略（1.5 天）
  ├─ 移植 ecso.js → ecso.py
  ├─ 实现 captcha/arithmetic.py
  ├─ 创建 ecso 项目配置
  ├─ 测试 ecso 登录
  └─ 打 tag: phase-3-ecso

Phase 4: 天枢策略（1.5 天）
  ├─ 移植 tianshu.js → tianshu.py
  ├─ 实现 captcha/ocr.py
  ├─ 实现 localStorage 持久化
  ├─ 创建 tianshu 项目配置
  ├─ 测试天枢登录
  └─ 打 tag: phase-4-tianshu

Phase 5: 测试迁移（2 天）
  ├─ 创建 test_session.py
  ├─ 创建 test_cookie_store.py
  ├─ 创建 test_strategy_*.py（4 个）
  ├─ 删除 test_auth.py
  ├─ 运行全部测试
  └─ 打 tag: phase-5-tests

Phase 6: 文档与清理（1 天）
  ├─ 更新 PROJECT_GUIDE.md
  ├─ 更新 skill.md
  ├─ 删除 projects/estack/（用户允许）
  ├─ 重新生成 estack 脚本
  ├─ 端到端测试
  └─ 打 tag: phase-6-final
```

**总工期：10 天**

### 6.2 每个 Phase 的验收标准

#### Phase 1 验收标准

```bash
# 1. EcsCloud 项目登录成功
python -m core.discovery.run --project estack --module "用户管理" --stage 1
# 预期：成功登录并生成 ui.json

# 2. Cookie 文件生成
ls workspace/estack/output/config/cookies.json
ls workspace/estack/output/config/cookies.meta.json
# 预期：文件存在且非空

# 3. 无重复代码
grep -r "AuthSession" lib/ core/ --include="*.py"
# 预期：无匹配（除了注释）
```

#### Phase 2-4 验收标准

```bash
# 1. 策略单元测试通过
pytest tests/test_strategy_<name>.py -v
# 预期：所有测试通过

# 2. 项目登录成功
python -m core.discovery.run --project <name> --module "任意模块" --stage 1
# 预期：成功登录
```

#### Phase 5 验收标准

```bash
# 1. 全部测试通过
pytest tests/ -q
# 预期：50+ 测试通过

# 2. 代码覆盖率
pytest --cov=lib/auth --cov-report=html
# 预期：覆盖率 > 80%
```

#### Phase 6 验收标准

```bash
# 1. 端到端测试
python -m core.discovery.run --project estack --module "用户管理" --stage all
# 预期：全流程成功

# 2. 生成脚本可执行
cd projects/estack/v1.0.0/api
python 01_访问控制/01_用户组管理_API测试.py
# 预期：测试通过

# 3. 生成脚本可移植
# 复制 projects/estack/v1.0.0/api/ 到其他机器
# 复制 cookies.json
# 运行脚本
# 预期：成功执行
```

---

## 7. 验收标准

### 7.1 功能验收

- [ ] 4 个策略全部实现并测试通过
- [ ] LoginSession 成为唯一登录入口
- [ ] 所有重复代码已删除
- [ ] Cookie 读写统一通过 CookieStore
- [ ] localStorage/sessionStorage 持久化正常工作
- [ ] 域名过滤正常工作
- [ ] 锁定检测正常（ecso + tianshu）

### 7.2 代码质量验收

- [ ] 无重复的登录代码
- [ ] 无死代码
- [ ] 测试覆盖率 > 80%
- [ ] 所有测试通过

### 7.3 可移植性验收

- [ ] 生成脚本不包含登录逻辑
- [ ] 生成脚本只包含 cookie_client.py（只读）
- [ ] 生成脚本可复制到其他机器执行（只需 cookies.json）

### 7.4 文档验收

- [ ] PROJECT_GUIDE.md 已更新
- [ ] skill.md 已更新
- [ ] LOGIN_STRATEGIES.md 已创建（策略开发指南）

---

## 8. 风险评估

### 8.1 技术风险

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|---------|
| EcsCloud 策略抽取不完整 | 高 | 中 | 充分测试，保留原 auth.py 作为参考 |
| OpenCV 缺口检测移植问题 | 中 | 低 | 已有成熟代码，直接迁移 |
| ddddocr 依赖安装问题 | 低 | 低 | 提供安装文档，标记为可选依赖 |
| localStorage 持久化失败 | 高 | 低 | 天枢策略单独测试 |

### 8.2 进度风险

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|---------|
| Phase 1 超时 | 高 | 中 | EcsCloud 是最复杂的，预留 2 天 |
| 测试迁移工作量超预期 | 中 | 低 | 渐进式迁移，每个策略单独测试 |
| 文档更新遗漏 | 低 | 中 | 每个 Phase 结束时检查文档 |

### 8.3 回滚方案

每个 Phase 完成后打 Git tag：

```bash
git tag phase-0-baseline
git tag phase-1-ecscloud
git tag phase-2-bcov
git tag phase-3-ecso
git tag phase-4-tianshu
git tag phase-5-tests
git tag phase-6-final
```

如果某 Phase 失败，回滚到上一个 tag：

```bash
git checkout phase-N-baseline
git reset --hard
```

---

## 9. 关键设计决策

### 9.1 为什么删除 auth.py 而不是保留兼容层？

**理由：**
1. 用户明确允许不向后兼容
2. 保留兼容层会留下混乱的代码路径
3. 彻底删除可以强制所有调用方使用新架构
4. 生成脚本的 cookie_client.py 不受影响（独立存在）

### 9.2 为什么 cookie_client.py 保持不变？

**理由：**
1. 它是生成脚本的**只读消费者**，不包含登录逻辑
2. 被复制到生成脚本中是为了可移植性
3. 这是**正确的设计**，不是重复
4. 公共 API 保持不变，生成脚本无需修改

### 9.3 为什么不实现内容指纹识别？

**理由：**
1. login-kit 的内容指纹识别是为了自动检测站点类型
2. API_AI_test 通过 `profile.yaml` 的 `strategy` 字段显式指定
3. 显式配置更可靠，避免误判
4. 可以后续作为增强功能添加

### 9.4 为什么删除 projects/estack/？

**理由：**
1. 用户明确允许
2. 重新生成可以验证新架构的正确性
3. 避免旧数据的干扰
4. 生成脚本的 cookie_client.py 不受影响

---

## 10. 总结

本方案通过**彻底重构**，解决了登录逻辑分散、重复、混乱的问题：

**核心改进：**
1. ✅ **单一入口**：所有登录通过 `LoginSession`
2. ✅ **策略模式**：4 个项目通过策略接口抽象
3. ✅ **统一存储**：Cookie 读写通过 `CookieStore`
4. ✅ **生成脚本纯净**：只包含 `cookie_client.py`（只读）
5. ✅ **彻底清理**：删除所有重复代码

**实施保障：**
- 10 天分 6 个 Phase 完成
- 每个 Phase 有明确验收标准
- 每个 Phase 可独立回滚
- 充分测试覆盖

**预期效果：**
- 代码量减少 ~300 行（删除重复）
- 可维护性大幅提升
- 新增策略只需实现 `LoginStrategy` 接口
- 生成脚本保持可移植性

---

## 附录 A：策略开发指南

### A.1 新增策略的步骤

1. 创建 `lib/auth/strategies/<name>.py`
2. 继承 `LoginStrategy` 基类
3. 实现 4 个必需方法：
   - `get_selectors()`
   - `get_defaults()`
   - `login()`
   - `probe()`
4. 在 `registry.py` 中注册
5. 在 `config/sites.yaml` 中添加配置
6. 编写测试 `tests/test_strategy_<name>.py`

### A.2 策略模板

```python
from ..strategy_base import LoginStrategy, LoginResult, LoginConfig

class MyStrategy(LoginStrategy):
    key = "myproject"
    label = "My Project"
    
    def get_selectors(self):
        return {
            "username": 'input[placeholder="用户名"]',
            "password": 'input[placeholder="密码"]',
            "login_button": 'button:has-text("登录")',
        }
    
    def get_defaults(self):
        return {
            "login_url": "/login",
            "probe_url": "/portal",
            "cookie_keys": ["session_id"],
            "fresh_ttl_seconds": 1800,
        }
    
    async def login(self, page, context, config):
        # 实现登录逻辑
        pass
    
    async def probe(self, page, probe_url):
        # 实现探测逻辑
        pass
```

---

## 附录 B：配置文件参考

### B.1 profile.yaml 完整字段

```yaml
strategy: "ecscloud"                    # 策略名称（必需）
base_url: "https://example.com"         # 基础 URL
login_url: "/login"                     # 登录页路径
probe_url: "/portal"                    # 探测页路径

auth:
  header_name: "Authorization"          # Token header 名称
  header_prefix: "Bearer "              # Token header 前缀
  token_key: "token"                    # localStorage key
  token_storage: "localStorage"         # Token 存储位置
  cookie_token_key: "session_id"        # Cookie 中的 token key
  cookie_keys: ["session_id"]           # 关键 cookie 列表

captcha:
  type: "slider"                        # 验证码类型
  material_url_regex: "captcha"         # 素材 URL 正则
  auth_button_text: "认证"              # 认证按钮文本

selectors:
  username: 'input[name="username"]'    # 用户名选择器
  password: 'input[name="password"]'    # 密码选择器
  login_button: 'button[type="submit"]' # 登录按钮选择器

session:
  fresh_ttl_seconds: 1800               # Cookie 新鲜度 TTL
  max_login_retries: 2                  # 最大登录重试次数
  persist_localStorage: false           # 是否持久化 localStorage
  persist_sessionStorage: false         # 是否持久化 sessionStorage
  detect_lock: false                    # 是否检测账号锁定

credentials:
  username_env: "APP_USER"              # 用户名环境变量
  password_env: "APP_PASS"              # 密码环境变量
```

### B.2 sites.yaml 完整示例

见 [4.4.1 节](#441-configsitesyaml新增)

---

## 附录 C：术语表

| 术语 | 说明 |
|------|------|
| **LoginSession** | 统一登录会话，所有框架登录的唯一入口 |
| **LoginStrategy** | 登录策略接口，每个项目实现一个子类 |
| **CookieStore** | Cookie 存储管理，统一读写接口 |
| **cookie_client** | 轻量级 cookie 读取器，用于生成脚本（只读） |
| **Probe** | 服务端探测，访问 probe_url 检测会话是否有效 |
| **Freshness** | 新鲜度，cookie 在 TTL 内被视为新鲜，跳过服务端探测 |
| **Lock Detection** | 锁定检测，检测账号是否被服务端锁定 |
| **Human Drag** | 人类化拖拽，模拟真人滑动验证码操作 |

---

**文档结束**
