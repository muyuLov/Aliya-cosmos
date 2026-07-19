"""ReplyTool — 文本回复工具"""

from __future__ import annotations

from agent.tools.base import ToolBase, ToolContext, ToolResult, ToolPermission


class ReplyTool(ToolBase):
    """发送文本消息给用户的工具。

    只读（安全），可与其他只读工具并发执行。
    """

    name = "reply"
    description = "向用户发送文本消息。回复用户时必须使用此工具。"
    input_schema: dict = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "回复文本内容",
            },
        },
        "required": ["text"],
    }
    is_concurrency_safe = True
    permission = ToolPermission.ALWAYS_ALLOW

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        text = params["text"]
        if context.send_message:
            await context.send_message({"type": "reply", "reply": text})
        return ToolResult(success=True, data={"text": text})
