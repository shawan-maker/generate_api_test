"""
registry.py — 策略注册表

管理所有登录策略的注册和获取。新增策略只需在此注册。
"""

import logging
from typing import Dict, Type

from ..strategy_base import LoginStrategy

LOG = logging.getLogger("strategy_registry")

# 策略注册表
_REGISTRY: Dict[str, Type[LoginStrategy]] = {}


def register(strategy_cls: Type[LoginStrategy]):
    """注册策略类

    Args:
        strategy_cls: 策略类（LoginStrategy 的子类）
    """
    if not hasattr(strategy_cls, 'key') or not strategy_cls.key:
        raise ValueError(f"策略类 {strategy_cls.__name__} 缺少 key 属性")
    _REGISTRY[strategy_cls.key] = strategy_cls
    LOG.debug(f"策略已注册: {strategy_cls.key} ({strategy_cls.label})")


def get_strategy(key: str) -> LoginStrategy:
    """获取策略实例

    Args:
        key: 策略标识（如 "ecscloud", "bcov", "ecso", "tianshu"）

    Returns:
        LoginStrategy: 策略实例

    Raises:
        ValueError: 未知策略
    """
    if key not in _REGISTRY:
        available = ", ".join(_REGISTRY.keys())
        raise ValueError(f"未知策略: {key}，可用: {available}")
    return _REGISTRY[key]()


def list_strategies() -> Dict[str, str]:
    """列出所有已注册策略

    Returns:
        dict: {key: label}
    """
    return {key: cls.label for key, cls in _REGISTRY.items()}


# ========== 自动注册所有策略 ==========
def _auto_register():
    """自动导入并注册所有策略模块"""
    try:
        from . import ecscloud
        register(ecscloud.EcsCloudStrategy)
    except ImportError as e:
        LOG.debug(f"EcsCloud 策略未加载: {e}")

    try:
        from . import bcov
        register(bcov.BcovStrategy)
    except ImportError as e:
        LOG.debug(f"BCov 策略未加载: {e}")

    try:
        from . import ecso
        register(ecso.EcsoStrategy)
    except ImportError as e:
        LOG.debug(f"ECSO 策略未加载: {e}")

    try:
        from . import tianshu
        register(tianshu.TianshuStrategy)
    except ImportError as e:
        LOG.debug(f"Tianshu 策略未加载: {e}")


# 模块加载时自动注册
_auto_register()
