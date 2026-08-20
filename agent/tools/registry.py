"""工具注册表：注册 / 查询 / 过滤 / 生成 tools schema / 执行"""

from __future__ import annotations

from typing import Any

from agent.tools.base import ToolDefinition, ToolExecutor, ToolContext

_NOT_REGISTERED = "[工具未注册]"


class ToolRegistry:
    """按 id 注册与执行工具，负责生成 OpenAI tools schema。"""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolDefinition, ToolExecutor]] = {}

    def register(self, definition: ToolDefinition, executor: ToolExecutor) -> None:
        self._tools[definition.id] = (definition, executor)

    def get(self, tool_id: str) -> tuple[ToolDefinition, ToolExecutor] | None:
        return self._tools.get(tool_id)

    def enabled_definitions(self) -> list[ToolDefinition]:
        return [d for d, _ in self._tools.values() if d.enabled]

    def build_tools_schema(self) -> list[dict]:
        """转 OpenAI tools 数组：[{"type": "function", "function": {"name", "description", "parameters"}}]"""
        return [
            {
                "type": "function",
                "function": {
                    "name": d.name,
                    "description": d.description,
                    "parameters": d.input_schema,
                },
            }
            for d in self.enabled_definitions()
        ]

    async def execute(self, tool_id: str, ctx: ToolContext, args: dict[str, Any]) -> str:
        """执行工具，返回文本结果；未注册返回错误文案。"""
        entry = self._tools.get(tool_id)
        if not entry:
            return _NOT_REGISTERED
        _, executor = entry
        return await executor(ctx, args)
