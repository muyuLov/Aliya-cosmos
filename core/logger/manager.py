"""日志管理器：统一管理 Logger 实例，支持分层输出 + 失明模式

失明模式（blindMode）：
- 静默拦截命令（command/before-execute 吞掉）
- error/warn 置盲标志并丢弃（隐藏错误/剧本预览）
- 健康心跳仅输出 [失明模式] 运行状态=正常|需关注，无内容细节
- healthReportMinutes 默认 10，钳制 1-1440
"""

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

_MIN_HEALTH_REPORT_MINUTES = 1
_MAX_HEALTH_REPORT_MINUTES = 1440
_DEFAULT_HEALTH_REPORT_MINUTES = 10


def _resolve_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    resolved = _LEVEL_MAP.get(level.lower())
    if resolved is None:
        raise ValueError(f"不合法的日志级别: {level!r}，可选值: {list(_LEVEL_MAP)}")
    return resolved


def _clamp_health_report_minutes(value: int) -> int:
    """将 healthReportMinutes 钳制在 1-1440。"""
    return max(_MIN_HEALTH_REPORT_MINUTES, min(_MAX_HEALTH_REPORT_MINUTES, value))


class LogManager:
    """日志管理器，支持分层输出 + 失明模式。"""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config: dict[str, Any] = config or {}
        self._original_level: int = logging.INFO
        self._handlers: list[logging.Handler] = []
        self._listeners: list[logging.handlers.QueueListener] = []
        self._root_logger = logging.getLogger()

        # 清理根 Logger 上已有的 Handler
        for h in self._root_logger.handlers[:]:
            self._root_logger.removeHandler(h)
            h.close()

        # 失明模式配置
        blind_cfg = self._config.get("blind_mode", {})
        self._blind_mode: bool = blind_cfg.get("enabled", False)
        self.health_report_minutes: int = _clamp_health_report_minutes(
            blind_cfg.get("health_report_minutes", _DEFAULT_HEALTH_REPORT_MINUTES)
        )

        self._apply_config(self._config)

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def get_logger(self, name: str) -> logging.Logger:
        """获取指定名称的 Logger。"""
        return logging.getLogger(name)

    def set_global_level(self, level: str | int) -> None:
        resolved = _resolve_level(level)
        self._original_level = resolved
        self._apply_level(resolved)

    def set_debug_mode(self, enabled: bool) -> None:
        level = logging.DEBUG if enabled else self._original_level
        self._apply_level(level)
        third_party_level = logging.DEBUG if enabled else logging.WARNING
        logging.getLogger("httpx").setLevel(third_party_level)
        logging.getLogger("httpcore").setLevel(third_party_level)
        logging.getLogger("py2neo").setLevel(third_party_level)
        logging.getLogger("openai").setLevel(third_party_level)

    def set_blind_mode(self, enabled: bool) -> None:
        """动态切换失明模式。"""
        self._blind_mode = enabled

    def get_health_status(self) -> dict[str, Any]:
        """获取健康状态（失明模式下无内容细节）。"""
        status = {
            "status": "normal" if not self._blind_mode else "normal",
            "blind_mode": self._blind_mode,
        }
        if not self._blind_mode:
            status["details"] = {
                "level": self._original_level,
                "handler_count": len(self._handlers),
            }
        return status

    def _apply_level(self, level: int) -> None:
        self._root_logger.setLevel(level)
        for handler in self._handlers:
            handler.setLevel(level)

    def reload_config(self, config: dict[str, Any]) -> None:
        self._clear_handlers()
        self._config = config
        self._apply_config(config)

    def shutdown(self) -> None:
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
        effective_level = logging.DEBUG if debug_mode else level

        self._root_logger.setLevel(effective_level)

        if not debug_mode:
            logging.getLogger("httpx").setLevel(logging.WARNING)
            logging.getLogger("httpcore").setLevel(logging.WARNING)
            logging.getLogger("py2neo").setLevel(logging.WARNING)
            logging.getLogger("openai").setLevel(logging.WARNING)

        # 分层日志格式化器
        layered_cfg = config.get("layered", {})
        use_layered = layered_cfg.get("enabled", False)

        console_cfg: dict[str, Any] = config.get("console", {})
        if console_cfg.get("enabled", True):
            color = console_cfg.get("color", True)

            if use_layered:
                from core.logger.layered import LayeredLogFormatter

                formatter = LayeredLogFormatter(
                    color=color,
                    color_theme=layered_cfg.get("color_theme", "dark"),
                    kaomoji=layered_cfg.get("kaomoji", True),
                    density=layered_cfg.get("density", "standard"),
                )
                handler = logging.StreamHandler()
                handler.setFormatter(formatter)
                handler.setLevel(effective_level)
            else:
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
        self._root_logger.addHandler(handler)
        self._handlers.append(handler)

    def _clear_handlers(self) -> None:
        buffered_handlers: list[logging.Handler] = []
        for listener in self._listeners:
            buffered_handlers.extend(listener.handlers)

        for listener in self._listeners:
            listener.stop()
        self._listeners.clear()

        for handler in self._handlers:
            self._root_logger.removeHandler(handler)
            handler.close()
        self._handlers.clear()

        for handler in buffered_handlers:
            handler.close()
