"""ToolRegistry — 工具注册、描述格式化与分区并发调度

参考 Claude Code 的设计模式：
1. **分区执行**：只读工具（concurrency_safe）批次内并发，写入工具串行
2. **错误级联**：写入工具失败时取消同批次兄弟工具
3. **权限校验**：执行前调用 check_permissions 钩子
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from core.logger import get_logger
from agent.tools.base import BaseTool, ToolResult

if TYPE_CHECKING:
    from agent.context import AgentContext

logger = get_logger(__name__)

# 只读工具最大并发数
_MAX_READ_CONCURRENCY = 10


@dataclass
class ToolCallSpec:
    """一次工具调用的规格说明"""
    name: str
    params: dict


def partition_tool_calls(
    tool_calls: list[dict],
    tools: dict[str, BaseTool],
) -> list[list[ToolCallSpec]]:
    """将工具调用分区为多个批次。

    分区规则：
    - 安全（只读）工具可多个并发执行
    - 非安全（写入）工具必须单独一批串行执行
    - 当出现混合时，将尽可能多的安全工具归入同一批

    Returns:
        批次列表，每个批次内的 tools 可以并发执行；
        批次间必须串行执行。
    """
    if not tool_calls:
        return []

    # 将调用规格化
    specs: list[ToolCallSpec] = []
    for c in tool_calls:
        name = c.get("name", "")
        params = c.get("params", {})
        specs.append(ToolCallSpec(name=name, params=params))

    if not specs:
        return []

    batches: list[list[ToolCallSpec]] = []
    pending: list[ToolCallSpec] = []

    for spec in specs:
        tool = tools.get(spec.name)
        is_safe = tool.is_concurrency_safe if tool else False

        if is_safe:
            # 安全工具放入当前待办池
            pending.append(spec)
        else:
            # 遇到不安全的工具：先把待办池收为一批（如果有），再单独一批给不安全工具
            if pending:
                batches.append(pending)
                pending = []
            batches.append([spec])

    if pending:
        batches.append(pending)

    return batches


@dataclass
class ToolRegistry:
    """工具注册表，描述缓存化，dispatch 按分区策略返回结果"""

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
            concurrency_tag = "🟢 只读（可并发）" if tool.is_concurrency_safe else "🔴 写入（串行）"
            perm_tag = tool.permission.value
            param_lines: list[str] = []
            for pname, pinfo in params.items():
                req = "（必填）" if pname in required else "（可选）"
                ptype = pinfo.get("type", "string")
                pdesc = pinfo.get("description", "")
                param_lines.append(f"    {pname} ({ptype}){req}: {pdesc}")
            header = f"### {tool.name} [{concurrency_tag}]"
            if param_lines:
                parts.append(f"{header}\n{tool.description}\n参数：\n" + "\n".join(param_lines))
            else:
                parts.append(f"{header}\n{tool.description}")
            parts.append(f"  权限: {perm_tag}")

        self._desc_cache = "\n\n".join(parts)
        return self._desc_cache

    def format_tool_summary(self, results: list[tuple[str, ToolResult]]) -> str:
        lines: list[str] = []
        for name, result in results:
            status = "成功" if result.success else "失败"
            detail = result.data if result.success else result.error
            duration = result.duration
            time_str = f" ({duration:.1f}s)" if duration > 0 else ""
            if detail:
                lines.append(f"工具 `{name}` 执行{status}{time_str}：{detail}")
            else:
                lines.append(f"工具 `{name}` 执行{status}{time_str}")
        return "\n".join(lines)

    async def dispatch_all(
        self,
        tool_calls: list[dict],
        context: "AgentContext",
    ) -> list[tuple[str, ToolResult]]:
        """分区并行调度工具，按原始顺序返回结果。"""
        if not tool_calls:
            return []

        batches = partition_tool_calls(tool_calls, self._tools)
        logger.debug(
            "工具调度 | calls=%d | batches=%d",
            len(tool_calls), len(batches),
        )

        results: list[tuple[str, ToolResult]] = []

        for batch_idx, batch in enumerate(batches):
            logger.debug("执行批次 %d/%d | tools=%s", batch_idx + 1, len(batches),
                         [s.name for s in batch])

            batch_results = await self._execute_batch(batch, context)

            # 检查是否有写入工具失败需要级联取消
            for name, result in batch_results:
                if not result.success and not self._is_safe(name):
                    # 写入工具失败 → 取消当前批次及后续所有批次
                    remaining = len(batches) - batch_idx - 1
                    if remaining > 0:
                        logger.warning(
                            "写入工具 %s 失败，取消后续 %d 批次 | error=%s",
                            name, remaining, result.error,
                        )
                    # 为尚未执行的工具填充取消标记
                    for later_batch in batches[batch_idx + 1:]:
                        for later_spec in later_batch:
                            results.append((
                                later_spec.name,
                                ToolResult(
                                    success=False,
                                    error=f"前置工具 `{name}` 失败，已取消",
                                ),
                            ))
                    results.extend(batch_results)
                    return results

            results.extend(batch_results)

        return results

    async def _execute_batch(
        self,
        batch: list[ToolCallSpec],
        context: "AgentContext",
    ) -> list[tuple[str, ToolResult]]:
        """执行一批（可并发的）工具调用。"""
        async def _run_one(spec: ToolCallSpec) -> tuple[str, ToolResult]:
            name = spec.name
            params = spec.params
            start = time.monotonic()

            if name not in self._tools:
                logger.warning("未知工具: %s", name)
                return name, ToolResult(success=False, error=f"未知工具: {name}")

            tool = self._tools[name]

            # 权限校验（BaseTool 协议保证 check_permissions 存在）
            allowed, reason = await tool.check_permissions(params, context)
            if not allowed:
                return name, ToolResult(
                    success=False, error=f"权限拒绝: {reason}",
                )

            if context.notify:
                await context.notify({
                    "type": "tool_start",
                    "tool": name,
                    "is_concurrency_safe": tool.is_concurrency_safe,
                })

            try:
                result = await tool.execute(params, context)
            except Exception as e:
                logger.error("工具执行异常: %s | error=%s", name, e)
                result = ToolResult(success=False, error=str(e))

            result.duration = time.monotonic() - start

            if context.notify:
                await context.notify({
                    "type": "tool_complete",
                    "tool": name,
                    "status": "success" if result.success else "fail",
                    "error": result.error,
                    "duration": result.duration,
                })

            return name, result

        # 批次内的工具并发执行
        tasks = [_run_one(spec) for spec in batch]
        return list(await asyncio.gather(*tasks))

    def _is_safe(self, name: str) -> bool:
        tool = self._tools.get(name)
        return tool.is_concurrency_safe if tool else False
