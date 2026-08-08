"""工具模块

- ``create_default_tool_registry``：默认工具注册工厂（内置工具统一在此注册）
- ``ToolRegistry``：工具注册、描述格式化与分区并发调度
- ``ToolBase`` / ``ToolResult`` 等：工具基础类型
"""

from __future__ import annotations

from agent.tools.base import BaseTool, ToolBase, ToolPermission, ToolResult
from agent.tools.get_current_time import GetCurrentTimeTool
from agent.tools.memory_query import MemoryQueryTool
from agent.tools.query_recent_conversation import QueryRecentConversationTool
from agent.tools.registry import ToolRegistry, partition_tool_calls


def create_default_tool_registry() -> ToolRegistry:
    """创建并注册全部内置工具。

    新增内置工具时在此登记，运行入口（ws.py / main.py）无需再改动。
    """
    registry = ToolRegistry()
    registry.register(MemoryQueryTool())
    registry.register(GetCurrentTimeTool())
    registry.register(QueryRecentConversationTool())
    return registry


__all__ = [
    "BaseTool",
    "ToolBase",
    "ToolPermission",
    "ToolResult",
    "ToolRegistry",
    "partition_tool_calls",
    "MemoryQueryTool",
    "GetCurrentTimeTool",
    "QueryRecentConversationTool",
    "create_default_tool_registry",
]
