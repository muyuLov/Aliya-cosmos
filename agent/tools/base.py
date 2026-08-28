"""工具系统基础类型：ToolDefinition 与 ToolContext"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class ToolDefinition:
    """工具定义（注册表元数据，也用于生成 LLM tools schema）"""

    id: str                      # 工具标识（permission 用）
    name: str                    # 函数名（LLM 调用名）
    description: str             # 描述（进 tools schema）
    input_schema: dict           # JSON Schema（参数）
    enabled: bool = True
    risk: str = "safe"           # "safe" | "medium" | "high"


@dataclass
class ToolContext:
    """工具执行上下文（构造注入给 executor）"""

    user_query: str
    conversation_id: str
    memory: Any = None           # UnifiedMemoryFacade 或 None


ToolExecutor = Callable[[ToolContext, dict[str, Any]], Awaitable[str]]
