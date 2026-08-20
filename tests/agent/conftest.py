"""agent 模块共享测试夹具"""

from __future__ import annotations

import math
import zlib

import pytest

from core.vector.embedding import EmbeddingProvider


class FakeEmbeddingProvider(EmbeddingProvider):
    """测试用确定性向量化提供者：字符 crc32 哈希特征向量（L2 归一化）。"""

    def __init__(self, dimension: int = 64) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def provider_name(self) -> str:
        return "fake"

    async def embed(self, texts):
        vectors = []
        for text in texts:
            vec = [0.0] * self._dimension
            for ch in text:
                vec[zlib.crc32(ch.encode("utf-8")) % self._dimension] += 1.0
            norm = math.sqrt(sum(v * v for v in vec))
            if norm > 0:
                vec = [v / norm for v in vec]
            vectors.append(vec)
        return vectors


@pytest.fixture
def fake_embedding() -> FakeEmbeddingProvider:
    """测试用确定性向量化提供者实例。"""
    return FakeEmbeddingProvider()
