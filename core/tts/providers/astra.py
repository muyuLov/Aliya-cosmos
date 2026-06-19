"""AstraTTS 提供商实现"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator

import httpx

from core.logger import get_logger
from core.tts.exceptions import TTSConnectionError, TTSRequestError, TTSSessionError
from core.tts.models import TTSRequest
from core.tts.providers.base import TTSProvider

_logger = get_logger(__name__)

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 0.5  # 指数退避基础延迟（秒）
_ERROR_BODY_LIMIT = 300
_JSON_HEADERS = {"Content-Type": "application/json"}


class AstraTTSProvider(TTSProvider):
    """
    AstraTTS 提供商，通过 HTTP 会话式流式 API 合成语音。

    API 流程：POST /create → GET /{sessionId} → DELETE /{sessionId}

    Args:
        config: 支持 api_url（必填）、default_avatar_id、default_reference_id、
            timeout（默认 60）、chunk_size（默认 4096）。
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)

        # 参数验证（集中校验）
        from core.tts.validation import validate_provider_config

        validate_provider_config("astra", config)

        self.api_url: str = config.get("api_url", "").rstrip("/")
        self.default_avatar_id: str | None = config.get("default_avatar_id")
        self.default_reference_id: str | None = config.get("default_reference_id")
        self._read_chunk_size: int = config.get("chunk_size", 4096)

        self._create_url = f"{self.api_url}/api/tts/stream/create"
        self._stream_base_url = f"{self.api_url}/api/tts/stream"

        # 分离连接超时与读取超时，避免长流式传输被连接超时中断
        # 连接池调优：增大 max_connections 支持更多并发，延长 keepalive 减少重连开销
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=self.timeout, write=10.0, pool=10.0),
            limits=httpx.Limits(
                max_connections=50,
                max_keepalive_connections=25,
                keepalive_expiry=60.0,
            ),
            transport=httpx.AsyncHTTPTransport(http2=False),
        )

    @property
    def provider_name(self) -> str:
        return "astra"

    def _build_body(self, request: TTSRequest) -> bytes:
        """构建请求体，用 is not None 防止空字符串回退到默认值。"""
        avatar_id = request.avatar_id if request.avatar_id is not None else self.default_avatar_id
        reference_id = (
            request.reference_id if request.reference_id is not None else self.default_reference_id
        )
        raw: dict[str, Any] = {
            "text": request.text,
            "avatarId": avatar_id,
            "referenceId": reference_id,
            "speed": request.speed,
            "noiseScale": request.noise_scale,
            "temperature": request.temperature,
            "topK": request.top_k,
            "streamingChunkSize": request.chunk_size,
            "g2PPriorityMode": request.g2p_priority_mode,
            "languages": request.languages,
        }
        return json.dumps(
            {k: v for k, v in raw.items() if v is not None}, ensure_ascii=False
        ).encode()

    async def create_session(self, request: TTSRequest) -> str:
        """创建合成会话，5xx 错误时指数退避重试。"""
        content = self._build_body(request)
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                resp = await self._client.post(
                    self._create_url, content=content, headers=_JSON_HEADERS
                )
                resp.raise_for_status()
                try:
                    return resp.json()["sessionId"]
                except (KeyError, ValueError) as e:
                    raise TTSRequestError(
                        self.provider_name,
                        f"响应格式异常，缺少 sessionId: {resp.text[:_ERROR_BODY_LIMIT]}",
                        cause=e,
                    ) from e
            except TTSRequestError:
                raise
            except httpx.HTTPStatusError as e:
                body_preview = e.response.text[:_ERROR_BODY_LIMIT]
                if e.response.status_code < 500:
                    raise TTSRequestError(
                        self.provider_name,
                        f"创建会话失败，HTTP {e.response.status_code}: {body_preview}",
                        cause=e,
                    ) from e
                _logger.warning(
                    "创建会话 5xx，准备重试",
                    extra={
                        "attempt": attempt + 1,
                        "status": e.response.status_code,
                        "body": body_preview,
                    },
                )
                last_exc = e
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
                _logger.warning(
                    "创建会话异常，准备重试 | attempt=%d | reason=%s",
                    attempt + 1,
                    e,
                )
                last_exc = e

            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(_RETRY_BASE_DELAY * (2**attempt))

        if isinstance(last_exc, httpx.HTTPStatusError):
            raise TTSRequestError(
                self.provider_name,
                f"创建会话失败，已重试 {_MAX_RETRIES} 次: {last_exc}",
                cause=last_exc,
            ) from last_exc
        raise TTSConnectionError(
            self.provider_name,
            f"创建会话失败，已重试 {_MAX_RETRIES} 次: {last_exc}",
            cause=last_exc,
        ) from last_exc

    async def consume_session(self, session_id: str) -> AsyncGenerator[bytes, None]:
        """流式消费音频数据，边接收边 yield。"""
        try:
            async with self._client.stream("GET", f"{self._stream_base_url}/{session_id}") as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=self._read_chunk_size):
                    yield chunk
        except httpx.HTTPStatusError as e:
            # 流式上下文中响应体尚未读取，需显式 aread()
            try:
                body_preview = (await e.response.aread()).decode(errors="replace")[
                    :_ERROR_BODY_LIMIT
                ]
            except (UnicodeDecodeError, httpx.ResponseNotRead) as read_exc:
                _logger.debug("读取错误响应体失败 | reason=%s", read_exc)
                body_preview = "<无法读取响应体>"
            raise TTSSessionError(
                session_id,
                f"消费音频流失败，HTTP {e.response.status_code}: {body_preview}",
                cause=e,
            ) from e
        except TTSSessionError:
            raise
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
            raise TTSSessionError(session_id, f"消费音频流异常: {e}", cause=e) from e

    async def close_session(self, session_id: str) -> None:
        """
        释放会话资源。

        404 状态码表示服务端已自动销毁会话，这是预期行为，记录调试信息。
        其他错误会抛出异常。
        
        注意：如果客户端已关闭（程序退出时），静默忽略，避免在清理阶段产生噪音日志。
        """
        try:
            resp = await self._client.delete(f"{self._stream_base_url}/{session_id}")
            if resp.status_code == 404:
                _logger.debug("会话已被服务端释放 | session_id=%s", session_id)
                return
            resp.raise_for_status()
        except RuntimeError as e:
            # 客户端已关闭（通常发生在程序退出时），静默忽略
            if "client has been closed" in str(e):
                _logger.debug("客户端已关闭，跳过会话释放 | session_id=%s", session_id)
                return
            raise TTSSessionError(session_id, f"释放会话失败: {e}", cause=e) from e
        except httpx.HTTPStatusError as e:
            raise TTSSessionError(
                session_id,
                f"释放会话失败，HTTP {e.response.status_code}",
                cause=e,
            ) from e
        except TTSSessionError:
            raise
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
            raise TTSSessionError(session_id, f"释放会话失败: {e}", cause=e) from e

    async def aclose(self) -> None:
        """关闭 AsyncClient，释放连接池资源。"""
        await self._client.aclose()
