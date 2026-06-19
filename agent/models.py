from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ToolCall:
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolResult:
    tool_name: str
    success: bool
    data: Any = None
    error: str | None = None
    error_code: str | None = None  # 结构化错误码，如 "TIMEOUT" / "NOT_FOUND"


@dataclass(slots=True)
class AgentResponse:
    reply_text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


def compute_tool_signature(tool_calls: list[ToolCall], skip_names: frozenset[str] | None = None) -> str:
    """为工具调用列表计算确定性签名，排除指定名称的工具（默认排除 'reply'）。

    对每个工具调用按参数名排序，对外层列表按 (工具名, 参数字符串) 排序，
    确保等价的工具调用集合产生相同签名。
    """
    if skip_names is None:
        skip_names = frozenset({"reply"})
    non_skip = [
        (tc.tool_name, tuple(sorted(tc.arguments.items())))
        for tc in tool_calls
        if tc.tool_name not in skip_names
    ]
    return str(sorted(non_skip))


@dataclass(slots=True)
class Skill:
    name: str
    description: str
    version: str
    instructions: str
    file_path: Path
    enabled: bool = True
    priority: int = 100
    trigger_words: list[str] = field(default_factory=list)
    # 预编译的触发词正则（由 SkillLoader 在加载时生成）
    trigger_patterns: list[re.Pattern] = field(default_factory=list, repr=False, init=False)
