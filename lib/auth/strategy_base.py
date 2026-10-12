"""
strategy_base.py — 登录策略基类与配置

定义所有登录策略必须实现的接口，以及登录配置数据类。
沿用 login-kit 的 cookie 优先 → 新鲜度缓存 → 服务端探测 → 证伪才登录 的架构。
"""

import os
import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


class LoginResult(enum.Enum):
    """登录结果枚举"""
    SUCCESS = "success"     # 登录成功
    FAILED = "failed"       # 登录失败（可重试）
    LOCKED = "locked"       # 账号被锁定（停止重试）


@dataclass
class LoginConfig:
    """登录配置 — 从 profile.yaml / sites.yaml / credentials.yaml 合并"""

    # 站点标识
    site_key: str = ""
    strategy: str = "ecscloud"

    # URL
    base_url: str = ""
    login_url: str = ""
    probe_url: str = ""

    # 凭据
    username: str = ""
    password: str = ""

    # Auth 配置
    header_name: str = "Authorization"
    header_prefix: str = "Bearer "
    token_key: str = ""                    # localStorage/sessionStorage key
    token_storage: str = "localStorage"
    cookie_token_key: str = ""
    cookie_keys: list = field(default_factory=list)

    # Captcha 配置
    captcha_type: str = "slider"
    material_url_regex: str = "pictures-verification"
    auth_button_text: str = "点击完成认证"

    # Session 配置
    fresh_ttl_seconds: int = 1800
    max_login_retries: int = 2
    persist_localStorage: bool = False
    persist_sessionStorage: bool = False
    detect_lock: bool = False

    # 选择器（可由 profile 覆盖）
    selectors: dict = field(default_factory=dict)

    @property
    def host(self) -> str:
        """从 base_url 提取 host（不含端口），用于 cookie 域名过滤"""
        import re
        m = re.search(r'https?://([^/:]+)', self.base_url)
        return m.group(1) if m else ""

    @classmethod
    def from_profile(cls, profile: dict, base_url: str = "", login_url: str = "",
                     credentials: dict = None) -> "LoginConfig":
        """从 profile.yaml 内容构建 LoginConfig

        Args:
            profile: profile.yaml 解析后的 dict
            base_url: 基础 URL（覆盖 profile 中的值）
            login_url: 登录 URL（覆盖 profile 中的值）
            credentials: credentials.yaml 中对应站点的凭据
        """
        auth_cfg = profile.get("auth", {}) or {}
        captcha_cfg = profile.get("captcha", {}) or {}
        session_cfg = profile.get("session", {}) or {}
        selectors_cfg = profile.get("selectors", {}) or {}
        creds_cfg = profile.get("credentials", {}) or {}

        # 凭据优先级：credentials.yaml > 环境变量 > profile.yaml
        creds = credentials or {}
        username = (creds.get("username")
                    or os.environ.get(creds_cfg.get("username_env", ""), "")
                    or creds_cfg.get("username", ""))
        password = (creds.get("password")
                    or os.environ.get(creds_cfg.get("password_env", ""), "")
                    or creds_cfg.get("password", ""))

        return cls(
            site_key=profile.get("site_key", ""),
            strategy=profile.get("strategy", "ecscloud"),
            base_url=base_url or profile.get("base_url", ""),
            login_url=login_url or profile.get("login_url", ""),
            probe_url=profile.get("probe_url", ""),
            username=username,
            password=password,
            header_name=auth_cfg.get("header_name", "Authorization"),
            header_prefix=auth_cfg.get("header_prefix", "Bearer "),
            token_key=auth_cfg.get("token_key", ""),
            token_storage=auth_cfg.get("token_storage", "localStorage"),
            cookie_token_key=auth_cfg.get("cookie_token_key", ""),
            cookie_keys=auth_cfg.get("cookie_keys", []),
            captcha_type=captcha_cfg.get("type", "slider"),
            material_url_regex=captcha_cfg.get("material_url_regex", "pictures-verification"),
            auth_button_text=captcha_cfg.get("auth_button_text", "点击完成认证"),
            fresh_ttl_seconds=session_cfg.get("fresh_ttl_seconds", 1800),
            max_login_retries=session_cfg.get("max_login_retries", 2),
            persist_localStorage=session_cfg.get("persist_localStorage", False),
            persist_sessionStorage=session_cfg.get("persist_sessionStorage", False),
            detect_lock=session_cfg.get("detect_lock", False),
            selectors=selectors_cfg,
        )


class LoginStrategy(ABC):
    """登录策略基类 — 每个项目实现一个子类

    子类必须实现:
    - login(): 执行登录流程
    - probe(): 检测会话是否有效
    - get_selectors(): 返回登录表单选择器
    - get_defaults(): 返回默认配置
    """

    # 子类设置
    key: str = ""        # 策略标识（如 "ecscloud"）
    label: str = ""      # 策略描述（如 "EcsCloud 移动云控制台"）

    @abstractmethod
    async def login(self, page, context, config: LoginConfig) -> LoginResult:
        """执行登录流程

        Args:
            page: Playwright Page 对象
            context: Playwright BrowserContext 对象
            config: 登录配置

        Returns:
            LoginResult: SUCCESS / FAILED / LOCKED
        """

    @abstractmethod
    async def probe(self, page, probe_url: str) -> bool:
        """检测会话是否有效（服务端探测）

        Args:
            page: Playwright Page 对象（已注入 cookie）
            probe_url: 探测页 URL（完整 URL）

        Returns:
            True 表示会话有效
        """

    @abstractmethod
    def get_selectors(self) -> dict:
        """返回登录表单选择器

        Returns:
            dict: {"username": ..., "password": ..., "login_button": ...}
        """

    @abstractmethod
    def get_defaults(self) -> dict:
        """返回默认配置（可被 profile.yaml 覆盖）

        Returns:
            dict: 默认配置值
        """
