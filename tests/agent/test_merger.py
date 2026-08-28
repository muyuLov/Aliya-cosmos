"""Task 3.3b: 消息合并与过期请求取消（merger）测试

验证同一关系分支连续消息合并、过期请求取消。
"""

import asyncio
import pytest


@pytest.mark.asyncio
async def test_merger_merges_within_window():
    """2 秒窗口内的连续消息应被合并"""
    from agent.story.merger import MessageMerger

    merger = MessageMerger(merge_window_ms=2000)
    await merger.push("s1", "user", "第一条")
    merged = await merger.push("s1", "user", "第二条")
    assert merged is not None
    assert "第一条" in merged
    assert "第二条" in merged


@pytest.mark.asyncio
async def test_merger_does_not_merge_outside_window():
    """超过合并窗口的消息不应合并"""
    from agent.story.merger import MessageMerger

    merger = MessageMerger(merge_window_ms=50)  # 50ms 窗口
    await merger.push("s1", "user", "第一条")
    await asyncio.sleep(0.1)  # 等待超过窗口
    merged = await merger.push("s1", "user", "第二条")
    assert merged is None  # 不合并，第二条是独立消息


@pytest.mark.asyncio
async def test_merger_different_participant_not_merged():
    """不同参与者的消息不应合并"""
    from agent.story.merger import MessageMerger

    merger = MessageMerger(merge_window_ms=2000)
    await merger.push("s1", "user", "用户说话")
    merged = await merger.push("s1", "aliya", "角色说话")
    assert merged is None  # 不同参与者不合并


@pytest.mark.asyncio
async def test_supersede_cancels_pending():
    """should_supersede 在提交前应取消待处理请求"""
    from agent.story.merger import MessageMerger

    merger = MessageMerger(merge_window_ms=2000)
    await merger.push("s1", "user", "旧消息")
    assert merger.has_pending("s1", "user") is True

    should_supersede = await merger.should_supersede("s1", "user")
    assert should_supersede is True


@pytest.mark.asyncio
async def test_no_supersede_after_commit():
    """提交后 should_supersede 应返回 False"""
    from agent.story.merger import MessageMerger

    merger = MessageMerger(merge_window_ms=2000)
    await merger.push("s1", "user", "消息")
    await asyncio.sleep(0.01)
    merger.mark_committed("s1", "user")

    should_supersede = await merger.should_supersede("s1", "user")
    assert should_supersede is False


@pytest.mark.asyncio
async def test_expiration_check():
    """过期结果不应落库"""
    from agent.story.merger import MessageMerger

    merger = MessageMerger(merge_window_ms=50)
    await merger.push("s1", "user", "消息")
    await asyncio.sleep(0.1)

    is_stale = await merger.is_stale("s1", "user", tolerance_ms=50)
    assert is_stale is True


@pytest.mark.asyncio
async def test_not_stale_within_tolerance():
    """窗口内不应标记为过期"""
    from agent.story.merger import MessageMerger

    merger = MessageMerger(merge_window_ms=5000)
    await merger.push("s1", "user", "消息")
    is_stale = await merger.is_stale("s1", "user", tolerance_ms=5000)
    assert is_stale is False
