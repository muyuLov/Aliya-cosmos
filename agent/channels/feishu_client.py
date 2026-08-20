"""飞书开放平台客户端：token 获取 + 消息发送 + 事件校验。"""

from __future__ import annotations

import json

import httpx

from core.logger import get_logger

logger = get_logger(__name__)

_FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
_FEISHU_SEND_URL = "https://open.feishu.cn/open-apis/im/v1/messages"


class FeishuClient:
    """封装飞书开放 API：换 tenant_access_token、发送文本/交互卡片。"""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
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
            resp = await client.post(
                _FEISHU_TOKEN_URL,
                json={"app_id": self._app_id, "app_secret": self._app_secret},
            )
            data = resp.json()
            token = str(data.get("tenant_access_token") or "")
            self._token = token
            return token

    async def send_text(self, chat_id: str, text: str) -> None:
        token = await self._ensure_token()
        async with self._new_client() as client:
            resp = await client.post(
                f"{_FEISHU_SEND_URL}?receive_id_type=chat_id",
                json={"receive_id": chat_id, "msg_type": "text", "content": _text_content(text)},
                headers={"Authorization": f"Bearer {token}"},
            )
            body = resp.json()
            if resp.status_code != 200 or body.get("code") != 0:
                logger.warning("飞书消息发送失败: %s", resp.text)

    async def send_card(self, chat_id: str, card: dict) -> None:
        """发送交互卡片（确认请求用，含确认/拒绝按钮）。"""
        token = await self._ensure_token()
        async with self._new_client() as client:
            resp = await client.post(
                f"{_FEISHU_SEND_URL}?receive_id_type=chat_id",
                json={"receive_id": chat_id, "msg_type": "interactive", "content": _card_content(card)},
                headers={"Authorization": f"Bearer {token}"},
            )
            body = resp.json()
            if resp.status_code != 200 or body.get("code") != 0:
                logger.warning("飞书卡片发送失败: %s", resp.text)


# 飞书 API 约束：content 必须是 JSON 字符串（非 dict），故需 json.dumps
def _text_content(text: str) -> str:
    return json.dumps({"text": text}, ensure_ascii=False)


def _card_content(card: dict) -> str:
    return json.dumps(card, ensure_ascii=False)
