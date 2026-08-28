"""Task 3.1: 结构化输出解析器（metadata_parser）测试

验证 normalize 防御和 prose/transport 分离。
"""

import json
import pytest


def test_parse_script_from_json():
    """应从 JSON 回复中提取 script 字段"""
    from agent.metadata_parser import parse_narrative_output

    raw = json.dumps({
        "script": "今天天气很好，她走出了门",
        "reply": {"mode": "immediate", "content": "你好啊！"},
        "memories": [],
        "intents": [],
        "actions": [],
    })
    result = parse_narrative_output(raw)
    assert result.script == "今天天气很好，她走出了门"


def test_parse_reply_mode():
    """应解析 reply.mode"""
    from agent.metadata_parser import parse_narrative_output

    raw = json.dumps({
        "script": "日常",
        "reply": {"mode": "immediate", "content": "嗨~"},
        "memories": [],
        "intents": [],
        "actions": [],
    })
    result = parse_narrative_output(raw)
    assert result.reply_mode == "immediate"
    assert result.reply_content == "嗨~"


def test_parse_memories():
    """应解析 memories 列表"""
    from agent.metadata_parser import parse_narrative_output

    raw = json.dumps({
        "script": "日常",
        "reply": {"mode": "immediate", "content": "嗨"},
        "memories": [
            {"content": "用户喜欢蓝色", "importance": 0.7, "participantId": "user", "kind": "fact"},
        ],
        "intents": [],
        "actions": [],
    })
    result = parse_narrative_output(raw)
    assert len(result.memories) == 1
    assert result.memories[0]["content"] == "用户喜欢蓝色"


# ── normalize 防御 ────────────────────────────────────────


def test_normalize_script_truncation():
    """script 应被截断到 max_script_characters"""
    from agent.metadata_parser import parse_narrative_output

    long_script = "A" * 10000
    raw = json.dumps({
        "script": long_script,
        "reply": {"mode": "none", "content": ""},
        "memories": [],
        "intents": [],
        "actions": [],
    })
    result = parse_narrative_output(raw, max_script_characters=500)
    assert len(result.script) <= 500


def test_normalize_seen_forced_boolean():
    """seen 字段应被强制为 boolean"""
    from agent.metadata_parser import parse_narrative_output

    raw = json.dumps({
        "script": "日常",
        "reply": {"mode": "immediate", "content": "嗨"},
        "seen": 1,  # 非 boolean
        "memories": [],
        "intents": [],
        "actions": [],
    })
    result = parse_narrative_output(raw)
    assert result.seen is True


def test_normalize_seen_false_forces_reply_none():
    """seen=false 时 reply.mode 应被强制为 none"""
    from agent.metadata_parser import parse_narrative_output

    raw = json.dumps({
        "script": "日常",
        "reply": {"mode": "immediate", "content": "嗨"},
        "seen": False,
        "memories": [],
        "intents": [],
        "actions": [],
    })
    result = parse_narrative_output(raw)
    assert result.reply_mode == "none"


def test_normalize_alter_clamped():
    """alter 值应被限制在 -5..5 范围"""
    from agent.metadata_parser import parse_narrative_output

    raw = json.dumps({
        "script": "日常",
        "reply": {"mode": "none", "content": ""},
        "alter": 10,  # 超出范围
        "memories": [],
        "intents": [],
        "actions": [],
    })
    result = parse_narrative_output(raw)
    assert result.alter == 5

    raw2 = json.dumps({
        "script": "日常",
        "reply": {"mode": "none", "content": ""},
        "alter": -10,
        "memories": [],
        "intents": [],
        "actions": [],
    })
    result2 = parse_narrative_output(raw2)
    assert result2.alter == -5


def test_normalize_intents_max_8():
    """intents 应限制最多 8 条"""
    from agent.metadata_parser import parse_narrative_output

    intents = [
        {"type": "delay", "summary": f"意图{i}", "notBefore": "", "participantId": "user"}
        for i in range(12)
    ]
    raw = json.dumps({
        "script": "日常",
        "reply": {"mode": "none", "content": ""},
        "memories": [],
        "intents": intents,
        "actions": [],
    })
    result = parse_narrative_output(raw)
    assert len(result.intents) <= 8


def test_parse_fallback_to_plain_text():
    """非 JSON 输入应降级为纯文本模式"""
    from agent.metadata_parser import parse_narrative_output

    result = parse_narrative_output("这不是 JSON，是纯文本回复")
    assert result.script == "这不是 JSON，是纯文本回复"
    assert result.reply_mode == "immediate"
    assert result.reply_content == "这不是 JSON，是纯文本回复"


def test_parse_empty_script():
    """空 script 应标记 has_required_script=False"""
    from agent.metadata_parser import parse_narrative_output

    raw = json.dumps({
        "script": "",
        "reply": {"mode": "none", "content": ""},
        "memories": [],
        "intents": [],
        "actions": [],
    })
    result = parse_narrative_output(raw)
    assert result.has_required_script is False


def test_parse_valid_script():
    """非空 script 应标记 has_required_script=True"""
    from agent.metadata_parser import parse_narrative_output

    raw = json.dumps({
        "script": "她走出了门",
        "reply": {"mode": "none", "content": ""},
        "memories": [],
        "intents": [],
        "actions": [],
    })
    result = parse_narrative_output(raw)
    assert result.has_required_script is True
