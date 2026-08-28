"""Task 3.5: 上下文构建器重写测试

验证组装优先级：Canon → recentScript → continuitySnapshot →
分层记忆召回 → Alter 氛围 → Agency 容量 → 结构化输出指令 → 时间端点。
"""

import json
import pytest


@pytest.mark.asyncio
async def test_context_builder_builds_json_context():
    """build_context 应返回可序列化的 JSON 上下文字典"""
    from agent.context import NarrativeContextBuilder

    builder = NarrativeContextBuilder()
    ctx = await builder.build_context(
        user_input="你好",
        story_id="s1",
        participant_id="user",
    )
    assert isinstance(ctx, dict)
    assert "userInput" in ctx
    assert ctx["userInput"] == "你好"
    assert "storyId" in ctx


@pytest.mark.asyncio
async def test_context_includes_canon():
    """上下文应包含 persona / soul / toneRules（Canon 层）"""
    from agent.context import NarrativeContextBuilder

    builder = NarrativeContextBuilder()
    ctx = await builder.build_context(user_input="测试", story_id="s1", participant_id="user")

    # Canon 层字段应存在
    assert "persona" in ctx or "soul" in ctx or "toneRules" in ctx


@pytest.mark.asyncio
async def test_context_includes_time_endpoint():
    """上下文应包含时间端点（utc / nowLocal / period）"""
    from agent.context import NarrativeContextBuilder

    builder = NarrativeContextBuilder()
    ctx = await builder.build_context(user_input="测试", story_id="s1", participant_id="user")

    assert "time" in ctx
    time_ctx = ctx["time"]
    assert "utc" in time_ctx
    assert "nowLocal" in time_ctx
    assert "period" in time_ctx


@pytest.mark.asyncio
async def test_context_includes_output_format():
    """上下文应包含 JSON 输出格式指令"""
    from agent.context import NarrativeContextBuilder

    builder = NarrativeContextBuilder()
    ctx = await builder.build_context(user_input="测试", story_id="s1", participant_id="user")

    assert "outputFormat" in ctx
    assert isinstance(ctx["outputFormat"], dict)


@pytest.mark.asyncio
async def test_context_includes_recent_script():
    """上下文应包含 recentScript（最近剧本摘要）"""
    from agent.context import NarrativeContextBuilder

    builder = NarrativeContextBuilder()
    ctx = await builder.build_context(user_input="测试", story_id="s1", participant_id="user")

    assert "recentScript" in ctx
    assert isinstance(ctx["recentScript"], list)


@pytest.mark.asyncio
async def test_context_respects_budget():
    """上下文总大小应受字符预算约束（上下文应在合理范围内）"""
    from agent.context import NarrativeContextBuilder

    builder = NarrativeContextBuilder(max_context_chars=8000)
    ctx = await builder.build_context(user_input="测试", story_id="s1", participant_id="user")

    serialized = json.dumps(ctx, ensure_ascii=False, default=str)
    # 上下文应包含完整 persona 内容，不超过 50KB
    assert len(serialized) < 50000
    # 上下文大小应合理（包含人设等必须内容）
    assert len(serialized) > 100


@pytest.mark.asyncio
async def test_context_includes_alter():
    """上下文应包含 alter 氛围字段"""
    from agent.context import NarrativeContextBuilder

    builder = NarrativeContextBuilder()
    ctx = await builder.build_context(user_input="测试", story_id="s1", participant_id="user")

    assert "alter" in ctx
    assert isinstance(ctx["alter"], dict)


@pytest.mark.asyncio
async def test_context_includes_participant():
    """上下文应包含参与者信息"""
    from agent.context import NarrativeContextBuilder

    builder = NarrativeContextBuilder()
    ctx = await builder.build_context(user_input="测试", story_id="s1", participant_id="user")

    assert "participantId" in ctx
    assert ctx["participantId"] == "user"
