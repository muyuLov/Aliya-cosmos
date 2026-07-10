"""测试 graph 模块的工具函数（无需 Neo4j 连接）"""

from __future__ import annotations

from core.memory.graph import _REL_TYPE_PATTERN


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
