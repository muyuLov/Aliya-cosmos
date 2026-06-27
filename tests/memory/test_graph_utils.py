"""测试 graph 模块的工具函数（无需 Neo4j 连接）"""

from __future__ import annotations

import re

from memory.graph import _safe_truncate, _REL_TYPE_PATTERN


class TestSafeTruncate:
    def test_short_text(self):
        text = "短文本"
        assert _safe_truncate(text, max_chars=100) == text

    def test_exact_boundary(self):
        text = "a" * 100
        result = _safe_truncate(text, max_chars=100)
        assert result == text

    def test_truncate_at_sentence_end(self):
        text = "第一句。第二句。" + "x" * 500
        result = _safe_truncate(text, max_chars=100)
        assert len(result) <= 100
        assert "第一句。第二句。"[:len(result)] == result[:len("第一句。第二句。")]

    def test_truncate_at_newline(self):
        text = "第一行\n" + "y" * 200
        result = _safe_truncate(text, max_chars=4)
        assert result == "第一行\n"

    def test_fallback_no_boundary(self):
        text = "无标点" * 200
        result = _safe_truncate(text, max_chars=100)
        assert len(result) <= 100

    def test_empty_text(self):
        assert _safe_truncate("", max_chars=100) == ""


class TestRelTypePattern:
    def test_valid_chinese(self):
        assert _REL_TYPE_PATTERN.match("工作于")
        assert _REL_TYPE_PATTERN.match("居住在")
        assert _REL_TYPE_PATTERN.match("喜欢")

    def test_valid_english(self):
        assert _REL_TYPE_PATTERN.match("WORKS_AT")
        assert _REL_TYPE_PATTERN.match("lives-in")
        assert _REL_TYPE_PATTERN.match("IS_FRIEND_OF")

    def test_valid_mixed(self):
        assert _REL_TYPE_PATTERN.match("工作在Google")
        assert _REL_TYPE_PATTERN.match("study-at_清华")

    def test_invalid_chars(self):
        assert not _REL_TYPE_PATTERN.match("工作@公司")
        assert not _REL_TYPE_PATTERN.match("喜欢！跑步")
        assert not _REL_TYPE_PATTERN.match("has space")

    def test_empty(self):
        assert not _REL_TYPE_PATTERN.match("")

    def test_special_chars_rejected(self):
        assert not _REL_TYPE_PATTERN.match("a.b")
        assert not _REL_TYPE_PATTERN.match("a[b]")
        assert not _REL_TYPE_PATTERN.match("a(b)")
        assert not _REL_TYPE_PATTERN.match("a+b")
