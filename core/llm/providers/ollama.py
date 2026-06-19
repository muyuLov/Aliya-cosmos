"""Ollama 本地模型提供商（OpenAI 兼容接口）"""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI

from core.llm.providers.openai_compatible import OpenAICompatibleProvider


class OllamaProvider(OpenAICompatibleProvider):
    """
    Ollama 本地模型提供商，通过 OpenAI 兼容接口（/v1/chat/completions）调用。

    Ollama 自 v0.1.x 起提供完整的 OpenAI 兼容层，支持：
    - 流式输出（stream + stream_options.include_usage）
    - thinking 模型推理控制（reasoning_effort）
    - 工具调用、JSON mode、视觉输入等

    Args:
        config: 提供商配置字典，支持以下字段：
            url: Ollama 服务地址，默认 ``http://127.0.0.1:11434``。
            model: 模型名称，如 ``qwen2.5:latest``、``deepseek-r1:8b``。
            timeout: 请求超时秒数，默认 600。
            max_retries: SDK 层重试次数，默认 3。
    """

    @property
    def provider_name(self) -> str:
        return "ollama"

    def _build_client(self, config: dict[str, Any]) -> AsyncOpenAI:
        return AsyncOpenAI(
            api_key="ollama",  # Ollama 不需要认证，但 SDK 要求非空
            base_url=f"{config.get('url', 'http://127.0.0.1:11434').rstrip('/')}/v1",
            timeout=float(self.timeout),
            max_retries=self.max_retries,
        )
