"""内置工具：获取当前时间"""

from __future__ import annotations

from datetime import datetime

from agent.tools.base import ToolContext, ToolDefinition

_DEFINITION = ToolDefinition(
    id="get_current_time",
    name="get_current_time",
    description="获取当前日期与时间（含本地时区）",
    input_schema={"type": "object", "properties": {}},
    risk="safe",
)


async def get_current_time(_ctx: ToolContext, _args: dict) -> str:
    """返回当前本地时间，含时区。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z")


def register_time_tool(registry) -> None:
    """将时间工具注册进注册表。"""
    registry.register(_DEFINITION, get_current_time)
