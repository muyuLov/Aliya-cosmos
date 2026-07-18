"""测试 TTS 文本预处理模块：动作描写过滤、分句、短句合并"""

from __future__ import annotations

from core.tts.text_splitter import (
    filter_actions,
    merge_short_segments,
    split_text,
)


class TestFilterActions:
    def test_remove_chinese_brackets(self):
        assert filter_actions("你好（微笑）世界") == "你好世界"

    def test_remove_english_brackets(self):
        assert filter_actions("Hello (waves) there") == "Hello there"

    def test_remove_multiple_brackets(self):
        assert filter_actions("测试（动作1）文本（动作2）结束") == "测试文本结束"

    def test_remove_ellipsis_chinese(self):
        assert filter_actions("嗯…好的") == "嗯好的"

    def test_remove_ellipsis_english(self):
        assert filter_actions("你好...世界") == "你好世界"

    def test_clean_whitespace(self):
        result = filter_actions("  很多   空格  ")
        assert "  " not in result
        assert result == "很多 空格"

    def test_no_brackets_unchanged(self):
        assert filter_actions("普通文本") == "普通文本"

    def test_empty_string(self):
        assert filter_actions("") == ""

    def test_nested_brackets(self):
        """嵌套括号：外左括号匹配到内右括号，残余字符应仍被移除"""
        # 当前正则不支持嵌套，只匹配最外层到第一个右括号
        result = filter_actions("（外层（内层））")
        assert "外层" not in result
        assert "内层" not in result


class TestSplitText:
    def test_single_sentence(self):
        segments = split_text("你好世界。")
        assert len(segments) == 1
        assert segments[0] == "你好世界。"

    def test_multiple_sentences(self):
        segments = split_text("你好世界。再见！", min_segment_length=3)
        assert len(segments) == 2
        assert segments[0] == "你好世界。"
        assert segments[1] == "再见！"

    def test_newline_splits(self):
        segments = split_text("第一行内容\n第二行内容", min_segment_length=3)
        assert len(segments) >= 2

    def test_question_mark(self):
        segments = split_text("你好世界吗？我很好。", min_segment_length=3)
        assert segments[0] == "你好世界吗？"
        assert segments[1] == "我很好。"

    def test_short_segments_merged(self):
        """过短段应自动合并"""
        segments = split_text("你好。好。", min_segment_length=5)
        assert len(segments) == 1  # "好。" 太短，合并到前一段

    def test_empty_text_returns_single(self):
        segments = split_text("")
        assert segments == [""]

    def test_only_whitespace(self):
        segments = split_text("   ")
        assert len(segments) >= 1

    def test_no_punctuation(self):
        segments = split_text("这是一个没有标点的长文本需要分段")
        assert len(segments) == 1


class TestMergeShortSegments:
    def test_single_segment_unchanged(self):
        assert merge_short_segments(["你好世界"], 8) == ["你好世界"]

    def test_two_short_merged(self):
        assert merge_short_segments(["你", "好", "世界"], 8) == ["你好世界"]

    def test_short_first_merged(self):
        result = merge_short_segments(["短", "这是长段内容"], 8)
        assert len(result) == 1
        assert result[0] == "短这是长段内容"

    def test_short_last_merged(self):
        result = merge_short_segments(["这是长段内容", "短"], 5)
        assert len(result) == 1
        assert result[0] == "这是长段内容短"

    def test_all_long_unchanged(self):
        result = merge_short_segments(["段落一", "段落二"], 3)
        assert result == ["段落一", "段落二"]

    def test_empty_input(self):
        assert merge_short_segments([], 8) == []
