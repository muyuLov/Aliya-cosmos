"""GetCurrentTimeTool — 获取当前日期与时间工具"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from agent.tools.base import ToolBase, ToolPermission, ToolResult

if TYPE_CHECKING:
    from agent.context import AgentContext


class GetCurrentTimeTool(ToolBase):
    """返回当前日期与时间（只读，无外部依赖）。

    用户询问"现在几点""今天星期几"等时间类问题时使用。
    """

    name = "get_current_time"
    description = (
        "获取当前日期和时间。当用户询问现在几点、今天是几号、星期几等时间信息时使用此工具。"
    )
    input_schema: dict = {
        "type": "object",
        "properties": {},
        "required": [],
    }
    is_concurrency_safe = True
    permission = ToolPermission.ALWAYS_ALLOW

    async def execute(self, params: dict, context: "AgentContext") -> ToolResult:
        # 参数名须与 BaseTool Protocol 一致（basedpyright Protocol 匹配要求）
        _ = (params, context)
        now = datetime.now()
        data = {
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "weekday": "星期" + "一二三四五六日"[now.weekday()],
        }
        text = f"当前时间：{data['datetime']}（{data['weekday']}）"
        return ToolResult(success=True, data={"text": text, **data})
