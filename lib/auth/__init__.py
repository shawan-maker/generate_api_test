"""auth/ - 鉴权模块集群

新架构（2026-10-12 重构）：
- session.py: LoginSession（统一登录入口）
- strategy_base.py: LoginStrategy + LoginConfig
- strategies/: 4 个策略实现
- captcha/: 验证码求解器
- cookie_store.py: Cookie 存储管理
- cookie_client.py: 生成脚本专用（只读，无登录逻辑）

旧接口（过渡期保留，后续删除）：
- auth.py: AuthSession（已废弃，用 LoginSession 替代）
- slider.py: 滑块求解（已迁移到 captcha/slider.py）
"""

# 新架构
from .strategy_base import LoginResult, LoginConfig, LoginStrategy
from .session import LoginSession
from .cookie_store import CookieStore

# 旧接口（过渡期）
try:
    from .auth import AuthSession
except ImportError:
    AuthSession = None

from .cookie_client import *
