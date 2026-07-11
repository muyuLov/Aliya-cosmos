"""ReplyTool — 文本回复工具"""

from __future__ import annotations

from agent.tools.base import BaseTool, ToolContext, ToolResult


class ReplyTool:
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

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        text = params["text"]
        if context.send_message:
            await context.send_message({"type": "reply", "reply": text})
        return ToolResult(success=True, data={"text": text})
