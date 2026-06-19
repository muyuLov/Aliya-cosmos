"""日志格式化器：结构化彩色输出与 JSON 格式输出"""

import json
import logging
from datetime import datetime, timezone
from typing import ClassVar

# ── ANSI 样式常量 ────────────────────────────────────────────────────────────
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"

_FG_WHITE = "\033[97m"
_FG_CYAN = "\033[36m"
_FG_GREEN = "\033[32m"
_FG_YELLOW = "\033[33m"
_FG_RED = "\033[31m"
_FG_MAGENTA = "\033[35m"

# 背景色（仅 CRITICAL 使用）
_BG_RED = "\033[41m"

# ── 各部分颜色配置 ────────────────────────────────────────────────────────────
# 时间戳：暗白，低调不抢眼
_COLOR_TS = _DIM + _FG_WHITE
_COLOR_THREAD = _FG_CYAN
_COLOR_SEP = _DIM + _FG_WHITE
_COLOR_ARROW = _DIM + _FG_WHITE

# 级别名颜色（含消息体颜色，保持视觉一致）
_LEVEL_STYLES: dict[int, tuple[str, str]] = {
    #                    级别色                消息色
    logging.DEBUG: (_FG_CYAN, _DIM + _FG_WHITE),
    logging.INFO: (_BOLD + _FG_GREEN, _FG_WHITE),
    logging.WARNING: (_BOLD + _FG_YELLOW, _FG_YELLOW),
    logging.ERROR: (_BOLD + _FG_RED, _FG_RED),
    logging.CRITICAL: (_BOLD + _BG_RED + _FG_WHITE, _BOLD + _FG_RED),
}


class StructuredFormatter(logging.Formatter):
    """
    结构化日志格式化器，输出固定格式并支持彩色终端显示。

    输出格式：
        ``时间戳 | 线程名 | 日志级别 --> 消息内容``

    Args:
        color: 是否启用 ANSI 彩色输出，默认 True。
    """

    _LEVEL_STYLES: ClassVar[dict[int, tuple[str, str]]] = _LEVEL_STYLES

    def __init__(self, color: bool = True) -> None:
        super().__init__()
        self._color = color

    def format(self, record: logging.LogRecord) -> str:
        """
        格式化日志记录。

        Args:
            record: 标准 LogRecord 对象。

        Returns:
            格式化后的日志字符串。
        """
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        # 固定 20 字符宽度：保证各列对齐，超长线程名截断以防破坏布局
        thread_raw = record.threadName or ""
        thread_name = f"{thread_raw[:20]:<20}"
        message = record.getMessage()

        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"

        # 先在裸文本上填充宽度，再包裹颜色码——ANSI 转义字符会撑宽字符串，
        # 若先着色再 ljust 会导致视觉对齐错位
        padded = f"{record.levelname:<8}"
        if self._color:
            level_style, msg_style = self._LEVEL_STYLES.get(record.levelno, ("", _FG_WHITE))
            ts_str = f"{_COLOR_TS}{ts}{_RESET}"
            thread_str = f"{_COLOR_THREAD}{thread_name}{_RESET}"
            sep = f"{_COLOR_SEP}|{_RESET}"
            level_str = f"{level_style}{padded}{_RESET}"
            arrow = f"{_COLOR_ARROW}-->{_RESET}"
            msg_str = f"{msg_style}{message}{_RESET}"
            return f"{ts_str} {sep} {thread_str} {sep} {level_str}{sep} {arrow} {msg_str}"
        else:
            return f"{ts} | {thread_name} | {padded}| --> {message}"


class JSONFormatter(logging.Formatter):
    """
    JSON 结构化日志格式化器，适用于日志采集与分析场景。

    输出字段：timestamp、thread、level、logger、message，以及 extra 附加字段。
    """

    # 标准 LogRecord 内置属性白名单，过滤后剩余的才是cosmos通过 extra= 注入的自定义字段
    _BUILTIN_ATTRS: ClassVar[frozenset[str]] = frozenset(
        {
            "args",
            "asctime",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "message",
            "module",
            "msecs",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "taskName",
            "thread",
            "threadName",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        """
        将 LogRecord 序列化为 JSON 字符串。

        Args:
            record: 标准 LogRecord 对象。

        Returns:
            JSON 格式的日志字符串。
        """
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload: dict = {
            "timestamp": ts,
            "thread": record.threadName,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # 将cosmos通过 extra= 传入的自定义字段附加到输出；
        # 白名单过滤确保内置属性不会污染业务字段
        for key, val in record.__dict__.items():
            if key not in self._BUILTIN_ATTRS:
                payload[key] = val

        return json.dumps(payload, ensure_ascii=False)
