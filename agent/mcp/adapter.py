"""MCP 适配器：把远端工具映射为本地 FC 工具。"""

from __future__ import annotations

from agent.mcp.client import McpToolProxy
from agent.tools.base import ToolContext, ToolDefinition, ToolExecutor
from agent.tools.registry import ToolRegistry
from core.logger import get_logger

logger = get_logger(__name__)


def _make_executor(proxy: McpToolProxy) -> ToolExecutor:
    async def _exec(_ctx: ToolContext, args: dict) -> str:
        # proxy.invoke 为 MCP session.call_tool 的封装
        if proxy.invoke is None:
            return "[MCP 未连接]"
        try:
            return await proxy.invoke(args)
        except Exception as e:
            logger.warning("MCP 工具 %s/%s 调用失败: %s", proxy.server, proxy.name, e)
            return f"[MCP 工具调用失败: {e}]"

    return _exec


def register_mcp_server(registry: ToolRegistry, proxies: list[McpToolProxy]) -> None:
    """把一批 MCP 工具代理注册进 registry。"""
    for proxy in proxies:
        if not proxy.invoke:
            continue
        definition = ToolDefinition(
            id=f"mcp__{proxy.server}__{proxy.name}",
            name=f"mcp__{proxy.server}__{proxy.name}",
            description=proxy.description,
            input_schema=proxy.input_schema,
            enabled=True,
            risk="medium",  # 远端工具不可控，标为中等风险
        )
        registry.register(definition, _make_executor(proxy))
