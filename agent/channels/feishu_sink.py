"""飞书事件消费者：实现 EventSink，把 AgentEvent 转发到飞书会话。"""

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


class FeishuEventSink(EventSink):
    """把 AgentSession 事件流转发到指定飞书聊天：文本聚合后发送，确认请求发交互卡片。"""

    def __init__(self, client: Any, chat_id: str, confirm: bool = True) -> None:
        self._client = client
        self._chat_id = chat_id
        self._confirm = confirm
        self._buf = ""

    async def emit(self, event: AgentEvent | ProtocolEvent) -> None:
        # CONFIRM_REQUEST 是 ProtocolEvent（loop.py 生成），需优先判定
        if self._confirm and isinstance(event, ProtocolEvent) and event.type == CONFIRM_REQUEST:
            await self._client.send_card(self._chat_id, _confirm_card(event.payload, self._chat_id))
            return
        if isinstance(event, TextMessageDelta):
            self._buf += event.text  # 累积，结束再发（飞书非流式，避免刷屏）
        elif isinstance(event, TextMessageEnd):
            full = event.full_text or self._buf
            if full.strip():
                await self._client.send_text(self._chat_id, full)
            self._buf = ""
        # 其他事件（RunStarted/StepStarted/TOOL_CALL_* 等）忽略


def _confirm_card(payload: dict, chat_id: str) -> dict:
    """构造确认卡片，把 call_id + chat_id 编码进交互按钮 value（供 /card 回调解出）。"""
    return {
        "type": "template_card",
        "data": {
            "template_card": {
                "card_type": "button_interaction",
                "main": {
                    "title": "工具授权确认",
                    "sub_title": f"是否允许执行：{payload.get('tool', '')}",
                },
                "action": {
                    "button_list": [
                        {
                            "text": "确认",
                            "value": {"call_id": payload.get("call_id"), "chat_id": chat_id, "allowed": True},
                        },
                        {
                            "text": "拒绝",
                            "value": {"call_id": payload.get("call_id"), "chat_id": chat_id, "allowed": False},
                        },
                    ]
                },
            }
        },
    }
