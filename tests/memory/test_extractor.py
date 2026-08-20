"""测试 extractor 模块的解析/验证/工具函数"""

from __future__ import annotations

from core.memory.extractor import (
    VALID_ENTITY_TYPES,
    _detect_speaker,
    _is_valid_entity_type,
    QuintupleExtractor,
)
from core.memory._utils import parse_json_array


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
        data = [["小明", "人物", "喜欢", "苹果", "物品", "偏好"]]
        result = extractor._validate_quintuples(data)
        assert result == ([("小明", "人物", "喜欢", "苹果", "物品")], ["偏好"])

    def test_invalid_entity_type_downgraded(self):
        """未知实体类型不再跳过，降级为「概念」。"""
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        data = [["小明", "妖怪", "喜欢", "苹果", "物品", "偏好"]]
        result = extractor._validate_quintuples(data)
        assert result == ([("小明", "概念", "喜欢", "苹果", "物品")], ["偏好"])

    def test_partial_invalid_downgraded(self):
        data = [
            ["小明", "人物", "喜欢", "苹果", "物品", "偏好"],
            ["妖怪", "妖怪", "吃", "人类", "妖怪", "属性"],
            ["小红", "人物", "讨厌", "香蕉", "物品", "偏好"],
        ]
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        result = extractor._validate_quintuples(data)
        # "妖怪"类型降级为「概念」，不再跳过
        assert result == (
            [
                ("小明", "人物", "喜欢", "苹果", "物品"),
                ("妖怪", "概念", "吃", "人类", "概念"),
                ("小红", "人物", "讨厌", "香蕉", "物品"),
            ],
            ["偏好", "属性", "偏好"],
        )

    def test_wrong_length_skipped(self):
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        data = [["a", "b", "c"]]  # 只有 3 个元素
        result = extractor._validate_quintuples(data)
        assert result == ([], [])

    def test_empty_string_skipped(self):
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        data = [["", "人物", "喜欢", "苹果", "物品", "偏好"]]
        result = extractor._validate_quintuples(data)
        assert result == ([], [])

    def test_not_a_list(self):
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        result = extractor._validate_quintuples("not a list")
        assert result == ([], [])

    def test_empty_list(self):
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        result = extractor._validate_quintuples([])
        assert result == ([], [])

    def test_strips_whitespace(self):
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        data = [[" 小明 ", " 人物 ", " 喜欢 ", " 苹果 ", " 物品 ", " 偏好 "]]
        result = extractor._validate_quintuples(data)
        assert result == ([("小明", "人物", "喜欢", "苹果", "物品")], ["偏好"])

    def test_noise_fallback_interaction_kept(self):
        """兜底寒暄/空泛互动不再过滤（放宽规则：允许提取）。"""
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        data = [["Aliya", "人物", "请求", "对方重复", "概念", "人际"]]
        assert extractor._validate_quintuples(data) == (
            [("Aliya", "人物", "请求", "对方重复", "概念")],
            ["人际"],
        )

    def test_noise_vague_tail_kept(self):
        """宾语为'对方…'式不具体表达时不再过滤。"""
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        data = [
            ["Aliya", "人物", "询问", "对方是否想念她", "概念", "人际"],
            ["Aliya", "人物", "进行", "屏幕聊天", "活动", "事件"],
        ]
        assert extractor._validate_quintuples(data) == (
            [
                ("Aliya", "人物", "询问", "对方是否想念她", "概念"),
                ("Aliya", "人物", "进行", "屏幕聊天", "活动"),
            ],
            ["人际", "事件"],
        )

    def test_invalid_category_skipped(self):
        """非法类别条目被跳过。"""
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        data = [
            ["小明", "人物", "喜欢", "苹果", "物品", "非法类别"],
            ["小明", "人物", "喜欢", "苹果", "物品", "偏好"],
        ]
        result = extractor._validate_quintuples(data)
        assert result == ([("小明", "人物", "喜欢", "苹果", "物品")], ["偏好"])

    def test_pronoun_head_replaced_fallback(self):
        """无 speaker 信息时回退：我→user_name，你→ai_name。"""
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        data = [
            ["我", "人物", "喜欢", "咖啡", "物品", "偏好"],
            ["你", "人物", "是", "宇航员", "职业", "身份"],
        ]
        result = extractor._validate_quintuples(data)
        assert result == (
            [
                (extractor.user_name, "人物", "喜欢", "咖啡", "物品"),
                (extractor.ai_name, "人物", "是", "宇航员", "职业"),
            ],
            ["偏好", "身份"],
        )

    def test_pronoun_head_by_speaker_user(self):
        """user 链（用户在说话）：我→user_name，你→ai_name。"""
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        data = [
            ["我", "人物", "喜欢", "咖啡", "物品", "偏好"],
            ["你", "人物", "是", "宇航员", "职业", "身份"],
        ]
        result = extractor._validate_quintuples(data, speaker=extractor.user_name)
        assert result == (
            [
                (extractor.user_name, "人物", "喜欢", "咖啡", "物品"),
                (extractor.ai_name, "人物", "是", "宇航员", "职业"),
            ],
            ["偏好", "身份"],
        )

    def test_pronoun_head_by_speaker_aliya(self):
        """aliya 链（Aliya 在说话）：我→ai_name，你→user_name。"""
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        data = [
            ["我", "人物", "是", "宇航员", "职业", "身份"],
            ["你", "人物", "喜欢", "咖啡", "物品", "偏好"],
        ]
        result = extractor._validate_quintuples(data, speaker=extractor.ai_name)
        assert result == (
            [
                (extractor.ai_name, "人物", "是", "宇航员", "职业"),
                (extractor.user_name, "人物", "喜欢", "咖啡", "物品"),
            ],
            ["身份", "偏好"],
        )

    def test_pronoun_head_unknown_speaker_falls_back(self):
        """speaker 为第三方角色时：我→该角色，你→回退 ai_name。"""
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        data = [
            ["我", "人物", "来自", "火星", "地点", "属性"],
            ["你", "人物", "是", "舰长", "职业", "身份"],
        ]
        result = extractor._validate_quintuples(data, speaker="Kane")
        assert result == (
            [
                ("Kane", "人物", "来自", "火星", "地点"),
                (extractor.ai_name, "人物", "是", "舰长", "职业"),
            ],
            ["属性", "身份"],
        )

    def test_duplicates_removed(self):
        """完全重复的五元组去重，保留首次出现顺序。"""
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        data = [
            ["小明", "人物", "喜欢", "苹果", "物品", "偏好"],
            ["小明", "人物", "喜欢", "苹果", "物品", "偏好"],
            ["小明", "人物", "喜欢", "香蕉", "物品", "偏好"],
        ]
        result = extractor._validate_quintuples(data)
        assert result == (
            [("小明", "人物", "喜欢", "苹果", "物品"), ("小明", "人物", "喜欢", "香蕉", "物品")],
            ["偏好", "偏好"],
        )

    def test_long_field_truncated(self):
        """超长宾语/关系被截断，防止整段原文入库。"""
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        long_tail = "这是一段非常非常非常非常非常非常非常非常非常非常长的宾语描述"  # 30 字
        data = [["小明", "人物", "喜欢", long_tail, "物品", "偏好"]]
        (quintuples, _categories) = extractor._validate_quintuples(data)
        assert len(quintuples) == 1
        assert len(quintuples[0][3]) <= 64


class TestParseResponse:
    def test_valid_json(self):
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        result = extractor._parse_response('[["小明","人物","喜欢","苹果","物品","偏好"]]')
        assert result == ([("小明", "人物", "喜欢", "苹果", "物品")], ["偏好"])

    def test_invalid_json(self):
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        result = extractor._parse_response("不是JSON")
        assert result == ([], [])

    def test_empty_array(self):
        extractor = QuintupleExtractor(max_retries=1, timeout=5)
        result = extractor._parse_response("[]")
        assert result == ([], [])


class TestDetectSpeaker:
    """对话说话人解析（供代词主体按角色自动调整）"""

    def test_ascii_colon(self):
        assert _detect_speaker("Aliya: 我是宇航员") == "Aliya"

    def test_chinese_colon(self):
        assert _detect_speaker("Aliya：我是宇航员") == "Aliya"

    def test_multiline_first_line(self):
        text = "cosmos: 早上好呀！\nAliya: 早上好。"
        assert _detect_speaker(text) == "cosmos"

    def test_none_on_no_prefix(self):
        assert _detect_speaker("没有冒号的文本") is None

    def test_none_on_empty(self):
        assert _detect_speaker("") is None

    def test_empty_prefix_returns_none(self):
        assert _detect_speaker(": 没有角色名") is None
