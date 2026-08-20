"""企业微信 webhook 路由：接收消息事件、驱动 AgentSession、处理确认回调。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from agent.channels.wechat_sink import WeChatEventSink
from agent.session import AgentSession, build_agent_session
from core.logger import get_logger

logger = get_logger(__name__)


def create_wechat_router(client: Any, confirm: bool = True) -> APIRouter:
    router = APIRouter()
    sessions: dict[str, AgentSession] = {}

    @router.get("/channels/wechat")
    async def wechat_verify(req: Request):
        # URL 校验：返回 echostr（演示模式采用明文，生产应使用 aes_key 解密验签）
        return PlainTextResponse(req.query_params.get("echostr") or "")

    @router.post("/channels/wechat")
    async def wechat_webhook(req: Request):
        body = await req.json()
        # 企业微信文本回调体：{"FromUserName": userid, "Content": "消息内容", ...}
        user_id = str(body.get("FromUserName") or "")
        text = str(body.get("Content") or "").strip()
        if not user_id or not text:
            return PlainTextResponse("success")
        session = sessions.get(user_id)
        if session is None:
            session = await build_agent_session(user_id)
            session.add_sink(WeChatEventSink(client, user_id, confirm=confirm))  # 每会话绑定一次
            sessions[user_id] = session
        async for _ in session.submit(text):
            pass  # 事件已由 sink 转发到企业微信，此处消费以驱动生成
        return PlainTextResponse("success")

    @router.post("/channels/wechat/card")
    async def wechat_card_callback(req: Request):
        """确认卡片回调：用户点击确认/拒绝按钮。"""
        body = await req.json()
        call_id = str(body.get("call_id") or "")
        user_id = str(body.get("user_id") or "")
        session = sessions.get(user_id)
        if session is None or not call_id:
            return {"code": 0}
        await session.loop.resolve_confirmation(call_id, allowed=bool(body.get("allowed", False)))
        return {"code": 0}

    return router
