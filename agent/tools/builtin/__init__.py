"""内置工具包：统一注册入口"""

from __future__ import annotations

from agent.skills.loader import load_skills
from agent.tools.builtin.memory_tools import register_memory_tools
from agent.tools.builtin.time_tool import register_time_tool
from agent.tools.rag import search_knowledge, search_knowledge_def
from agent.tools.registry import ToolRegistry


def register_builtin_tools(registry: ToolRegistry) -> None:
    """注册全部内置工具（时间 + 记忆 + RAG 检索 + Skill）。"""
    register_time_tool(registry)
    register_memory_tools(registry)
    registry.register(search_knowledge_def, search_knowledge)
    load_skills(registry)


__all__ = ["register_builtin_tools", "search_knowledge_def"]
