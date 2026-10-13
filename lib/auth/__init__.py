"""auth/ - 鉴权模块集群

架构（2026-10-12 重构）：
- session.py: LoginSession（统一登录入口）
- strategy_base.py: LoginStrategy + LoginConfig
- strategies/: 4 个策略实现（ecscloud/bcov/ecso/tianshu）
- captcha/: 验证码求解器（slider/puzzle/arithmetic/ocr）
- cookie_store.py: Cookie 存储管理
- cookie_client.py: 生成脚本专用（只读，无登录逻辑）

认证入口：
- 框架内: core/discovery/auth_service.py (AuthService)
- 生成脚本: cookie_client.py (require_auth / require_auth_for_ui)
"""

from .strategy_base import LoginResult, LoginConfig, LoginStrategy
from .session import LoginSession
from .cookie_store import CookieStore
from .cookie_client import *
