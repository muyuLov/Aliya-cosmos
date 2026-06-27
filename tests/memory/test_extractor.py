"""测试 extractor 模块的解析/验证/工具函数"""

from __future__ import annotations

from memory.extractor import (
    VALID_ENTITY_TYPES,
    _is_valid_entity_type,
    _truncate_input,
    QuintupleExtractor,
)
from memory._utils import parse_json_array


class TestIsValidEntityType:
    def test_valid_chinese_types(self):
        assert _is_valid_entity_type("人物")
        assert _is_valid_entity_type("地点")
        assert _is_valid_entity_type("组织")
        assert _is_valid_entity_type("物品")
        assert _is_valid_entity_type("概念")
        assert _is_valid_entity_type("时间")
        assert _is_valid_entity_type("事件")
        assert _is_valid_entity_type("活动")
        assert _is_valid_entity_type("技能")

    def test_valid_english_types(self):
        assert _is_valid_entity_type("Person")
        assert _is_valid_entity_type("Location")
        assert _is_valid_entity_type("Organization")
        assert _is_valid_entity_type("Object")
        assert _is_valid_entity_type("Concept")
        assert _is_valid_entity_type("Time")
        assert _is_valid_entity_type("Event")
        assert _is_valid_entity_type("Activity")
        assert _is_valid_entity_type("Skill")

    def test_invalid_types(self):
        assert not _is_valid_entity_type("")
        assert not _is_valid_entity_type("妖怪")
        assert not _is_valid_entity_type("abc")
        assert not _is_valid_entity_type("123")
        assert not _is_valid_entity_type("动物")


class TestValidEntityTypesSet:
    def test_frozenset_immutable(self):
        try:
            VALID_ENTITY_TYPES.add("新类型")  # type: ignore[attr-defined]
        except AttributeError:
            pass  # frozenset 不可变，期望的行为

    def test_coverage(self):
        """确保所有中英文类型成对存在"""
        chinese = {"人物", "地点", "组织", "物品", "概念", "时间", "事件", "活动", "技能"}
        english = {"Person", "Location", "Organization", "Object", "Concept", "Time", "Event", "Activity", "Skill"}
        assert chinese | english == VALID_ENTITY_TYPES


class TestTruncateInput:
    def test_short_text(self):
        text = "你好世界"
        assert _truncate_input(text, max_chars=100) == text

    def test_exact_boundary(self):
        text = "a" * 100
        result = _truncate_input(text, max_chars=100)
        assert result == text

    def test_truncate_at_sentence_boundary(self):
        text = "第一句。第二句。第三句。"
        result = _truncate_input(text, max_chars=10)
        assert len(result) <= 13  # 10 chars + "..."
        assert result.endswith("...")

    def test_very_long_no_punctuation(self):
        text = "无标点" * 200
        MAX = 100
        result = _truncate_input(text, max_chars=MAX)
        assert len(result) <= MAX + 3


class TestParseJsonArray:
    def test_direct_json_array(self):
        result = parse_json_array('[["a","b","c","d","e"]]', "测试")
        assert result == [["a", "b", "c", "d", "e"]]

    def test_json_in_markdown(self):
        result = parse_json_array(
            '```json\n[["主体","人物","动作","客体","物品"]]\n```', "测试"
        )
        assert result is not None

    def test_empty_brackets(self):
        result = parse_json_array("[]", "测试")
        assert result == []

    def test_invalid_content(self):
        result = parse_json_array("这不是JSON", "测试")
        assert result is None

    def test_partial_extract(self):
        text = "结果是：[1, 2, 3]，请查收"
        result = parse_json_array(text, "测试")
        assert result == [1, 2, 3]


class TestValidateQuintuples:
    def test_valid_quintuples(self):
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        data = [["小明", "人物", "喜欢", "苹果", "物品"]]
        result = extractor._validate_quintuples(data)
        assert result == [("小明", "人物", "喜欢", "苹果", "物品")]

    def test_invalid_entity_type_skipped(self):
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        data = [["小明", "妖怪", "喜欢", "苹果", "物品"]]
        result = extractor._validate_quintuples(data)
        assert result == []

    def test_partial_invalid_skipped(self):
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        data = [
            ["小明", "人物", "喜欢", "苹果", "物品"],
            ["妖怪", "妖怪", "吃", "人类", "妖怪"],
            ["小红", "人物", "讨厌", "香蕉", "物品"],
        ]
        result = extractor._validate_quintuples(data)
        assert result == [
            ("小明", "人物", "喜欢", "苹果", "物品"),
            ("小红", "人物", "讨厌", "香蕉", "物品"),
        ]

    def test_wrong_length_skipped(self):
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        data = [["a", "b", "c"]]  # 只有 3 个元素
        result = extractor._validate_quintuples(data)
        assert result == []

    def test_empty_string_skipped(self):
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        data = [["", "人物", "喜欢", "苹果", "物品"]]
        result = extractor._validate_quintuples(data)
        assert result == []

    def test_not_a_list(self):
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        result = extractor._validate_quintuples("not a list")
        assert result == []

    def test_empty_list(self):
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        result = extractor._validate_quintuples([])
        assert result == []

    def test_strips_whitespace(self):
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        data = [[" 小明 ", " 人物 ", " 喜欢 ", " 苹果 ", " 物品 "]]
        result = extractor._validate_quintuples(data)
        assert result == [("小明", "人物", "喜欢", "苹果", "物品")]


class TestParseResponse:
    def test_valid_json(self):
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        result = extractor._parse_response('[["小明","人物","喜欢","苹果","物品"]]')
        assert result == [("小明", "人物", "喜欢", "苹果", "物品")]

    def test_invalid_json(self):
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        result = extractor._parse_response("不是JSON")
        assert result == []

    def test_empty_array(self):
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        result = extractor._parse_response("[]")
        assert result == []
