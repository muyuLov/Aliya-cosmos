"""Task 4.3b: 后台调度器重写测试

验证三来源调度（自动推进/到期 intent/proactive-check）。
"""

import pytest


@pytest.mark.asyncio
async def test_scheduler_creates():
    """调度器应能创建并持有三来源配置"""
    from agent.proactive.scheduler import NarrativeScheduler

    scheduler = NarrativeScheduler()
    assert scheduler is not None
    assert hasattr(scheduler, "tick")


@pytest.mark.asyncio
async def test_scheduler_tick_returns_empty_when_nothing_due():
    """无到期任务时 tick 应返回空列表"""
    from agent.proactive.scheduler import NarrativeScheduler

    scheduler = NarrativeScheduler()
    events = await scheduler.tick()
    assert isinstance(events, list)
    assert len(events) == 0


@pytest.mark.asyncio
async def test_scheduler_due_intents():
    """到期 intent 应被 tick 发现"""
    from agent.proactive.scheduler import NarrativeScheduler
    from datetime import datetime, timezone, timedelta

    scheduler = NarrativeScheduler()
    # 注入一个已到期的 intent
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    scheduler.add_intent(
        intent_id="i1",
        summary="提醒用户喝水",
        participant_id="user",
        not_before=past,
    )
    events = await scheduler.tick()
    assert len(events) >= 1
    assert any(e["type"] == "intent_due" for e in events)


@pytest.mark.asyncio
async def test_scheduler_auto_advance():
    """自动推进事件应在满足条件时触发"""
    from agent.proactive.scheduler import NarrativeScheduler

    scheduler = NarrativeScheduler(auto_advance_enabled=True)
    scheduler.set_last_advance_at(0)  # 很久之前
    scheduler.set_advance_interval_minutes(1)  # 1 分钟间隔

    events = await scheduler.tick()
    assert len(events) >= 1
    assert any(e["type"] == "auto_advance" for e in events)


@pytest.mark.asyncio
async def test_scheduler_proactive_check():
    """proactive-check 事件在有 pending 时触发"""
    from agent.proactive.scheduler import NarrativeScheduler

    scheduler = NarrativeScheduler()
    scheduler.add_proactive_check("s1", "user", "长时间未联系")
    events = await scheduler.tick()
    assert len(events) >= 1
    assert any(e["type"] == "proactive_check" for e in events)
