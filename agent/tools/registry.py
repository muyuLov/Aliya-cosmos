"""ToolRegistry — 工具注册、描述格式化与并行调度"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from core.logger import get_logger
from agent.tools.base import BaseTool, ToolContext, ToolResult

logger = get_logger(__name__)


@dataclass
class ToolRegistry:
    """工具注册表，描述缓存化，dispatch 按原始顺序返回结果"""

    _tools: dict[str, BaseTool] = field(default_factory=dict)
    _desc_cache: str = ""

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool
        self._desc_cache = ""
        logger.debug("注册工具: %s", tool.name)

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list(self) -> list[BaseTool]:
        return list(self._tools.values())

    def format_descriptions(self) -> str:
        if self._desc_cache:
            return self._desc_cache
        if not self._tools:
            return ""

        parts: list[str] = []
        for tool in self._tools.values():
            params = tool.input_schema.get("properties", {})
            required = tool.input_schema.get("required", [])
            param_lines: list[str] = []
            for pname, pinfo in params.items():
                req = "（必填）" if pname in required else "（可选）"
                ptype = pinfo.get("type", "string")
                pdesc = pinfo.get("description", "")
                param_lines.append(f"    {pname} ({ptype}){req}: {pdesc}")

            if param_lines:
                parts.append(f"### {tool.name}\n{tool.description}\n参数：\n" + "\n".join(param_lines))
            else:
                parts.append(f"### {tool.name}\n{tool.description}")

        self._desc_cache = "\n\n".join(parts)
        return self._desc_cache

    def format_tool_summary(self, results: list[tuple[str, ToolResult]]) -> str:
        lines: list[str] = []
        for name, result in results:
            status = "成功" if result.success else "失败"
            detail = result.data if result.success else result.error
            if detail:
                lines.append(f"工具 `{name}` 执行{status}：{detail}")
            else:
                lines.append(f"工具 `{name}` 执行{status}")
        return "\n".join(lines)

    async def dispatch_all(
        self,
        tool_calls: list[dict],
        context: ToolContext,
    ) -> list[tuple[str, ToolResult]]:
        async def _run_one(call: dict) -> tuple[str, ToolResult]:
            name = call.get("name", "")
            params = call.get("params", {})

            if name not in self._tools:
                logger.warning("未知工具: %s", name)
                return name, ToolResult(success=False, error=f"未知工具: {name}")

            tool = self._tools[name]

            if context.send_message:
                await context.send_message({
                    "type": "tool_start",
                    "tool": name,
                })

            try:
                result = await tool.execute(params, context)
            except Exception as e:
                logger.error("工具执行异常: %s | error=%s", name, e)
                result = ToolResult(success=False, error=str(e))

            if context.send_message:
                await context.send_message({
                    "type": "tool_complete",
                    "tool": name,
                    "status": "success" if result.success else "fail",
                    "error": result.error,
                })

            return name, result

        if not tool_calls:
            return []

        tasks = [_run_one(call) for call in tool_calls]
        results = list(await asyncio.gather(*tasks))

        success_count = sum(1 for _, r in results if r.success)
        fail_count = sum(1 for _, r in results if not r.success)
        if context.send_message:
            await context.send_message({
                "type": "tool_summary",
                "total": len(tool_calls),
                "success": success_count,
                "fail": fail_count,
            })

        return results
