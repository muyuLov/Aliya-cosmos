"""测试 agent.mcp.adapter：把远端工具映射为本地 FC 工具。"""

from __future__ import annotations

from typing import Any

from agent.mcp.adapter import register_mcp_server
from agent.mcp.client import McpToolProxy
from agent.tools.base import ToolContext
from agent.tools.registry import ToolRegistry

_SENTINEL: Any = object()


def _proxy(name: str = "echo", invoke: Any = _SENTINEL) -> McpToolProxy:
    async def _echo(args):
        return f"echo:{args.get('text', '')}"

    return McpToolProxy(
        server="demo",
        name=name,
        description="回声工具",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
        },
        invoke=_echo if invoke is _SENTINEL else invoke,
    )


def test_register_mcp_server_schema():
    reg = ToolRegistry()
    register_mcp_server(reg, [_proxy()])
    names = {d.name for d in reg.enabled_definitions()}
    assert "mcp__demo__echo" in names


async def test_register_mcp_server_execute():
    reg = ToolRegistry()
    register_mcp_server(reg, [_proxy()])
    result = await reg.execute(
        "mcp__demo__echo", ToolContext("hi", "c1"), {"text": "hi"}
    )
    assert result == "echo:hi"


async def test_invoke_failure_safe_text():
    async def broken(_args):
        raise RuntimeError("boom")

    reg = ToolRegistry()
    register_mcp_server(reg, [_proxy(invoke=broken)])
    result = await reg.execute(
        "mcp__demo__echo", ToolContext("hi", "c1"), {"text": "hi"}
    )
    assert "[MCP 工具调用失败" in result


def test_proxy_without_invoke_skipped():
    reg = ToolRegistry()
    register_mcp_server(reg, [_proxy(invoke=None)])
    assert "mcp__demo__echo" not in {d.name for d in reg.enabled_definitions()}
