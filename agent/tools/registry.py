from __future__ import annotations

import asyncio

from agent.models import ToolCall, ToolResult
from agent.tools.base import BaseTool
from core.logger import get_logger


logger = get_logger(__name__)


class ToolRegistry:
    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._timeout_seconds = timeout_seconds

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, tool_name: str) -> BaseTool | None:
        return self._tools.get(tool_name)

    def list_tools(self) -> list[BaseTool]:
        return list(self._tools.values())

    async def dispatch(self, tool_calls: list[ToolCall]) -> list[ToolResult]:
        """
        并行分发执行多个工具调用。
        注意：InternalTool 子类不会通过此方法执行，它们在 BrainEngine 内部处理。
        """
        results = await asyncio.gather(
            *(self._execute_tool_call(call) for call in tool_calls),
            return_exceptions=True,
        )
        final: list[ToolResult] = []
        for call, result in zip(tool_calls, results):
            if isinstance(result, BaseException):
                logger.exception("工具执行异常：%s", call.tool_name)
                error_code = "INTERNAL_ERROR"
                if isinstance(result, asyncio.TimeoutError):
                    error_code = "TIMEOUT"
                elif isinstance(result, (KeyError, AttributeError, TypeError)):
                    error_code = "TOOL_IMPLEMENTATION_ERROR"
                final.append(
                    ToolResult(
                        tool_name=call.tool_name,
                        success=False,
                        error="工具执行失败",
                        error_code=error_code,
                    )
                )
            elif isinstance(result, ToolResult):
                final.append(result)
            else:
                final.append(result)
        return final

    def format_descriptions(self) -> str:
        """生成所有工具的完整描述文本（含 input_schema），供 LLM 上下文注入。

        工具之间使用双换行分隔以提高可读性。
        """
        return "\n\n".join(t.format_signature() for t in self._tools.values())

    async def _execute_tool_call(self, call: ToolCall) -> ToolResult:
        """
        执行单个工具调用。
        注意：InternalTool 子类不会注册到 ToolRegistry，因此不会通过此方法执行。
        """
        tool = self._tools.get(call.tool_name)
        if tool is None:
            return ToolResult(
                tool_name=call.tool_name, success=False,
                error=f"tool not found: {call.tool_name}",
                error_code="TOOL_NOT_FOUND",
            )

        # 参数预校验（基于 input_schema）
        err = tool.validate_args(call.arguments)
        if err:
            return ToolResult(
                tool_name=call.tool_name, success=False,
                error=err, error_code="INVALID_ARGS",
            )

        try:
            if self._timeout_seconds > 0:
                data = await asyncio.wait_for(tool.run(call.arguments), timeout=self._timeout_seconds)
            else:
                data = await tool.run(call.arguments)
        except asyncio.TimeoutError:
            return ToolResult(
                tool_name=call.tool_name, success=False,
                error=f"tool timeout ({self._timeout_seconds}s)",
                error_code="TIMEOUT",
            )
        except Exception as exc:
            return ToolResult(
                tool_name=call.tool_name, success=False,
                error=str(exc), error_code="EXECUTION_ERROR",
            )

        return ToolResult(tool_name=call.tool_name, success=True, data=data)
