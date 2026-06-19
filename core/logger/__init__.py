"""日志模块公共接口"""

from __future__ import annotations

import logging
from typing import Any

from core.logger.formatter import JSONFormatter, StructuredFormatter
from core.logger.manager import LogManager

_DEFAULT_CONFIG_PATH = "data/config/main.yml"

_manager: LogManager | None = None


def _ensure_manager() -> LogManager:
    global _manager
    if _manager is None:
        _manager = LogManager()
    return _manager


def setup(config: dict[str, Any] | str | None = None) -> LogManager:
    """
    初始化全局日志管理器。

    应在应用启动时调用一次，后续通过 ``get_logger`` 获取 Logger。

    支持三种调用方式：
    1. ``setup()``：自动从默认配置路径 ``data/config/main.yml`` 加载 ``cosmos.logger`` 配置
    2. ``setup("path/to/config.yml")``：从指定配置路径加载 ``cosmos.logger`` 配置
    3. ``setup({"level": "debug", ...})``：直接传入配置字典（向后兼容）

    Args:
        config: 日志配置字典、配置文件路径，或 None（使用默认路径）。

    Returns:
        全局 LogManager 实例。
    """
    global _manager

    if isinstance(config, str):
        from core.config import get_config_instance

        config_dict: dict[str, Any] | None = get_config_instance(config).get("cosmos.logger")
    elif config is None:
        from core.config import get_config_instance

        config_dict = get_config_instance(_DEFAULT_CONFIG_PATH).get("cosmos.logger")
    else:
        config_dict = config

    _manager = LogManager(config_dict)
    return _manager


def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的 Logger。

    若全局管理器尚未初始化，自动以默认配置初始化。

    Args:
        name: Logger 名称，通常传入 ``__name__``。

    Returns:
        配置好的 Logger 实例。
    """
    return _ensure_manager().get_logger(name)


def get_manager() -> LogManager:
    """
    获取全局 LogManager 实例。

    若尚未初始化，自动以默认配置初始化。

    Returns:
        全局 LogManager 实例。
    """
    return _ensure_manager()


__all__ = [
    "LogManager",
    "StructuredFormatter",
    "JSONFormatter",
    "setup",
    "get_logger",
    "get_manager",
]
