from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import httpx

from agent.cache import format_memory_list
from agent.models import ToolProgress, ToolResult
from agent.tools.base import BaseTool, InternalTool, ToolCategory
from core.logger import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from memory.memory_manager import GRAGMemoryManager


class WebSearchTool(BaseTool):
    """网页搜索工具（调用外部搜索 API）"""

    name = "web_search"
    description = "搜索网页获取实时信息"
    input_schema = {
        "query": {"type": "string", "description": "搜索关键词"},
        "limit": {"type": "integer", "description": "返回结果数量，默认 5"},
    }

    def __init__(self, search_api_url: str = ""):
        self.search_api_url = search_api_url

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = arguments.get("query", "")
        if not query:
            return {"success": False, "error": "查询为空"}
        if not self.search_api_url:
            return {"success": False, "error": "搜索 API 未配置"}

        limit = arguments.get("limit", 5)
        from agent.tools.base import get_progress_callback
        on_progress = get_progress_callback()

        def emit(pt: str, msg: str, progress: float | None = None) -> None:
            if on_progress:
                on_progress(ToolProgress(self.name, pt, msg, progress))

        emit("searching", f"正在搜索: {query[:60]}")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(self.search_api_url, params={"q": query, "limit": limit})
                response.raise_for_status()
                results = response.json()
                emit("completed", f"搜索完成, 获得 {len(results)} 条结果", 1.0)
                return {"success": True, "results": results}
        except httpx.HTTPError as e:
            return {"success": False, "error": f"搜索失败：{e}"}
        except Exception as e:
            return {"success": False, "error": str(e)}


class MemoryQueryTool(InternalTool):
    """记忆查询工具（结果注入对话，让 LLM 继续推理）"""

    name = "memory_query"
    description = "从 GRAG 记忆系统检索相关信息"
    message_prefix = "【记忆查询结果】"
    category = ToolCategory.INTERNAL
    input_schema = {
        "query": {"type": "string", "description": "查询内容"},
        "limit": {"type": "integer", "description": "返回结果数量（1-20）", "default": 5},
    }

    def __init__(self, memory_manager: GRAGMemoryManager):
        self.memory_manager = memory_manager

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        query = arguments.get("query", "")
        if not query:
            return ToolResult(
                tool_name=self.name, success=False,
                error="查询为空", error_code="EMPTY_QUERY",
            )

        try:
            limit = max(1, min(20, int(arguments.get("limit", 5))))
        except (ValueError, TypeError):
            limit = 5

        try:
            memories = await self.memory_manager.get_relevant_memories(query, limit=limit)
            return ToolResult(
                tool_name=self.name, success=True,
                data={"memories": memories, "count": len(memories)},
            )
        except Exception as e:
            logger.warning("MemoryQueryTool 执行失败: %s", e)
            return ToolResult(
                tool_name=self.name, success=False,
                error="记忆系统暂时不可用", error_code="MEMORY_QUERY_FAILED",
            )

    async def execute_and_format(self, arguments: dict[str, Any]) -> str:
        """执行查询并格式化为可注入对话的文本。"""
        result = await self.run(arguments)
        if not result.success:
            return f"{self.message_prefix}查询失败: {result.error}"
        if not result.data or not result.data.get("memories"):
            return f"{self.message_prefix}未找到相关记忆"
        return format_memory_list(
            result.data["memories"],
            empty_text=f"{self.message_prefix}未找到相关记忆",
            prefix=self.message_prefix,
        )
