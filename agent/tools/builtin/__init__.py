"""内置工具包：统一注册入口"""

from __future__ import annotations

from agent.tools.registry import ToolRegistry
from agent.tools.builtin.memory_tools import register_memory_tools
from agent.tools.builtin.time_tool import register_time_tool


def register_builtin_tools(registry: ToolRegistry) -> None:
    """注册全部内置工具（时间 + 记忆）。"""
    register_time_tool(registry)
    register_memory_tools(registry)


__all__ = ["register_builtin_tools"]
