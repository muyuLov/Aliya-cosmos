"""飞书 webhook 路由：接收消息事件、驱动 AgentSession、处理确认卡片回调。"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request

from agent.channels.feishu_sink import FeishuEventSink
from agent.session import AgentSession, build_agent_session
from core.logger import get_logger

logger = get_logger(__name__)


def create_feishu_router(client: Any, confirm: bool = True) -> APIRouter:
    router = APIRouter()
    sessions: dict[str, AgentSession] = {}

    @router.post("/channels/feishu")
    async def feishu_webhook(req: Request):
        body = await req.json()
        if body.get("type") == "url_verification":
            return {"challenge": body["challenge"]}  # 飞书校验
        event = body.get("event", {})
        msg = event.get("message", {})
        chat_id = msg.get("chat_id") or event.get("open_chat_id")
        text = _parse_message_text(msg)
        if not chat_id or not text:
            return {"code": 0}
        session = sessions.get(chat_id)
        if session is None:
            session = await build_agent_session(chat_id)
            session.add_sink(FeishuEventSink(client, chat_id, confirm=confirm))  # 每会话绑定一次
            sessions[chat_id] = session
        async for _ in session.submit(text):
            pass  # 事件已由 sink 转发到飞书，此处消费以驱动生成
        return {"code": 0}

    @router.post("/channels/feishu/card")
    async def feishu_card_callback(req: Request):
        """飞书交互卡片回调：用户点击确认/拒绝按钮。"""
        body = await req.json()
        action = body.get("action", {})
        value = action.get("value", {})
        call_id = value.get("call_id", "")
        session = sessions.get(value.get("chat_id", ""))
        if session is None or not call_id:
            return {"code": 0}
        await session.loop.resolve_confirmation(call_id, allowed=bool(value.get("allowed", False)))
        return {"code": 0}

    return router


def _parse_message_text(msg: dict) -> str:
    """飞书消息文本位于 message.content（JSON 字符串），格式 {"text": "..."}。"""
    raw = (msg.get("content") or "").strip()
    if not raw:
        return ""
    try:
        return str(json.loads(raw).get("text") or "").strip()
    except (json.JSONDecodeError, TypeError):
        return ""
