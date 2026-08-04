"""Embedding 向量化模块

提供 OpenAI 兼容 Embedding API 向量化实现（``OpenAIEmbeddingProvider``），
通过 :func:`EmbeddingFactory.create` 创建。

连接参数（model / url / api_key）均来自向量模块自身配置，
不做静默补齐或降级；配置不完整时抛出 ``VectorConfigError``。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional, Sequence

from openai import AsyncOpenAI

from core.logger import get_logger
from core.vector.config import EmbeddingConfig, VectorConfig
from core.vector.exceptions import DimensionMismatchError, EmbeddingAPIError, VectorConfigError

logger = get_logger(__name__)


class EmbeddingProvider(ABC):
    """向量化提供者抽象基类"""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """向量维度；尚未向量化任何文本时返回 0（未知）。"""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """提供者名称，用于日志与异常标注。"""

    @abstractmethod
    async def embed(self, texts: Sequence[str]) -> List[List[float]]:
        """将一批文本向量化，返回与输入顺序一致的向量列表。

        Args:
            texts: 待向量化的文本列表。

        Returns:
            浮点向量列表。

        Raises:
            EmbeddingError: 向量化失败。
        """


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI 兼容 Embedding API 提供者

    通过 ``AsyncOpenAI`` 客户端调用 ``/v1/embeddings`` 接口，
    支持按 ``batch_size`` 分批向量化。

    Args:
        config: EmbeddingConfig，必须包含 model 与 url。
    """

    def __init__(self, config: EmbeddingConfig) -> None:
        if not config.model:
            raise VectorConfigError("必须配置 embedding.model")
        if not config.url:
            raise VectorConfigError("必须配置 embedding.url")
        # 本地服务（Ollama/LM Studio 等）通常无真实密钥，api_key 为空时用占位符
        # 以满足 OpenAI SDK 的必填约束；不主动从 LLM 配置补齐。
        self._config = config
        # 用配置的 dimension 作为初始已知维度（0=未知）；首次向量化后以实际值覆盖
        self._dimension: Optional[int] = config.dimension or None
        self._client = AsyncOpenAI(
            api_key=config.api_key or "embedding",
            base_url=f"{config.url.rstrip('/')}/v1",
            timeout=60,
            max_retries=3,
        )
        self._batch_size = config.batch_size

    @property
    def dimension(self) -> int:
        return self._dimension or 0

    @property
    def provider_name(self) -> str:
        return "api"

    async def embed(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        vectors: List[List[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = list(texts[i : i + self._batch_size])
            try:
                response = await self._client.embeddings.create(
                    model=self._config.model,
                    input=batch,
                )
            except Exception as exc:
                raise EmbeddingAPIError(
                    message=str(exc),
                    provider=self.provider_name,
                    details={"model": self._config.model, "batch_size": len(batch)},
                    cause=exc,
                ) from exc
            vectors.extend(item.embedding for item in response.data)

        if vectors:
            actual = len(vectors[0])
            # 配置了期望维度时校验：API 返回维度不一致说明配置错误，尽早暴露
            expected = self._config.dimension
            if expected and actual != expected:
                raise DimensionMismatchError(expected, actual)
            self._dimension = actual
        return vectors

    async def aclose(self) -> None:
        """关闭底层 AsyncOpenAI 客户端，释放连接池资源。"""
        await self._client.close()


class EmbeddingFactory:
    """按配置创建 Embedding 提供者（固定为 OpenAI 兼容 Embedding API）"""

    @staticmethod
    def create(config: VectorConfig) -> EmbeddingProvider:
        return OpenAIEmbeddingProvider(config.embedding)


__all__ = [
    "EmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "EmbeddingFactory",
]
