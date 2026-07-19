"""DeepSeek 云端模型提供商"""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI

from core.llm.providers.openai_compatible import OpenAICompatibleProvider


class DeepSeekProvider(OpenAICompatibleProvider):
    """
    DeepSeek 云端模型提供商，通过 OpenAI 兼容接口调用 DeepSeek API。

    Args:
        config: 提供商配置字典，支持以下字段：
            url: API 基础地址，默认 ``https://api.deepseek.com``。
            api_key / key: DeepSeek API 密钥（必填）。
            model: 模型名称，如 ``deepseek-v4-flash``、``deepseek-v4-pro``。
            timeout: 请求超时秒数，默认 600。
            max_retries: SDK 层重试次数，默认 3。
    """

    @property
    def provider_name(self) -> str:
        return "deepseek"

    @property
    def supports_reasoning(self) -> bool:
        """DeepSeek API 始终支持思维链推理 token 计数。"""
        return True

    def _build_client(self, config: dict[str, Any]) -> AsyncOpenAI:
        return AsyncOpenAI(
            api_key=config.get("api_key") or config.get("key", ""),
            base_url=config.get("url", "https://api.deepseek.com"),
            timeout=float(self.timeout),
            max_retries=self.max_retries,
        )
