"""MemoryQueryTool — 记忆查询工具"""

from __future__ import annotations

from agent.tools.base import ToolBase, ToolContext, ToolResult, ToolPermission


class MemoryQueryTool(ToolBase):
    """从记忆图谱中查询相关信息的工具。

    只读（安全），可与其他只读工具并发执行。
    """

    name = "memory_query"
    description = "从记忆图谱中查询相关信息。当需要回忆用户说过的话、过去的约定、偏好时使用此工具。"
    input_schema: dict = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "要查询的内容关键词",
            },
        },
        "required": ["query"],
    }
    is_concurrency_safe = True
    permission = ToolPermission.ALWAYS_ALLOW

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        query = params["query"]

        if not context.memory_manager:
            return ToolResult(success=False, error="记忆系统不可用")

        try:
            result = await context.memory_manager.query_memory(query)
            if result:
                return ToolResult(success=True, data={"result": str(result)})
            return ToolResult(success=True, data={"result": "未找到相关记忆"})
        except Exception as e:
            return ToolResult(success=False, error=str(e))
