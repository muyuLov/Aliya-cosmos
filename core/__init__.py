"""
core 包入口

暴露各子模块的核心接口，方便顶层统一导入。
"""

from core.config import ConfigManager, get_config_instance
from core.exception import ExceptionHandler, StructuredException, catch_context
from core.logger import get_logger, get_manager, setup

__all__ = [
    # 日志
    "setup",
    "get_logger",
    "get_manager",
    # 配置
    "ConfigManager",
    "get_config_instance",
    # 异常
    "StructuredException",
    "ExceptionHandler",
    "catch_context",
]
