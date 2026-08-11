"""QueryRecentConversationTool — 查询最近对话历史工具"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.tools.base import ToolBase, ToolPermission, ToolResult

if TYPE_CHECKING:
    from agent.context import AgentContext

# 注入消息的元数据前缀标记（与 stages/think.py 的 TOOL_RESULT_MARKER 对应）
_INJECTED_PREFIX = "tool_result"

# 角色名 → 显示标签映射
_ROLE_LABELS: dict[str, str] = {"user": "用户", "assistant": "Aliya", "system": "系统"}


class QueryRecentConversationTool(ToolBase):
    """返回最近的对话历史（只读）。

    通过统一依赖容器直接访问 ConversationService，
    当需要回顾用户最近说过的话、找回话题上下文时使用。
    """

    name = "query_recent_conversation"
    description = (
        "查询最近的对话历史记录。当需要回顾用户最近说过的话、"
        "忘记当前话题上下文或想了解之前聊了什么时使用此工具。"
    )
    input_schema: dict = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "返回最近多少条消息（默认 10，最大 20）",
            },
        },
        "required": [],
    }
    is_concurrency_safe = True
    permission = ToolPermission.ALWAYS_ALLOW

    async def execute(self, params: dict, context: "AgentContext") -> ToolResult:
        try:
            raw_limit = int(params.get("limit", 10) or 10)
        except (TypeError, ValueError):
            raw_limit = 10
        limit = max(1, min(raw_limit, 20))

        try:
            history = await context.conv.get_history()
        except Exception as e:
            return ToolResult(success=False, error=f"对话历史不可用: {e}")

        # 过滤工具执行结果注入消息，避免污染对话回顾
        visible = [
            m for m in history
            if not (m.metadata and m.metadata.get("prefix") == _INJECTED_PREFIX)
        ]
        if not visible:
            return ToolResult(success=True, data={"result": "暂无对话记录"})

        lines: list[str] = []
        for msg in visible[-limit:]:
            role = _ROLE_LABELS.get(msg.role, "系统")
            content = msg.content.replace("\n", " ").strip()
            lines.append(f"{role}: {content}")
        return ToolResult(success=True, data={"result": "\n".join(lines)})
