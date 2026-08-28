"""分层日志核心（LayeredLogFormatter）

阶段感知 + LogAction 枚举动作检测 + 明暗 256 色主题 +
KAOMOJI/SYMBOLS 双模式 + 三档密度（summary < standard < diagnostic）+ tree 字段布局。
"""

from __future__ import annotations

import enum
import logging
import re
from datetime import datetime, timezone


# ── LogAction 16 枚举 ──────────────────────────────────────────────────────

class LogAction(enum.Enum):
    """日志动作枚举（16 个）。"""
    RECEIVE = "receive"
    SEND = "send"
    PROCESSING = "processing"
    COMPLETE = "complete"
    TRIGGER = "trigger"
    EMOTION = "emotion"
    MEMORY = "memory"
    ADVANCE = "advance"
    AGENCY = "agency"
    GROUP = "group"
    ERROR = "error"
    RETRY = "retry"
    WARNING = "warning"
    WAITING = "waiting"
    SYSTEM = "system"


# ── 密度等级 ────────────────────────────────────────────────────────────────

class DensityLevel(enum.IntEnum):
    """三档密度（与 logging.level 独立叠加）。"""
    SUMMARY = 0      # 仅结果摘要
    STANDARD = 1     # 调度/模型活动
    DIAGNOSTIC = 2   # 跳过原因/内部计数


# ── 动作检测 ────────────────────────────────────────────────────────────────

_ACTION_PATTERNS: list[tuple[LogAction, re.Pattern[str]]] = [
    (LogAction.RECEIVE, re.compile(r"接收到|收到|receive|incoming|用户消息|user_message")),
    (LogAction.SEND, re.compile(r"发送|发送回复|send|outgoing|投递|deliver")),
    (LogAction.PROCESSING, re.compile(r"处理中|processing|运行|执行")),
    (LogAction.COMPLETE, re.compile(r"完成|complete|done|成功|finished")),
    (LogAction.TRIGGER, re.compile(r"触发|trigger|激活|fire")),
    (LogAction.EMOTION, re.compile(r"情绪|情感|氛围|alter|emotion|emotional")),
    (LogAction.MEMORY, re.compile(r"记忆|写入记忆|memory|recall|fact|overlay|continuity")),
    (LogAction.ADVANCE, re.compile(r"推进|advance|auto_advance|自动推进|生活推进")),
    (LogAction.AGENCY, re.compile(r"主体|agency|主动联系|proactive|联系候选")),
    (LogAction.GROUP, re.compile(r"群组|group|多人|multi.*participant")),
    (LogAction.ERROR, re.compile(r"错误|error|失败|exception|failed|崩溃")),
    (LogAction.RETRY, re.compile(r"重试|retry|重新尝试")),
    (LogAction.WARNING, re.compile(r"警告|warn|注意|deprecat")),
    (LogAction.WAITING, re.compile(r"等待|waiting|挂起|pending|timeout|超时")),
    (LogAction.SYSTEM, re.compile(r"系统|system|启动|shutdown|初始化|init")),
]


def detect_log_action(message: str) -> LogAction:
    """从消息内容判定 LogAction。"""
    for action, pattern in _ACTION_PATTERNS:
        if pattern.search(message):
            return action
    return LogAction.SYSTEM


# ── 颜文字与符号 ────────────────────────────────────────────────────────────

_KAOMOJI: dict[LogAction, str] = {
    LogAction.RECEIVE: "(*^▽^*)",
    LogAction.SEND: "(~*^_^*)~",
    LogAction.PROCESSING: "(o^^o)",
    LogAction.COMPLETE: "(ノ´ヮ`)ノ*: ・゚✧",
    LogAction.TRIGGER: "(心跳加速!)",
    LogAction.EMOTION: "(♡´oba`♡)",
    LogAction.MEMORY: "(*・ω・)memory",
    LogAction.ADVANCE: "(~˘▾˘~)advance",
    LogAction.AGENCY: "(•̀ᴗ•́)وagency",
    LogAction.GROUP: "(*群体*)",
    LogAction.ERROR: "(；д；)",
    LogAction.RETRY: "(▰ SD ▱)",
    LogAction.WARNING: "(；一_一)",
    LogAction.WAITING: "( _._PWM._)",
    LogAction.SYSTEM: "(neutral)",
}

_SYMBOLS: dict[LogAction, str] = {
    LogAction.RECEIVE: "←",
    LogAction.SEND: "→",
    LogAction.PROCESSING: "⋯",
    LogAction.COMPLETE: "✓",
    LogAction.TRIGGER: "⚡",
    LogAction.EMOTION: "♡",
    LogAction.MEMORY: "⊛",
    LogAction.ADVANCE: "▸",
    LogAction.AGENCY: "⊕",
    LogAction.GROUP: "⊞",
    LogAction.ERROR: "✗",
    LogAction.RETRY: "↻",
    LogAction.WARNING: "⚠",
    LogAction.WAITING: "◌",
    LogAction.SYSTEM: "●",
}


# ── 字段抽取 ────────────────────────────────────────────────────────────────

# 匹配 "键=值" 或 "键=值 " 模式
_FIELD_PATTERN = re.compile(r"(\w[\w\u4e00-\u9fff]*)=([^\s,]+)")

# 中文 label 映射
_LABEL_MAP: dict[str, str] = {
    "story_id": "故事",
    "participant": "参与者",
    "message_id": "消息",
    "session_id": "会话",
    "turn": "轮次",
    "capacity": "容量",
    "direction": "方向",
    "intensity": "强度",
    "weight": "权重",
}


def extract_fields(message: str) -> dict[str, str]:
    """从消息中正则提取 键=值 对。"""
    fields: dict[str, str] = {}
    for match in _FIELD_PATTERN.finditer(message):
        key = match.group(1)
        value = match.group(2)
        label = _LABEL_MAP.get(key, key)
        fields[label] = value
    return fields


# ── 颜色主题 ────────────────────────────────────────────────────────────────

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"

# 暗色主题（256 色 ANSI palette）
_DARK_THEME: dict[str, str] = {
    "protagonist": "\033[38;5;75m",   # 浅蓝
    "detail": "\033[38;5;246m",       # 灰
    "user": "\033[38;5;214m",         # 橙
    "success": "\033[38;5;78m",       # 绿
    "alter": "\033[38;5;213m",        # 粉
    "memory": "\033[38;5;141m",       # 紫
    "warning": "\033[38;5;220m",      # 黄
    "error": "\033[38;5;196m",        # 红
    "action": "\033[38;5;75m",        # 蓝
    "timestamp": "\033[38;5;246m",    # 灰
    "tree": "\033[38;5;240m",         # 深灰
}

# 亮色主题
_LIGHT_THEME: dict[str, str] = {
    "protagonist": "\033[38;5;26m",
    "detail": "\033[38;5;240m",
    "user": "\033[38;5;166m",
    "success": "\033[38;5;22m",
    "alter": "\033[38;5;163m",
    "memory": "\033[38;5;93m",
    "warning": "\033[38;5;172m",
    "error": "\033[38;5;124m",
    "action": "\033[38;5;26m",
    "timestamp": "\033[38;5;240m",
    "tree": "\033[38;5;245m",
}


# ── LayeredLogFormatter ─────────────────────────────────────────────────────

class LayeredLogFormatter(logging.Formatter):
    """分层日志格式化器：阶段感知 + 动作检测 + 颜文字/符号 + 三档密度 + tree 布局。"""

    # density → 最低级别映射（独立于 logging.level）
    _DENSITY_LEVEL_MAP: dict[str, int] = {
        "summary": logging.WARNING,
        "standard": logging.INFO,
        "diagnostic": logging.DEBUG,
    }

    def __init__(
        self,
        *,
        color: bool = True,
        color_theme: str = "dark",
        kaomoji: bool = True,
        density: str = "standard",
    ) -> None:
        super().__init__()
        self._color = color
        self._kaomoji = kaomoji
        self._density = DensityLevel[density.upper()]
        self._theme = _DARK_THEME if color_theme == "dark" else _LIGHT_THEME

    def format(self, record: logging.LogRecord) -> str:
        """格式化日志记录。"""
        message = record.getMessage()

        # ── 密度过滤 ──
        min_level = self._DENSITY_LEVEL_MAP.get(self._density.value, logging.INFO)
        # diagnostic 模式不过滤任何级别
        if self._density != DensityLevel.DIAGNOSTIC and record.levelno < min_level:
            return ""

        # ── 动作检测 ──
        action = detect_log_action(message)

        # ── 时间戳 ──
        try:
            ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%H:%M:%S")
        except Exception:
            ts = f"{record.created:.0f}"

        # ── 动作标记 ──
        if self._kaomoji:
            action_marker = _KAOMOJI.get(action, "")
        else:
            action_marker = _SYMBOLS.get(action, "")

        # ── 构建输出 ──
        if self._color:
            theme = self._theme
            ts_str = f"{theme['timestamp']}{ts}{_RESET}"
            marker_str = f"{theme['action']}{action_marker}{_RESET}"
            level_str = f"{record.levelname:<8}"

            # 字段树形布局
            fields = extract_fields(message)
            if fields and self._density != DensityLevel.SUMMARY:
                tree_lines = _build_tree(fields, theme)
                main_line = f"{ts_str} {marker_str} {level_str} {message}"
                return f"{main_line}\n{tree_lines}"
            else:
                return f"{ts_str} {marker_str} {level_str} {message}"
        else:
            fields = extract_fields(message)
            if fields and self._density != DensityLevel.SUMMARY:
                tree_lines = _build_tree_plain(fields)
                main_line = f"{ts} {action_marker} {record.levelname:<8} {message}"
                return f"{main_line}\n{tree_lines}"
            else:
                return f"{ts} {action_marker} {record.levelname:<8} {message}"


def _build_tree(fields: dict[str, str], theme: dict[str, str]) -> str:
    """构建 tree 布局的字段输出（带颜色）。"""
    tree = _theme_get(theme, "tree", "\033[38;5;240m")
    lines: list[str] = []
    keys = list(fields.keys())
    for i, key in enumerate(keys):
        is_last = i == len(keys) - 1
        connector = "└─" if is_last else "├─"
        lines.append(f"  {tree}{connector}{_RESET} {key}={fields[key]}")
    return "\n".join(lines)


def _build_tree_plain(fields: dict[str, str]) -> str:
    """构建 tree 布局（无颜色）。"""
    lines: list[str] = []
    keys = list(fields.keys())
    for i, key in enumerate(keys):
        is_last = i == len(keys) - 1
        connector = "└─" if is_last else "├─"
        lines.append(f"  {connector} {key}={fields[key]}")
    return "\n".join(lines)


def _theme_get(theme: dict[str, str], key: str, default: str) -> str:
    return theme.get(key, default)
