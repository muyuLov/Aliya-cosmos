"""企业微信事件消费者：实现 EventSink，把 AgentEvent 转发到企业微信会话。"""

from __future__ import annotations

from typing import Any

from agent.events import (
    CONFIRM_REQUEST,
    AgentEvent,
    EventSink,
    ProtocolEvent,
    TextMessageDelta,
    TextMessageEnd,
)


class WeChatEventSink(EventSink):
    """把 AgentSession 事件流转发到指定企业微信用户：文本聚合后发送，确认请求发交互卡片。"""

    def __init__(self, client: Any, user_id: str, confirm: bool = True) -> None:
        self._client = client
        self._user_id = user_id
        self._confirm = confirm
        self._buf = ""

    async def emit(self, event: AgentEvent | ProtocolEvent) -> None:
        if self._confirm and isinstance(event, ProtocolEvent) and event.type == CONFIRM_REQUEST:
            await self._client.send_card(self._user_id, _confirm_card(event.payload, self._user_id))
            return
        if isinstance(event, TextMessageDelta):
            self._buf += event.text  # 累积，结束再发（企业微信非流式，避免刷屏）
        elif isinstance(event, TextMessageEnd):
            full = event.full_text or self._buf
            if full.strip():
                await self._client.send_text(self._user_id, full)
            self._buf = ""
        # 其他事件（RunStarted/StepStarted/TOOL_CALL_* 等）忽略


def _confirm_card(payload: dict, user_id: str) -> dict:
    """构造确认卡片：call_id + user_id 编码进按钮 key（供 /card 回调解出）。"""
    call_id = payload.get("call_id", "")
    return {
        "card_type": "button_interaction",
        "main_title": {"title": "工具授权确认"},
        "sub_title_text": f"是否允许执行：{payload.get('tool', '')}",
        "task_id": str(call_id),
        "button_list": [
            {"text": "确认", "style": 1, "key": f"allow:{user_id}:{call_id}"},
            {"text": "拒绝", "style": 2, "key": f"deny:{user_id}:{call_id}"},
        ],
    }
