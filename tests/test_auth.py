"""
test_auth.py — auth.py 单元测试

测试目标：
- AuthSession 初始化和配置解析
- resolve_credentials 凭据优先级
- load_auth_json 配置文件加载
- cookie 持久化方法
"""
import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from lib.auth import AuthSession


class TestAuthSessionInit:
    """测试 AuthSession 初始化"""

    def test_default_profile(self):
        """默认空 profile"""
        session = AuthSession({})
        assert session.profile == {}
        assert session.username == ""
        assert session.password == ""

    def test_profile_with_credentials(self):
        """从 profile 读取凭据"""
        profile = {
            "credentials": {
                "username": "testuser",
                "password": "testpass"
            }
        }
        session = AuthSession(profile)
        assert session.username == "testuser"
        assert session.password == "testpass"

    def test_explicit_credentials_override(self):
        """显式传入凭据覆盖 profile"""
        profile = {
            "credentials": {
                "username": "profile_user",
                "password": "profile_pass"
            }
        }
        session = AuthSession(profile, username="explicit_user", password="explicit_pass")
        assert session.username == "explicit_user"
        assert session.password == "explicit_pass"

    def test_env_var_credentials(self):
        """环境变量凭据"""
        profile = {
            "credentials": {
                "username_env": "CUSTOM_USER",
                "password_env": "CUSTOM_PASS"
            }
        }
        with patch.dict(os.environ, {"CUSTOM_USER": "env_user", "CUSTOM_PASS": "env_pass"}):
            session = AuthSession(profile)
            assert session.username == "env_user"
            assert session.password == "env_pass"

    def test_auth_config_parsing(self):
        """解析 auth 配置"""
        profile = {
            "auth": {
                "freshness_ttl_seconds": 3600,
                "fixed_headers": {"X-Custom": "value"}
            }
        }
        session = AuthSession(profile)
        assert session.ttl == 3600
        assert session.fixed_headers == {"X-Custom": "value"}


class TestLoadAuthJson:
    """测试 load_auth_json 静态方法"""

    def test_load_valid_auth_json(self, tmp_path):
        """加载有效的 auth.json"""
        auth_file = tmp_path / "auth.json"
        auth_data = {
            "auto_login": {"user": "json_user", "pass": "json_pass"},
            "login_url": "https://example.com/login",
            "target": "dw"
        }
        auth_file.write_text(json.dumps(auth_data), encoding="utf-8")

        result = AuthSession.load_auth_json(str(auth_file))
        assert result["username"] == "json_user"
        assert result["password"] == "json_pass"
        assert result["loginUrl"] == "https://example.com/login"
        assert result["target"] == "dw"

    def test_load_nonexistent_file(self):
        """加载不存在的文件返回空值"""
        result = AuthSession.load_auth_json("/nonexistent/auth.json")
        assert result == {"username": "", "password": "", "loginUrl": "", "target": ""}

    def test_load_invalid_json(self, tmp_path):
        """加载无效 JSON 返回空值"""
        auth_file = tmp_path / "auth.json"
        auth_file.write_text("invalid json {{{", encoding="utf-8")

        result = AuthSession.load_auth_json(str(auth_file))
        assert result == {"username": "", "password": "", "loginUrl": "", "target": ""}

    def test_load_partial_auth_json(self, tmp_path):
        """加载部分字段的 auth.json"""
        auth_file = tmp_path / "auth.json"
        auth_data = {
            "auto_login": {"user": "partial_user"}
            # 缺少 pass, login_url, target
        }
        auth_file.write_text(json.dumps(auth_data), encoding="utf-8")

        result = AuthSession.load_auth_json(str(auth_file))
        assert result["username"] == "partial_user"
        assert result["password"] == ""
        assert result["loginUrl"] == ""
        assert result["target"] == ""


class TestResolveCredentials:
    """测试 resolve_credentials 凭据优先级"""

    def test_explicit_params_highest_priority(self):
        """显式参数优先级最高"""
        session = AuthSession({})
        with patch.dict(os.environ, {"AUTO_LOGIN_USER": "env_user", "AUTO_LOGIN_PASS": "env_pass"}):
            result = session.resolve_credentials(username="param_user", password="param_pass")
            assert result["username"] == "param_user"
            assert result["password"] == "param_pass"

    def test_env_var_second_priority(self):
        """环境变量优先级第二"""
        session = AuthSession({})
        with patch.dict(os.environ, {"AUTO_LOGIN_USER": "env_user", "AUTO_LOGIN_PASS": "env_pass"}):
            result = session.resolve_credentials()
            assert result["username"] == "env_user"
            assert result["password"] == "env_pass"

    def test_auth_json_third_priority(self, tmp_path):
        """auth.json 优先级第三"""
        session = AuthSession({})
        session.context_path = str(tmp_path / "context.json")
        auth_file = tmp_path / "auth.json"
        auth_data = {
            "auto_login": {"user": "json_user", "pass": "json_pass"}
        }
        auth_file.write_text(json.dumps(auth_data), encoding="utf-8")

        # 清空环境变量确保 auth.json 生效
        with patch.dict(os.environ, {}, clear=True):
            result = session.resolve_credentials()
            assert result["username"] == "json_user"
            assert result["password"] == "json_pass"

    def test_profile_fourth_priority(self):
        """profile 凭据优先级最低"""
        profile = {
            "credentials": {
                "username": "profile_user",
                "password": "profile_pass"
            }
        }
        session = AuthSession(profile)
        # 清空环境变量
        with patch.dict(os.environ, {}, clear=True):
            result = session.resolve_credentials()
            assert result["username"] == "profile_user"
            assert result["password"] == "profile_pass"

    def test_custom_env_var_names(self):
        """自定义环境变量名"""
        profile = {
            "credentials": {
                "username_env": "MY_CUSTOM_USER",
                "password_env": "MY_CUSTOM_PASS"
            }
        }
        session = AuthSession(profile)
        with patch.dict(os.environ, {"MY_CUSTOM_USER": "custom_user", "MY_CUSTOM_PASS": "custom_pass"}):
            result = session.resolve_credentials()
            assert result["username"] == "custom_user"
            assert result["password"] == "custom_pass"


class TestSetContextPath:
    """测试 set_context_path 方法"""

    def test_set_context_path(self, tmp_path):
        """设置 context 路径并推导相关文件路径"""
        session = AuthSession({})
        context_path = tmp_path / "config" / "context.json"

        session.set_context_path(str(context_path))

        assert session.context_path == str(context_path)
        assert session._cookie_file == str(tmp_path / "config" / "cookies.json")
        assert session._meta_file == str(tmp_path / "config" / "meta.json")

    def test_set_context_path_none(self):
        """设置 None 路径"""
        session = AuthSession({})
        session.set_context_path(None)

        assert session.context_path is None


class TestHaveCredentials:
    """测试 have_credentials 方法"""

    def test_have_both_credentials(self):
        """同时有用户名和密码"""
        session = AuthSession({}, username="user", password="pass")
        assert session.have_credentials() is True

    def test_missing_username(self):
        """缺少用户名"""
        session = AuthSession({}, password="pass")
        assert session.have_credentials() is False

    def test_missing_password(self):
        """缺少密码"""
        session = AuthSession({}, username="user")
        assert session.have_credentials() is False

    def test_empty_credentials(self):
        """空凭据"""
        session = AuthSession({}, username="", password="")
        assert session.have_credentials() is False


class TestAutoLoginToggle:
    """测试自动登录开关"""

    def test_auto_login_enabled_by_default(self):
        """默认启用自动登录"""
        with patch.dict(os.environ, {}, clear=True):
            session = AuthSession({})
            assert session.auto_login_enabled is True

    def test_auto_login_disabled_via_env(self):
        """通过环境变量禁用自动登录"""
        with patch.dict(os.environ, {"AUTO_LOGIN": "0"}):
            session = AuthSession({})
            assert session.auto_login_enabled is False

    def test_auto_login_enabled_via_env(self):
        """通过环境变量启用自动登录"""
        with patch.dict(os.environ, {"AUTO_LOGIN": "1"}):
            session = AuthSession({})
            assert session.auto_login_enabled is True
