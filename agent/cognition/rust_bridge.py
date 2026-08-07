"""纯 Python 加速实现（PythonAccel）

原参考 LAAP 认知架构第 13 章的"双轨加速桥接层"。经实践评估，在
本项目中引入 Rust / 原生扩展需要额外编译工具链（MSVC / cargo），
收益与成本不成比例，故改为纯 Python 标准库实现，零外部依赖。

对外保留统一 API（与历史调用方兼容）：
- accel_mode()：恒为 "python"
- cosine_batch：批量余弦相似度
- multi_pattern_match：多正则同时扫描
- levenshtein_similarity：编辑距离相似度
"""

from __future__ import annotations

import re

from core.logger import get_logger

logger = get_logger(__name__)

# 加速模式：纯 Python 实现
_ACCEL_MODE = "python"


def accel_mode() -> str:
    """返回当前实现模式（恒为 "python"）。"""
    return _ACCEL_MODE


def cosine_batch(vectors: list[list[float]], query: list[float]) -> list[float]:
    """批量计算 query 与 vectors 的余弦相似度。

    Args:
        vectors: 向量列表。
        query: 查询向量。

    Returns:
        相似度列表（长度与 vectors 相同）。
    """
    q_norm = _norm(query)
    results: list[float] = []
    for vec in vectors:
        if len(vec) != len(query) or not vec:
            results.append(0.0)
            continue
        v_norm = _norm(vec)
        if v_norm == 0.0 or q_norm == 0.0:
            results.append(0.0)
            continue
        dot = sum(x * y for x, y in zip(vec, query))
        results.append(dot / (v_norm * q_norm))
    return results


def _norm(v: list[float]) -> float:
    return sum(x * x for x in v) ** 0.5


def multi_pattern_match(
    patterns: list[str], texts: list[str]
) -> list[list[bool]]:
    """批量执行多正则匹配。

    Args:
        patterns: 正则模式列表。
        texts: 待扫描文本列表。

    Returns:
        match 矩阵：results[i][j] = pattern_i 是否命中 texts[j]。
    """
    compiled: list[re.Pattern | None] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern, flags=re.IGNORECASE))
        except re.error:
            compiled.append(None)
    matrix: list[list[bool]] = []
    for compiled_re in compiled:
        if compiled_re is None:
            matrix.append([False] * len(texts))
        else:
            matrix.append([bool(compiled_re.search(t)) for t in texts])
    return matrix


def levenshtein_similarity(a: str, b: str) -> float:
    """编辑距离相似度 [0,1]（1 = 完全相同）。"""
    dist = _levenshtein(a, b)
    max_len = max(len(a), len(b))
    return 1.0 - dist / max_len if max_len else 1.0


def _levenshtein(a: str, b: str) -> int:
    """编辑距离（滚动数组优化）。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[-1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = curr
    return prev[-1]


def get_status() -> dict:
    """实现状态报告。"""
    return {
        "mode": _ACCEL_MODE,
        "native_loaded": False,
    }


__all__ = [
    "accel_mode",
    "cosine_batch",
    "multi_pattern_match",
    "levenshtein_similarity",
    "get_status",
]
