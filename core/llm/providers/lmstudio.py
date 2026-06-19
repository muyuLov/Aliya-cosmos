"""LM Studio 本地模型提供商（OpenAI 兼容接口）"""

from __future__ import annotations

from typing import Any

import httpx
from openai import AsyncOpenAI

from core.llm.providers.openai_compatible import OpenAICompatibleProvider


class LMStudioProvider(OpenAICompatibleProvider):
    """
    LM Studio 本地模型提供商，通过 OpenAI 兼容接口调用 LM Studio API。

    LM Studio 不支持 HTTP/2，通过自定义 httpx 传输层显式禁用，
    否则 httpx 0.28+ 默认 HTTP/2 会导致 502。

    Args:
        config: 提供商配置字典，支持以下字段：
            url: LM Studio 服务地址，默认 ``http://127.0.0.1:1234``。
            api_key / key: API 密钥，本地无需认证，默认 ``lm-studio``。
            model: 模型名称，如 ``deepseek-r1-0528-qwen3-8b@q4_k_m``。
            timeout: 请求超时秒数，默认 600。
            max_retries: SDK 层重试次数，默认 3。
    """

    @property
    def provider_name(self) -> str:
        return "lmstudio"

    def _build_client(self, config: dict[str, Any]) -> AsyncOpenAI:
        http_client = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(http2=False)
        )
        return AsyncOpenAI(
            api_key=config.get("api_key") or config.get("key", "lm-studio"),
            base_url=f"{config.get('url', 'http://127.0.0.1:1234').rstrip('/')}/v1",
            timeout=float(self.timeout),
            max_retries=self.max_retries,
            http_client=http_client,
        )
