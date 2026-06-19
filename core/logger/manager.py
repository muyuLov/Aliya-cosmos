"""日志管理器：统一管理 Logger 实例，支持动态配置与热重载"""

import logging
import logging.handlers
from typing import Any

from core.logger.handlers import build_console_handler, build_file_handler

# 日志级别字符串到常量的映射
_LEVEL_MAP: dict[str, int] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


def _resolve_level(level: str | int) -> int:
    """
    将字符串或整数级别统一转换为 logging 级别常量。

    Args:
        level: 字符串（如 "info"）或整数（如 20）。

    Returns:
        对应的 logging 级别整数。

    Raises:
        ValueError: 字符串级别不合法时抛出。
    """
    if isinstance(level, int):
        return level
    resolved = _LEVEL_MAP.get(level.lower())
    if resolved is None:
        raise ValueError(f"不合法的日志级别: {level!r}，可选值: {list(_LEVEL_MAP)}")
    return resolved


class LogManager:
    """
    日志管理器，单例模式，统一管理所有 Logger 实例。

    通过配置字典初始化，支持控制台与文件双路输出，
    可在运行时动态调整级别或切换 debug 模式。

    文件输出采用异步写入（QueueHandler + QueueListener），
    日志调用不阻塞业务线程，支持按天或按大小轮转。

    配置字典示例::

        {
            "level": "info",
            "debug": False,
            "console": {"enabled": True, "color": True},
            "file": {
                "enabled": True,
                "path": "data/log/app.log",
                "rotate": "session",        # "session"（每次启动）/"timed"（按天）/"sized"（按大小）
                "when": "midnight",         # timed 模式轮转周期
                "backup_count": 30,         # 保留历史文件数
                "max_bytes": 10485760,      # sized 模式单文件上限
                "buffer_size": 100,         # 缓冲区大小（条数）
                "flush_interval": 5.0,      # 自动刷新间隔（秒）
                "max_queue_size": 10000,    # 队列最大容量
            },
            "structured": False,
        }

    Args:
        config: 日志配置字典，为 None 时使用默认配置（INFO 级别，仅控制台）。
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config: dict[str, Any] = config or {}
        self._original_level: int = logging.INFO
        self._handlers: list[logging.Handler] = []
        self._listeners: list[logging.handlers.QueueListener] = []
        self._root_logger = logging.getLogger()
        # 清理根 Logger 上已有的 Handler，防止多次调用 setup() 时日志重复输出
        for h in self._root_logger.handlers[:]:
            self._root_logger.removeHandler(h)
            h.close()
        self._apply_config(self._config)

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def get_logger(self, name: str) -> logging.Logger:
        """
        获取指定名称的 Logger，已继承根 Logger 的 Handler 配置。

        Args:
            name: Logger 名称，通常使用模块名（如 ``__name__``）。

        Returns:
            配置好的 Logger 实例。
        """
        return logging.getLogger(name)

    def set_global_level(self, level: str | int) -> None:
        """
        动态修改全局日志级别。

        Args:
            level: 目标级别，字符串（"debug"/"info" 等）或整数。
        """
        resolved = _resolve_level(level)
        self._original_level = resolved
        self._apply_level(resolved)

    def set_debug_mode(self, enabled: bool) -> None:
        """
        快速切换调试模式。

        Args:
            enabled: True 时将全局级别设为 DEBUG，False 时恢复原级别。
        """
        level = logging.DEBUG if enabled else self._original_level
        self._apply_level(level)
        # debug 模式下开放第三方库日志，否则压制
        third_party_level = logging.DEBUG if enabled else logging.WARNING
        logging.getLogger("httpx").setLevel(third_party_level)
        logging.getLogger("httpcore").setLevel(third_party_level)

    def _apply_level(self, level: int) -> None:
        """同步设置根 Logger 与所有 Handler 的日志级别。"""
        self._root_logger.setLevel(level)
        for handler in self._handlers:
            handler.setLevel(level)

    def reload_config(self, config: dict[str, Any]) -> None:
        """
        动态重载配置，停止旧 listener、清除旧 Handler 后重新应用新配置。

        Args:
            config: 新的日志配置字典。
        """
        self._clear_handlers()
        self._config = config
        self._apply_config(config)

    def shutdown(self) -> None:
        """
        优雅关闭：等待异步队列消费完毕后停止所有 listener。

        应在应用退出前调用，确保缓冲队列中的日志全部落盘。
        """
        self._clear_handlers()

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _apply_config(self, config: dict[str, Any]) -> None:
        """解析配置并初始化根 Logger 的 Handler。"""
        level_str = config.get("level", "info")
        debug_mode = config.get("debug", False)
        structured = config.get("structured", False)

        level = _resolve_level(level_str)
        self._original_level = level

        # debug 模式优先于 level 配置，确保开发时能看到所有细节日志
        effective_level = logging.DEBUG if debug_mode else level

        self._root_logger.setLevel(effective_level)
        self._root_logger.propagate = False

        # 压制第三方库的冗余日志（如 httpx 的每条 HTTP 请求记录）
        # 仅在非 debug 模式下生效，debug 模式保留所有日志便于排查
        if not debug_mode:
            logging.getLogger("httpx").setLevel(logging.WARNING)
            logging.getLogger("httpcore").setLevel(logging.WARNING)

        console_cfg: dict[str, Any] = config.get("console", {})
        if console_cfg.get("enabled", True):
            color = console_cfg.get("color", True)
            handler = build_console_handler(
                level=effective_level, color=color, structured=structured
            )
            self._add_handler(handler)

        file_cfg: dict[str, Any] = config.get("file", {})
        if file_cfg.get("enabled", False):
            queue_handler, listener = build_file_handler(
                file_path=file_cfg.get("path", "data/log/app.log"),
                level=effective_level,
                structured=structured,
                rotate=file_cfg.get("rotate", "session"),
                when=file_cfg.get("when", "midnight"),
                backup_count=file_cfg.get("backup_count", 30),
                max_bytes=file_cfg.get("max_bytes", 10 * 1024 * 1024),
                buffer_size=file_cfg.get("buffer_size", 100),
                flush_interval=file_cfg.get("flush_interval", 5.0),
                max_queue_size=file_cfg.get("max_queue_size", 10000),
            )
            listener.start()
            self._listeners.append(listener)
            self._add_handler(queue_handler)

    def _add_handler(self, handler: logging.Handler) -> None:
        """向根 Logger 注册 Handler 并记录引用。"""
        self._root_logger.addHandler(handler)
        self._handlers.append(handler)

    def _clear_handlers(self) -> None:
        """停止所有 listener，从根 Logger 移除并关闭所有 Handler。"""
        # 先停止 listener，确保队列中的日志全部落盘后再关闭底层 handler，
        # 顺序不能颠倒，否则底层 handler 关闭后队列中的记录将永久丢失
        for listener in self._listeners:
            listener.stop()
        self._listeners.clear()

        for handler in self._handlers:
            self._root_logger.removeHandler(handler)
            handler.close()
        self._handlers.clear()
