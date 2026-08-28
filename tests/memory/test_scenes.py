"""Task 5.1: 场景/弧线管理测试

验证 SceneEntry / Scene / Arc / 场景阈值关闭 + LLM 压缩。
"""

import pytest
from datetime import datetime, timezone


def test_scene_entry_create():
    """SceneEntry 应持有 id / kind / content / occurred_at"""
    from core.memory.scenes import SceneEntry

    entry = SceneEntry(
        id=1,
        kind="user_message",
        content="你好",
    )
    assert entry.id == 1
    assert entry.kind == "user_message"
    assert entry.content == "你好"


def test_scene_entry_kinds():
    """SceneEntry.kind 应支持四种类型"""
    from core.memory.scenes import SceneEntry, SCENE_ENTRY_KINDS

    assert "user_message" in SCENE_ENTRY_KINDS
    assert "ai_reply" in SCENE_ENTRY_KINDS
    assert "tool_call" in SCENE_ENTRY_KINDS
    assert "system_event" in SCENE_ENTRY_KINDS


def test_scene_create():
    """Scene 应持有 scene_id / entries / status / hook"""
    from core.memory.scenes import Scene

    scene = Scene(scene_id="s1", arc_id="arc1")
    assert scene.scene_id == "s1"
    assert scene.arc_id == "arc1"
    assert scene.status == "active"
    assert scene.hook == ""


def test_scene_add_entry():
    """Scene 应能添加条目并计数"""
    from core.memory.scenes import Scene, SceneEntry

    scene = Scene(scene_id="s1")
    scene.add_entry(SceneEntry(id=1, kind="user_message", content="你好"))
    scene.add_entry(SceneEntry(id=2, kind="ai_reply", content="嗨"))

    assert len(scene.entries) == 2
    assert scene.entry_count == 2
    assert scene.char_count > 0


def test_scene_should_close_by_entry_count():
    """条目数达到阈值时 should_close 应返回 True"""
    from core.memory.scenes import Scene, SceneEntry

    scene = Scene(scene_id="s1", entry_threshold=3)
    for i in range(3):
        scene.add_entry(SceneEntry(id=i, kind="user_message", content=f"消息{i}"))
    assert scene.should_close() is True


def test_scene_should_not_close_under_threshold():
    """条目数未达阈值时 should_close 应返回 False"""
    from core.memory.scenes import Scene, SceneEntry

    scene = Scene(scene_id="s1", entry_threshold=10)
    scene.add_entry(SceneEntry(id=1, kind="user_message", content="你好"))
    assert scene.should_close() is False


def test_scene_should_close_by_char_count():
    """字符数达到阈值时 should_close 应返回 True"""
    from core.memory.scenes import Scene, SceneEntry

    scene = Scene(scene_id="s1", char_threshold=100)
    scene.add_entry(SceneEntry(id=1, kind="user_message", content="A" * 100))
    assert scene.should_close() is True


def test_arc_create():
    """Arc 应持有 arc_id / scenes / status"""
    from core.memory.scenes import Arc

    arc = Arc(arc_id="arc1", theme="日常")
    assert arc.arc_id == "arc1"
    assert arc.theme == "日常"
    assert arc.status == "active"


def test_arc_add_scene():
    """Arc 应能添加场景"""
    from core.memory.scenes import Arc, Scene

    arc = Arc(arc_id="arc1")
    scene = Scene(scene_id="s1", arc_id="arc1")
    arc.add_scene(scene)

    assert len(arc.scenes) == 1
    assert arc.scenes[0].scene_id == "s1"


def test_arc_close_scene():
    """Arc 关闭场景后应记录总结"""
    from core.memory.scenes import Arc, Scene

    arc = Arc(arc_id="arc1")
    scene = Scene(scene_id="s1", arc_id="arc1")
    arc.add_scene(scene)
    arc.close_scene("s1", summary="对话结束")

    assert scene.status == "closed"
    assert arc.scenes[0].summary == "对话结束"
