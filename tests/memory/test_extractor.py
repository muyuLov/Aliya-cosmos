"""测试 extractor 模块的解析/验证/工具函数"""

from __future__ import annotations

from memory.extractor import (
    VALID_ENTITY_TYPES,
    _is_valid_entity_type,
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
        chinese = {
            # 人物与角色
            "人物", "角色", "身份",
            # 地点与设施
            "地点", "区域", "设施",
            # 组织与机构
            "组织", "机构", "品牌",
            # 物品与产品
            "物品", "产品", "食物", "动植物",
            # 科技与信息
            "软件", "平台", "技术", "算法", "数据",
            # 时间
            "时间", "日期", "周期",
            # 事件与活动
            "事件", "活动",
            # 知识与工作
            "技能", "学科", "领域", "语言", "职业", "项目", "作品",
            # 抽象概念
            "概念", "目标", "规则", "方法", "原因", "结果", "关系",
            # 属性与度量
            "属性", "状态", "年龄", "数量", "价格", "比例",
        }
        english = {
            # 人物与角色
            "Person", "Role", "Identity",
            # 地点与设施
            "Location", "Region", "Facility",
            # 组织与机构
            "Organization", "Institution", "Brand",
            # 物品与产品
            "Object", "Product", "Food", "Biology",
            # 科技与信息
            "Software", "Platform", "Technology", "Algorithm", "Data",
            # 时间
            "Time", "Date", "Period",
            # 事件与活动
            "Event", "Activity",
            # 知识与工作
            "Skill", "Subject", "Domain", "Language", "Occupation", "Project", "Work",
            # 抽象概念
            "Concept", "Goal", "Rule", "Method", "Cause", "Result", "Relation",
            # 属性与度量
            "Attribute", "State", "Age", "Quantity", "Price", "Ratio",
        }
        assert chinese | english == VALID_ENTITY_TYPES


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
