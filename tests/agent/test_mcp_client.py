"""测试 agent.mcp.client：连接 MCP 测试服务器并拉取工具、调用工具。"""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("mcp")  # 无 mcp SDK 时整模块跳过

from agent.mcp.client import McpServerSpec, connect_server  # noqa: E402

# 最小 stdio MCP 测试服务器（用 mcp 2.0 内置低层 Server API，不依赖 fastmcp 包）
SERVER_SCRIPT = """\
import asyncio

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server


async def _list_tools(_ctx, _params):
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="add",
                description="两个数相加",
                input_schema={
                    "type": "object",
                    "properties": {
                        "a": {"type": "integer"},
                        "b": {"type": "integer"},
                    },
                    "required": ["a", "b"],
                },
            ),
            types.Tool(
                name="greeting",
                description="问候",
                input_schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            ),
            types.Tool(
                name="weather_json",
                description="返回结构化天气数据",
                input_schema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="always_fail",
                description="始终失败的工具",
                input_schema={"type": "object", "properties": {}},
            ),
        ]
    )


async def _call_tool(_ctx, params):
    args = params.arguments or {}
    if params.name == "add":
        text = str(int(args.get("a", 0)) + int(args.get("b", 0)))
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])
    if params.name == "greeting":
        text = "你好，" + str(args.get("name", ""))
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])
    if params.name == "weather_json":
        return types.CallToolResult(
            content=[],
            structured_content={"city": "上海", "temp": 28},
        )
    if params.name == "always_fail":
        return types.CallToolResult(content=[], is_error=True)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text="未知工具 " + params.name)]
    )


async def main():
    server = Server("test-server", on_list_tools=_list_tools, on_call_tool=_call_tool)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
"""


@pytest.fixture
def stdio_spec(tmp_path) -> McpServerSpec:
    script = tmp_path / "test_mcp_server.py"
    script.write_text(SERVER_SCRIPT, encoding="utf-8")
    return McpServerSpec(
        name="test", transport="stdio", command=[sys.executable, str(script)]
    )


async def test_connect_server_lists_tools(stdio_spec):
    proxies = await connect_server(stdio_spec)
    names = {p.name for p in proxies}
    assert "add" in names
    assert "greeting" in names
    add = next(p for p in proxies if p.name == "add")
    assert add.description
    assert add.input_schema


async def test_invoke_calls_tool(stdio_spec):
    proxies = await connect_server(stdio_spec)
    add = next(p for p in proxies if p.name == "add")
    assert add.invoke is not None
    result = await add.invoke({"a": 1, "b": 2})
    assert result == "3"


async def test_invoke_greeting_text(stdio_spec):
    proxies = await connect_server(stdio_spec)
    greeting = next(p for p in proxies if p.name == "greeting")
    assert greeting.invoke is not None
    result = await greeting.invoke({"name": "Aliya"})
    assert result == "你好，Aliya"


async def test_connect_unknown_transport_raises():
    bad = McpServerSpec(name="bad", transport="tcp", command=["x"])
    with pytest.raises(ValueError, match="未知传输类型"):
        await connect_server(bad)


async def test_invoke_structured_content(stdio_spec):
    """结构化内容（structured_content）应被格式化输出，而非丢失。"""
    proxies = await connect_server(stdio_spec)
    tool = next(p for p in proxies if p.name == "weather_json")
    assert tool.invoke is not None
    result = await tool.invoke({})
    assert "上海" in result
    assert "28" in result


async def test_invoke_error_marker(stdio_spec):
    """is_error=True 且无内容时返回失败标记，而非整个模型字符串。"""
    proxies = await connect_server(stdio_spec)
    tool = next(p for p in proxies if p.name == "always_fail")
    assert tool.invoke is not None
    result = await tool.invoke({})
    assert result == "[MCP 调用失败]"
