from __future__ import annotations

import asyncio
from collections.abc import Callable

from agent.models import ToolCall, ToolProgress, ToolResult
from agent.tools.base import BaseTool, ToolCategory, _on_progress_var
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

    async def dispatch(
        self,
        tool_calls: list[ToolCall],
        on_progress: Callable[[ToolProgress], None] | None = None,
    ) -> list[ToolResult]:
        """
        分发执行工具调用。

        根据工具的 concurrency_safe 属性分区：
        - 并发安全（只读）工具并行执行
        - 非并发安全（修改）工具串行执行

        Args:
            tool_calls: 待执行的工具调用列表。
            on_progress: 可选的进度回调，长耗时工具在执行期间通过此回调发射进度事件。
        """
        safe: list[ToolCall] = []
        unsafe: list[ToolCall] = []
        for tc in tool_calls:
            tool = self._tools.get(tc.tool_name)
            if tool is None:
                safe.append(tc)  # TOOL_NOT_FOUND 无副作用，可并行
            elif tool.is_concurrency_safe(tc.arguments):
                safe.append(tc)
            else:
                unsafe.append(tc)

        results: list[ToolResult] = []
        if safe:
            batch = await asyncio.gather(
                *(self._execute_tool_call(call, on_progress) for call in safe),
                return_exceptions=True,
            )
            results.extend(self._finalize_batch(safe, batch))
        if unsafe:
            for call in unsafe:
                result = await self._execute_tool_call(call, on_progress)
                results.append(result)

        return results

    @staticmethod
    def _finalize_batch(
        calls: list[ToolCall], batch: tuple[ToolResult | BaseException, ...]
    ) -> list[ToolResult]:
        final: list[ToolResult] = []
        for call, result in zip(calls, batch):
            if not isinstance(result, BaseException):
                final.append(result)
                continue
            logger.error("工具执行异常：%s: %s", call.tool_name, result,
                         exc_info=(type(result), result, result.__traceback__))
            match result:
                case asyncio.TimeoutError():
                    error_code = "TIMEOUT"
                case KeyError() | AttributeError() | TypeError():
                    error_code = "TOOL_IMPLEMENTATION_ERROR"
                case _:
                    error_code = "INTERNAL_ERROR"
            final.append(ToolResult(
                tool_name=call.tool_name, success=False,
                error="工具执行失败", error_code=error_code,
            ))
        return final

    async def format_prompts(self, context: dict[str, Any] | None = None) -> str:
        """异步生成所有工具的系统提示文档，使用各工具的 prompt() 方法。

        与 format_descriptions()（同步，使用 format_signature()）不同，
        此方法支持子类覆写 prompt() 以提供更丰富的文档。
        """
        parts = []
        for t in self._tools.values():
            parts.append(await t.prompt(context))
        return "\n\n".join(parts)

    def format_descriptions(self, category_filter: set[ToolCategory] | None = None) -> str:
        """生成工具的完整描述文本（含 input_schema），供 LLM 上下文注入。

        Args:
            category_filter: 只包含指定分类的工具。None 表示包含所有工具。
        """
        tools = self._tools.values()
        if category_filter is not None:
            tools = [t for t in tools if t.category in category_filter]
        return "\n\n".join(t.format_signature() for t in tools)

    async def _execute_tool_call(self, call: ToolCall, on_progress: Callable[[ToolProgress], None] | None = None) -> ToolResult:
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

        # 参数类型校验（基于 input_schema）
        err = tool.validate_args(call.arguments)
        if err:
            return ToolResult(
                tool_name=call.tool_name, success=False,
                error=err, error_code="INVALID_ARGS",
            )

        # 语义校验（业务规则）
        err = tool.validate_input(call.arguments)
        if err:
            return ToolResult(
                tool_name=call.tool_name, success=False,
                error=err, error_code="INVALID_INPUT",
            )

        # 权限检查
        err = tool.check_permissions(call.arguments)
        if err:
            return ToolResult(
                tool_name=call.tool_name, success=False,
                error=err, error_code="PERMISSION_DENIED",
            )

        # 通过 contextvar 注入进度回调，避免共享实例属性竞态
        token = _on_progress_var.set(on_progress)
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
        finally:
            _on_progress_var.reset(token)

        return ToolResult(tool_name=call.tool_name, success=True, data=data)
