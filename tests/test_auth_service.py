"""
test_auth_service.py — AuthService 单元测试

测试目标：
- AuthService 初始化和配置解析
- _resolve_credentials() 凭据优先级
- check_cookie_fresh() 新鲜度检查
- ensure_http_session() HTTP 会话构建
"""
import os
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from core.discovery.auth_service import AuthService


class TestAuthServiceInit:
    """测试 AuthService 初始化"""

    def test_init_basic(self):
        """基本初始化"""
        project_dir = Path("/tmp/project")
        workspace_dir = Path("/tmp/workspace")
        profile = {"base_url": "https://example.com"}

        service = AuthService(project_dir, workspace_dir, profile)

        assert service.project_dir == project_dir
        assert service.workspace_dir == workspace_dir
        assert service.profile == profile

    def test_init_with_overrides(self):
        """带用户名/密码覆盖参数"""
        service = AuthService(
            Path("/tmp/project"),
            Path("/tmp/workspace"),
            {},
            user_override="admin",
            pass_override="secret123"
        )

        assert service._user_override == "admin"
        assert service._pass_override == "secret123"


class TestResolveCredentials:
    """测试 _resolve_credentials() 凭据优先级"""

    def test_priority_override_first(self):
        """优先级：显式覆盖参数 > credentials.yaml > 环境变量 > profile.yaml"""
        profile = {
            "credentials": {
                "username": "profile_user",
                "password": "profile_pass"
            }
        }

        service = AuthService(
            Path("/tmp/project"),
            Path("/tmp/workspace"),
            profile,
            user_override="override_user",
            pass_override="override_pass"
        )

        creds = service._resolve_credentials()
        assert creds["username"] == "override_user"
        assert creds["password"] == "override_pass"

    def test_priority_env_vars(self):
        """优先级：环境变量 > profile.yaml"""
        profile = {
            "credentials": {
                "username": "profile_user",
                "password": "profile_pass",
                "username_env": "APP_USER",
                "password_env": "APP_PASS"
            }
        }

        service = AuthService(Path("/tmp/project"), Path("/tmp/workspace"), profile)

        with patch.dict(os.environ, {"APP_USER": "env_user", "APP_PASS": "env_pass"}):
            creds = service._resolve_credentials()
            assert creds["username"] == "env_user"
            assert creds["password"] == "env_pass"

    def test_priority_profile_yaml(self):
        """优先级：profile.yaml（最低优先级）"""
        profile = {
            "credentials": {
                "username": "profile_user",
                "password": "profile_pass"
            }
        }

        service = AuthService(Path("/tmp/project"), Path("/tmp/workspace"), profile)
        creds = service._resolve_credentials()

        assert creds["username"] == "profile_user"
        assert creds["password"] == "profile_pass"

    def test_empty_profile(self):
        """空 profile"""
        service = AuthService(Path("/tmp/project"), Path("/tmp/workspace"), {})
        creds = service._resolve_credentials()

        assert creds["username"] == ""
        assert creds["password"] == ""


class TestCheckCookieFresh:
    """测试 check_cookie_fresh() 新鲜度检查"""

    def test_fresh_cookie(self, tmp_path):
        """新鲜 cookie 返回 True"""
        workspace_dir = tmp_path
        config_dir = workspace_dir / "output" / "config"
        config_dir.mkdir(parents=True)

        # 创建 meta.json（刚刚保存）
        meta = {"savedAt": int(__import__("time").time() * 1000), "count": 2}
        (config_dir / "cookies.meta.json").write_text(json.dumps(meta))

        profile = {"session": {"fresh_ttl_seconds": 1800}}
        service = AuthService(Path("/tmp/project"), workspace_dir, profile)

        assert service.check_cookie_fresh() is True

    def test_stale_cookie(self, tmp_path):
        """过期 cookie 返回 False"""
        workspace_dir = tmp_path
        config_dir = workspace_dir / "output" / "config"
        config_dir.mkdir(parents=True)

        # 创建旧 meta.json（2 小时前）
        old_time = int(__import__("time").time() * 1000) - 2 * 3600 * 1000
        meta = {"savedAt": old_time, "count": 2}
        (config_dir / "cookies.meta.json").write_text(json.dumps(meta))

        profile = {"session": {"fresh_ttl_seconds": 1800}}
        service = AuthService(Path("/tmp/project"), workspace_dir, profile)

        assert service.check_cookie_fresh() is False

    def test_no_meta_file(self, tmp_path):
        """无 meta 文件返回 False"""
        workspace_dir = tmp_path
        (workspace_dir / "output" / "config").mkdir(parents=True)

        service = AuthService(Path("/tmp/project"), workspace_dir, {})
        assert service.check_cookie_fresh() is False


class TestEnsureHttpSession:
    """测试 ensure_http_session() HTTP 会话构建"""

    def test_build_session_with_cookie(self, tmp_path):
        """有 cookie 时构建 session"""
        workspace_dir = tmp_path
        config_dir = workspace_dir / "output" / "config"
        config_dir.mkdir(parents=True)

        # 创建 cookies.json
        cookies = [
            {"name": "accessToken", "value": "token123", "domain": "example.com", "path": "/"},
            {"name": "sessionId", "value": "sess456", "domain": "example.com", "path": "/"}
        ]
        (config_dir / "cookies.json").write_text(json.dumps(cookies))

        profile = {
            "base_url": "https://example.com",
            "auth": {
                "header_name": "Authorization",
                "header_prefix": "Bearer ",
                "cookie_token_key": "accessToken"
            }
        }

        service = AuthService(Path("/tmp/project"), workspace_dir, profile)
        session = service.ensure_http_session()

        assert session is not None
        assert "Authorization" in session.headers
        assert session.headers["Authorization"] == "Bearer token123"
        assert len(session.cookies) == 2

    def test_no_cookie_returns_none(self, tmp_path):
        """无 cookie 返回 None"""
        workspace_dir = tmp_path
        (workspace_dir / "output" / "config").mkdir(parents=True)

        service = AuthService(Path("/tmp/project"), workspace_dir, {})
        session = service.ensure_http_session()

        assert session is None


class TestBuildLoginConfig:
    """测试 _build_login_config() 配置构建"""

    def test_basic_config(self):
        """基本配置构建"""
        profile = {
            "base_url": "https://example.com",
            "login_url": "https://example.com/login",
            "probe_url": "/portal",
            "strategy": "ecscloud",
            "auth": {
                "header_name": "Authorization",
                "header_prefix": "Bearer ",
                "token_key": "accessToken"
            },
            "session": {
                "fresh_ttl_seconds": 1800,
                "max_login_retries": 2
            },
            "credentials": {
                "username": "testuser",
                "password": "testpass"
            }
        }

        service = AuthService(Path("/tmp/project"), Path("/tmp/workspace"), profile)
        config = service._build_login_config()

        assert config.base_url == "https://example.com"
        assert config.login_url == "https://example.com/login"
        assert config.probe_url == "/portal"
        assert config.strategy == "ecscloud"
        assert config.username == "testuser"
        assert config.password == "testpass"
        assert config.header_name == "Authorization"
        assert config.header_prefix == "Bearer "
        assert config.token_key == "accessToken"
        assert config.fresh_ttl_seconds == 1800
        assert config.max_login_retries == 2
