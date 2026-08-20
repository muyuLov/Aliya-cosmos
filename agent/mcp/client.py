"""MCP 客户端：连接 stdio / SSE 服务器，拉取并缓存工具清单。

采用官方 ``mcp`` Python SDK（``ClientSession`` / ``stdio_client`` / ``sse_client``）。
工具调用（``invoke``）每次重建连接执行，避免长连接泄漏与跨事件循环复用问题。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Awaitable, Callable

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client

from core.logger import get_logger

logger = get_logger(__name__)


@dataclass
class McpServerSpec:
    name: str
    transport: str  # "stdio" | "sse"
    command: list[str] | None = None  # stdio: [exe, arg, ...]
    url: str | None = None  # sse: http(s) endpoint
    enabled: bool = True


@dataclass
class McpToolProxy:
    server: str
    name: str
    description: str
    input_schema: dict
    # 调用时经 MCP session 执行（每次调用重建连接）
    invoke: Callable[[dict | None], Awaitable[str]] | None = field(default=None, repr=False)


@asynccontextmanager
async def _session_ctx(spec: McpServerSpec) -> AsyncGenerator[tuple[Any, Any], None]:
    """按传输类型建立 MCP 会话上下文，产出 (read, write) 流。"""
    if spec.transport == "stdio":
        if not spec.command:
            raise ValueError(f"MCP 服务器 {spec.name}: stdio 传输缺少 command")
        params = StdioServerParameters(
            command=spec.command[0], args=list(spec.command[1:])
        )
        async with stdio_client(params) as (read, write):
            yield read, write
    elif spec.transport == "sse":
        if not spec.url:
            raise ValueError(f"MCP 服务器 {spec.name}: sse 传输缺少 url")
        async with sse_client(spec.url) as (read, write):
            yield read, write
    else:
        raise ValueError(
            f"MCP 服务器 {spec.name}: 未知传输类型 {spec.transport!r}"
        )


def _format_call_result(result: Any) -> str:
    """把 MCP call_tool 返回结果格式化为文本。

    mcp 2.x 模型字段为 snake_case：structured_content / is_error。
    """
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(str(text))
    structured = getattr(result, "structured_content", None)
    if structured:
        parts.append(str(structured))
    if not parts and getattr(result, "is_error", False):
        parts.append("[MCP 调用失败]")
    return "\n".join(parts) if parts else str(result)


def _make_invoke(
    spec: McpServerSpec, tool_name: str
) -> Callable[[dict | None], Awaitable[str]]:
    async def invoke(args: dict | None = None) -> str:
        async with _session_ctx(spec) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, args or {})
                return _format_call_result(result)

    return invoke


async def connect_server(spec: McpServerSpec) -> list[McpToolProxy]:
    """连接服务器并拉取工具清单，返回 McpToolProxy 列表。

    连接或握手失败直接抛异常，由 sync_mcp_servers 捕获并隔离。
    """
    async with _session_ctx(spec) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            proxies = [
                McpToolProxy(
                    server=spec.name,
                    name=t.name,
                    description=t.description or "",
                    input_schema=t.input_schema or {},
                    invoke=_make_invoke(spec, t.name),
                )
                for t in tools.tools
            ]
    logger.info("MCP 服务器 %s 拉取 %d 个工具", spec.name, len(proxies))
    return proxies
