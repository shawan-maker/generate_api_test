"""
test_session.py — LoginSession 单元测试
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, AsyncMock, MagicMock
import tempfile

from lib.auth import LoginSession, LoginConfig, LoginResult
from lib.auth.strategy_base import LoginStrategy


class MockStrategy(LoginStrategy):
    """测试用 Mock 策略"""

    key = "mock"
    label = "Mock Strategy"

    def __init__(self, login_result=LoginResult.SUCCESS, probe_result=True):
        self.login_result = login_result
        self.probe_result = probe_result
        self.login_called = False
        self.probe_called = False

    async def login(self, page, context, config):
        self.login_called = True
        return self.login_result

    async def probe(self, page, probe_url):
        self.probe_called = True
        return self.probe_result

    def get_selectors(self):
        return {"username": "input", "password": "input"}

    def get_defaults(self):
        return {"login_url": "/login"}


class TestLoginSession:
    """LoginSession 测试"""

    def setup_method(self):
        """每个测试前创建临时目录"""
        self.temp_dir = Path(tempfile.mkdtemp())

    def teardown_method(self):
        """每个测试后清理临时目录"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_ensure_auth_with_fresh_cookie(self):
        """测试使用新鲜 cookie 跳过登录（跳过探测，直接信任 TTL）"""
        config = LoginConfig(
            site_key="test",
            strategy="mock",
            base_url="https://example.com",
            login_url="/login",
            probe_url="/portal",
            username="user",
            password="pass",
            fresh_ttl_seconds=1800,
            persist_localStorage=False,
        )

        # 先保存新鲜 cookie（带正确域名）
        session = LoginSession(self.temp_dir, config)
        session.store.save(self.temp_dir, [
            {"name": "session", "value": "test", "domain": "example.com", "path": "/"}
        ])

        strategy = MockStrategy(probe_result=True)

        # Mock page 和 context
        page = AsyncMock()
        context = AsyncMock()
        context.add_cookies = AsyncMock()

        result = await session.ensure_auth(page, context, strategy)

        assert result is True
        assert strategy.login_called is False  # 应该跳过登录

    @pytest.mark.asyncio
    async def test_ensure_auth_login_when_no_cookie(self):
        """测试没有 cookie 时直接执行登录（跳过探测）"""
        config = LoginConfig(
            site_key="test",
            strategy="mock",
            base_url="https://example.com",
            login_url="/login",
            username="user",
            password="pass",
            fresh_ttl_seconds=1800,
            max_login_retries=1,
        )

        session = LoginSession(self.temp_dir, config)
        strategy = MockStrategy(login_result=LoginResult.SUCCESS)

        page = AsyncMock()
        context = AsyncMock()
        context.cookies = AsyncMock(return_value=[
            {"name": "session", "value": "new", "domain": "example.com", "path": "/"}
        ])

        result = await session.ensure_auth(page, context, strategy)

        assert result is True
        assert strategy.login_called is True  # 应该执行登录

    @pytest.mark.asyncio
    async def test_ensure_auth_login_fail(self):
        """测试登录失败"""
        config = LoginConfig(
            site_key="test",
            strategy="mock",
            base_url="https://example.com",
            login_url="/login",
            username="user",
            password="pass",
            max_login_retries=2,
        )

        session = LoginSession(self.temp_dir, config)
        strategy = MockStrategy(login_result=LoginResult.FAILED)

        page = AsyncMock()
        context = AsyncMock()

        result = await session.ensure_auth(page, context, strategy)

        assert result is False

    @pytest.mark.asyncio
    async def test_ensure_auth_locked(self):
        """测试账号锁定停止重试"""
        config = LoginConfig(
            site_key="test",
            strategy="mock",
            base_url="https://example.com",
            login_url="/login",
            username="user",
            password="pass",
            max_login_retries=3,
        )

        session = LoginSession(self.temp_dir, config)
        strategy = MockStrategy(login_result=LoginResult.LOCKED)

        page = AsyncMock()
        context = AsyncMock()

        result = await session.ensure_auth(page, context, strategy)

        assert result is False


class TestLoginConfig:
    """LoginConfig 测试"""

    def test_from_profile(self):
        """测试从 profile 构建配置"""
        profile = {
            "strategy": "ecscloud",
            "base_url": "https://example.com",
            "login_url": "/login",
            "probe_url": "/portal",
            "auth": {
                "header_name": "Authorization",
                "header_prefix": "Bearer ",
                "token_key": "token",
                "cookie_token_key": "session",
            },
            "captcha": {
                "type": "slider",
            },
            "session": {
                "fresh_ttl_seconds": 3600,
            },
        }

        config = LoginConfig.from_profile(profile)

        assert config.strategy == "ecscloud"
        assert config.base_url == "https://example.com"
        assert config.login_url == "/login"
        assert config.token_key == "token"
        assert config.captcha_type == "slider"
        assert config.fresh_ttl_seconds == 3600

    def test_host_property(self):
        """测试 host 属性提取"""
        config = LoginConfig(base_url="https://example.com:8080/path")
        assert config.host == "example.com"

        config = LoginConfig(base_url="http://10.0.0.1/login")
        assert config.host == "10.0.0.1"


class TestStrategyRegistry:
    """策略注册表测试"""

    def test_register_and_get(self):
        """测试注册和获取策略"""
        from lib.auth.strategies.registry import register, get_strategy

        register(MockStrategy)
        strategy = get_strategy("mock")

        assert isinstance(strategy, MockStrategy)
        assert strategy.key == "mock"

    def test_get_unknown_strategy(self):
        """测试获取未知策略"""
        from lib.auth.strategies.registry import get_strategy

        with pytest.raises(ValueError) as exc_info:
            get_strategy("unknown_strategy")

        assert "未知策略" in str(exc_info.value)

    def test_auto_register(self):
        """测试自动注册"""
        from lib.auth.strategies.registry import list_strategies

        strategies = list_strategies()

        # 应该自动注册了 4 个策略
        assert "ecscloud" in strategies
        assert "bcov" in strategies
        assert "ecso" in strategies
        assert "tianshu" in strategies
