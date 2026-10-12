"""
test_strategies.py — 登录策略单元测试

测试目标：
- 策略注册和加载
- 各策略的选择器和默认配置
- 策略接口一致性
"""
import pytest
from lib.auth.strategies.registry import get_strategy, list_strategies
from lib.auth.strategy_base import LoginStrategy, LoginResult


class TestStrategyRegistry:
    """测试策略注册表"""

    def test_list_strategies(self):
        """列出所有已注册策略"""
        strategies = list_strategies()
        assert len(strategies) >= 4
        assert "ecscloud" in strategies
        assert "bcov" in strategies
        assert "ecso" in strategies
        assert "tianshu" in strategies

    def test_get_ecscloud_strategy(self):
        """获取 EcsCloud 策略"""
        strategy = get_strategy("ecscloud")
        assert strategy is not None
        assert strategy.key == "ecscloud"
        assert "EcsCloud" in strategy.label

    def test_get_bcov_strategy(self):
        """获取 BCov 策略"""
        strategy = get_strategy("bcov")
        assert strategy is not None
        assert strategy.key == "bcov"
        assert "BCov" in strategy.label

    def test_get_ecso_strategy(self):
        """获取 ECSO 策略"""
        strategy = get_strategy("ecso")
        assert strategy is not None
        assert strategy.key == "ecso"
        assert "ECSO" in strategy.label

    def test_get_tianshu_strategy(self):
        """获取天枢策略"""
        strategy = get_strategy("tianshu")
        assert strategy is not None
        assert strategy.key == "tianshu"
        assert "天枢" in strategy.label

    def test_get_nonexistent_strategy(self):
        """获取不存在的策略应该抛出异常"""
        with pytest.raises(ValueError, match="未知策略"):
            get_strategy("nonexistent")


class TestStrategyInterface:
    """测试策略接口一致性"""

    @pytest.mark.parametrize("strategy_key", ["ecscloud", "bcov", "ecso", "tianshu"])
    def test_strategy_has_required_methods(self, strategy_key):
        """所有策略必须实现必需的方法"""
        strategy = get_strategy(strategy_key)
        assert isinstance(strategy, LoginStrategy)
        assert hasattr(strategy, "login")
        assert hasattr(strategy, "probe")
        assert hasattr(strategy, "get_selectors")
        assert hasattr(strategy, "get_defaults")

    @pytest.mark.parametrize("strategy_key", ["ecscloud", "bcov", "ecso", "tianshu"])
    def test_strategy_has_key_and_label(self, strategy_key):
        """所有策略必须有 key 和 label 属性"""
        strategy = get_strategy(strategy_key)
        assert hasattr(strategy, "key")
        assert hasattr(strategy, "label")
        assert strategy.key == strategy_key
        assert len(strategy.label) > 0

    @pytest.mark.parametrize("strategy_key", ["ecscloud", "bcov", "ecso", "tianshu"])
    def test_get_selectors_returns_dict(self, strategy_key):
        """get_selectors 必须返回字典"""
        strategy = get_strategy(strategy_key)
        selectors = strategy.get_selectors()
        assert isinstance(selectors, dict)
        assert "username" in selectors
        assert "password" in selectors

    @pytest.mark.parametrize("strategy_key", ["ecscloud", "bcov", "ecso", "tianshu"])
    def test_get_defaults_returns_dict(self, strategy_key):
        """get_defaults 必须返回字典"""
        strategy = get_strategy(strategy_key)
        defaults = strategy.get_defaults()
        assert isinstance(defaults, dict)
        assert "login_url" in defaults


class TestEcsCloudStrategy:
    """测试 EcsCloud 策略特定功能"""

    def test_ecscloud_selectors(self):
        """EcsCloud 选择器"""
        strategy = get_strategy("ecscloud")
        selectors = strategy.get_selectors()
        assert "username" in selectors
        assert "password" in selectors
        assert "登录" in selectors["login_button"]

    def test_ecscloud_defaults(self):
        """EcsCloud 默认配置"""
        strategy = get_strategy("ecscloud")
        defaults = strategy.get_defaults()
        assert "/estack/web/estack/login" in defaults["login_url"]
        assert defaults["captcha_type"] == "slider"


class TestBcovStrategy:
    """测试 BCov 策略特定功能"""

    def test_bcov_selectors(self):
        """BCov 选择器"""
        strategy = get_strategy("bcov")
        selectors = strategy.get_selectors()
        assert "username" in selectors
        assert "password" in selectors

    def test_bcov_defaults(self):
        """BCov 默认配置"""
        strategy = get_strategy("bcov")
        defaults = strategy.get_defaults()
        assert "/bcov/login" in defaults["login_url"]
        assert defaults["captcha_type"] == "puzzle"


class TestEcsoStrategy:
    """测试 ECSO 策略特定功能"""

    def test_ecso_selectors(self):
        """ECSO 选择器"""
        strategy = get_strategy("ecso")
        selectors = strategy.get_selectors()
        assert "username" in selectors
        assert "密码" in selectors["password"]

    def test_ecso_defaults(self):
        """ECSO 默认配置"""
        strategy = get_strategy("ecso")
        defaults = strategy.get_defaults()
        assert "/ecso/login" in defaults["login_url"]
        assert defaults["captcha_type"] == "arithmetic"


class TestTianshuStrategy:
    """测试天枢策略特定功能"""

    def test_tianshu_selectors(self):
        """天枢选择器"""
        strategy = get_strategy("tianshu")
        selectors = strategy.get_selectors()
        assert "username" in selectors
        assert "请输入用户名" in selectors["username"]

    def test_tianshu_defaults(self):
        """天枢默认配置"""
        strategy = get_strategy("tianshu")
        defaults = strategy.get_defaults()
        assert "#/login" in defaults["login_url"]
        assert defaults["captcha_type"] == "ocr"


class TestLoginResult:
    """测试登录结果枚举"""

    def test_login_result_values(self):
        """LoginResult 枚举值"""
        assert LoginResult.SUCCESS.value == "success"
        assert LoginResult.FAILED.value == "failed"
        assert LoginResult.LOCKED.value == "locked"

    def test_login_result_comparison(self):
        """LoginResult 比较"""
        result = LoginResult.SUCCESS
        assert result == LoginResult.SUCCESS
        assert result != LoginResult.FAILED
        assert result != LoginResult.LOCKED
