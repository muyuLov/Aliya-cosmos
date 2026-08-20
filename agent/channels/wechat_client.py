"""企业微信客户端：access_token 获取 + 消息发送。"""

from __future__ import annotations

import httpx

from core.logger import get_logger

logger = get_logger(__name__)

_WECHAT_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
_WECHAT_SEND_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"


class WeChatClient:
    """封装企业微信应用 API：换 access_token、发送文本/模板卡片。"""

    def __init__(
        self,
        corp_id: str,
        secret: str,
        agent_id: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._corp_id = corp_id
        self._secret = secret
        self._agent_id = agent_id
        self._transport = transport  # 可注入 MockTransport 供测试
        self._token: str | None = None

    def _new_client(self) -> httpx.AsyncClient:
        if self._transport is not None:
            return httpx.AsyncClient(transport=self._transport)
        return httpx.AsyncClient()

    async def _ensure_token(self) -> str:
        if self._token:
            return self._token
        async with self._new_client() as client:
            resp = await client.get(
                _WECHAT_TOKEN_URL,
                params={"corpid": self._corp_id, "corpsecret": self._secret},
            )
            data = resp.json()
            token = str(data.get("access_token") or "")
            self._token = token
            return token

    async def send_text(self, user_id: str, text: str) -> None:
        token = await self._ensure_token()
        async with self._new_client() as client:
            resp = await client.post(
                _WECHAT_SEND_URL,
                params={"access_token": token},
                json={
                    "touser": user_id,
                    "msgtype": "text",
                    "agentid": self._agent_id,
                    "text": {"content": text},
                },
            )
            data = resp.json()
            if data.get("errcode") != 0:
                logger.warning("企业微信消息发送失败: %s", resp.text)

    async def send_card(self, user_id: str, card: dict) -> None:
        """发送交互卡片（确认请求用，含确认/拒绝按钮）。"""
        token = await self._ensure_token()
        async with self._new_client() as client:
            resp = await client.post(
                _WECHAT_SEND_URL,
                params={"access_token": token},
                json={
                    "touser": user_id,
                    "msgtype": "template_card",
                    "agentid": self._agent_id,
                    "template_card": card,
                },
            )
            data = resp.json()
            if data.get("errcode") != 0:
                logger.warning("企业微信卡片发送失败: %s", resp.text)
