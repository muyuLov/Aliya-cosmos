"""Task 3.0: 主剧本与参与者模块测试

验证 ScriptEntry / CanonicalStory / Participant / ScriptStore 的核心行为。
"""

import pytest
from datetime import datetime, timezone


# ── ScriptEntry 基础 ───────────────────────────────────────


def test_script_entry_create():
    """ScriptEntry 应持有 story_id, participant_id, kind, actor, content, occurred_at"""
    from agent.story.entry import ScriptEntry

    now = datetime.now(timezone.utc)
    entry = ScriptEntry(
        story_id="s1",
        participant_id="user",
        kind="user_message",
        actor="user",
        content="你好",
        occurred_at=now,
    )
    assert entry.story_id == "s1"
    assert entry.participant_id == "user"
    assert entry.kind == "user_message"
    assert entry.actor == "user"
    assert entry.content == "你好"
    assert entry.occurred_at == now


def test_script_entry_kinds():
    """ScriptEntry.kind 应支持四种类型"""
    from agent.story.entry import ScriptEntry, ENTRY_KINDS

    assert "user_message" in ENTRY_KINDS
    assert "character_message" in ENTRY_KINDS
    assert "tool_call" in ENTRY_KINDS
    assert "system_event" in ENTRY_KINDS


# ── CanonicalStory 基础 ────────────────────────────────────


def test_canonical_story_create():
    """CanonicalStory 应持有 setting / state / cursor_at / status"""
    from agent.story.canonical import CanonicalStory

    story = CanonicalStory(story_id="s1", setting="日常对话")
    assert story.story_id == "s1"
    assert story.setting == "日常对话"
    assert story.status == "active"


def test_canonical_story_default_status():
    """新建 CanonicalStory 默认 status='active'"""
    from agent.story.canonical import CanonicalStory

    story = CanonicalStory(story_id="s2", setting="冒险")
    assert story.status == "active"


# ── Participant 基础 ────────────────────────────────────────


def test_participant_create():
    """Participant 应持有 story_id / person_id / display_name / profile"""
    from agent.story.participant import Participant

    p = Participant(
        story_id="s1",
        person_id="aliya",
        display_name="Aliya",
        profile="性格温和的 AI 伴侣",
    )
    assert p.story_id == "s1"
    assert p.person_id == "aliya"
    assert p.display_name == "Aliya"
    assert p.relationship == ""
    assert p.unread_message_count == 0


def test_participant_relationship_update():
    """Participant.relationship 可更新"""
    from agent.story.participant import Participant

    p = Participant(
        story_id="s1",
        person_id="user",
        display_name="用户",
        profile="",
    )
    p.relationship = "朋友"
    assert p.relationship == "朋友"


# ── ScriptStore 持久化 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_script_store_create_and_get(tmp_path):
    """ScriptStore 应能创建剧本并按 ID 检索"""
    from agent.story.script_store import ScriptStore

    db_path = str(tmp_path / "test_story.db")
    store = ScriptStore(db_path)
    await store.initialize()

    await store.create_story("s1", setting="日常对话")
    story = await store.get_story("s1")
    assert story is not None
    assert story.story_id == "s1"
    assert story.setting == "日常对话"

    await store.close()


@pytest.mark.asyncio
async def test_script_store_add_entry(tmp_path):
    """ScriptStore 应能添加剧本条目并查询"""
    from agent.story.script_store import ScriptStore
    from agent.story.entry import ScriptEntry

    db_path = str(tmp_path / "test_story.db")
    store = ScriptStore(db_path)
    await store.initialize()

    await store.create_story("s1", setting="日常")
    entry = ScriptEntry(
        story_id="s1",
        participant_id="user",
        kind="user_message",
        actor="user",
        content="你好",
        occurred_at=datetime.now(timezone.utc),
    )
    await store.append_entry(entry)

    entries = await store.get_recent_entries("s1", limit=10)
    assert len(entries) == 1
    assert entries[0].content == "你好"

    await store.close()


@pytest.mark.asyncio
async def test_script_store_cursor_advancement(tmp_path):
    """append_entry 后 cursor_at 应推进到 occurred_at"""
    from agent.story.script_store import ScriptStore
    from agent.story.entry import ScriptEntry

    db_path = str(tmp_path / "test_story.db")
    store = ScriptStore(db_path)
    await store.initialize()

    await store.create_story("s1", setting="日常")
    now = datetime.now(timezone.utc)
    entry = ScriptEntry(
        story_id="s1",
        participant_id="user",
        kind="user_message",
        actor="user",
        content="测试",
        occurred_at=now,
    )
    await store.append_entry(entry)

    story = await store.get_story("s1")
    assert story is not None
    assert story.cursor_at is not None

    await store.close()


@pytest.mark.asyncio
async def test_script_store_add_participant(tmp_path):
    """ScriptStore 应能添加参与者并检索"""
    from agent.story.script_store import ScriptStore
    from agent.story.participant import Participant

    db_path = str(tmp_path / "test_story.db")
    store = ScriptStore(db_path)
    await store.initialize()

    await store.create_story("s1", setting="日常")
    p = Participant(
        story_id="s1",
        person_id="aliya",
        display_name="Aliya",
        profile="AI 伴侣",
    )
    await store.upsert_participant(p)

    participants = await store.get_participants("s1")
    assert len(participants) == 1
    assert participants[0].person_id == "aliya"

    await store.close()


# ── story 包 __init__ ──────────────────────────────────────


def test_story_package_exports():
    """agent.story 包应导出关键类型"""
    from agent.story import (
        CanonicalStory,
        Participant,
        ScriptEntry,
        ScriptStore,
    )

    assert CanonicalStory is not None
    assert Participant is not None
    assert ScriptEntry is not None
    assert ScriptStore is not None
