"""测试纯 Python 加速实现（rust_bridge.py）"""

from __future__ import annotations

import pytest

from agent.cognition.rust_bridge import (
    accel_mode,
    cosine_batch,
    get_status,
    levenshtein_similarity,
    multi_pattern_match,
)


class TestAccelMode:
    def test_mode_is_python(self):
        assert accel_mode() == "python"

    def test_get_status(self):
        status = get_status()
        assert status["mode"] == "python"
        assert status["native_loaded"] is False


class TestCosineBatch:
    def test_identical_vectors(self):
        sims = cosine_batch([[1.0, 0.0]], [1.0, 0.0])
        assert len(sims) == 1
        assert sims[0] == pytest.approx(1.0, abs=1e-6)

    def test_orthogonal_vectors(self):
        sims = cosine_batch([[1.0, 0.0]], [0.0, 1.0])
        assert sims[0] == pytest.approx(0.0, abs=1e-6)

    def test_empty_vectors(self):
        assert cosine_batch([], [1.0, 0.0]) == []

    def test_zero_vector_safe(self):
        sims = cosine_batch([[0.0, 0.0]], [1.0, 0.0])
        assert sims[0] == pytest.approx(0.0)

    def test_dimension_mismatch_returns_zero(self):
        sims = cosine_batch([[1.0, 0.0, 0.5]], [1.0, 0.0])
        assert sims[0] == pytest.approx(0.0)


class TestMultiPatternMatch:
    def test_match_matrix(self):
        matrix = multi_pattern_match(
            [r"咖啡", r"\d{4}"], ["我喜欢咖啡", "验证码1234"]
        )
        assert len(matrix) == 2  # 两个 pattern
        assert matrix[0] == [True, False]
        assert matrix[1] == [False, True]

    def test_no_match(self):
        matrix = multi_pattern_match([r"zzz"], ["abc"])
        assert matrix == [[False]]

    def test_invalid_pattern_returns_false(self):
        matrix = multi_pattern_match([r"[unclosed"], ["abc"])
        assert matrix == [[False]]


class TestLevenshtein:
    def test_identical(self):
        assert levenshtein_similarity("你好", "你好") == pytest.approx(1.0)

    def test_completely_different(self):
        sim = levenshtein_similarity("abc", "xyz")
        # 距离 3 / 3 = 0
        assert sim == pytest.approx(0.0)

    def test_one_insertion(self):
        sim = levenshtein_similarity("cat", "cats")
        # 距离 1 / 4 = 0.75
        assert sim == pytest.approx(0.75)

    def test_empty_both(self):
        assert levenshtein_similarity("", "") == 1.0
