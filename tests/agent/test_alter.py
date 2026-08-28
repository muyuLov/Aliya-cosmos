"""Task 4.1: Alter 动态氛围阈值系统测试

验证 AlterState 状态机 + 动态阈值 + 权重生命周期 + 侧端分析不阻塞。
"""

import pytest
from datetime import datetime, timezone


def test_alter_state_create():
    """AlterState 应持有累计值、方向、权重、冷却等字段"""
    from agent.emotion.alter import AlterState

    state = AlterState()
    assert state.cumulative == 0.0
    assert state.direction == ""
    assert state.weight == 0.0


def test_alter_apply_delta_same_direction():
    """同向 delta 应增强权重"""
    from agent.emotion.alter import AlterState

    state = AlterState()
    state.apply(2, "warm")
    assert state.cumulative == 2.0
    assert state.direction == "warm"
    assert state.weight > 0

    state.apply(3, "warm")
    assert state.cumulative == 5.0
    assert state.weight > state.weight or state.weight == 1.0


def test_alter_apply_delta_opposite_direction():
    """反向 delta 应衰减权重"""
    from agent.emotion.alter import AlterState

    state = AlterState()
    state.apply(3, "warm")
    w_before = state.weight
    state.apply(-2, "cool")
    assert state.weight < w_before


def test_alter_apply_delta_cancels():
    """完全抵消的 delta 应清除方向"""
    from agent.emotion.alter import AlterState

    state = AlterState()
    state.apply(3, "warm")
    state.apply(-3, "warm")
    # 完全抵消后累计应接近 0
    assert abs(state.cumulative) < 0.01


def test_alter_dynamic_threshold():
    """阈值应受 density 影响：密度高时阈值降低"""
    from agent.emotion.alter import AlterState

    state = AlterState(base_threshold=5.0)
    threshold_normal = state.get_threshold(density=0.0)
    threshold_dense = state.get_threshold(density=0.8)
    assert threshold_dense < threshold_normal


def test_alter_should_trigger():
    """累计值达到阈值时 should_trigger 应返回 True"""
    from agent.emotion.alter import AlterState

    state = AlterState(base_threshold=3.0)
    assert state.should_trigger(density=0.0) is False

    state.apply(4, "warm")
    assert state.should_trigger(density=0.0) is True


def test_alter_cooldown_prevents_retrigger():
    """冷却期间不应重复触发"""
    from agent.emotion.alter import AlterState

    state = AlterState(base_threshold=2.0, cooldown_seconds=60)
    state.apply(5, "warm")
    assert state.should_trigger(density=0.0) is True
    state.mark_triggered()
    # 刚触发完不应再触发
    assert state.should_trigger(density=0.0) is False


def test_alter_weight_lifecycle():
    """权重生命周期：同向增强 → 反向衰减 → 过低清除"""
    from agent.emotion.alter import AlterState

    state = AlterState()
    # 无方向时权重为 0
    assert state.weight == 0.0

    # 同向增强
    state.apply(2, "warm")
    assert state.weight > 0
    w1 = state.weight
    state.apply(2, "warm")
    assert state.weight >= w1

    # 反向衰减
    w_before_decay = state.weight
    state.apply(-1, "cool")
    assert state.weight < w_before_decay or state.weight == 0

    # 全部抵消后清除
    state.apply(-state.cumulative, "cool")
    state.update_weight_decay()
    assert state.weight == 0.0


def test_alter_to_dict():
    """to_dict 应返回完整状态"""
    from agent.emotion.alter import AlterState

    state = AlterState()
    state.apply(2, "warm")
    d = state.to_dict()
    assert "cumulative" in d
    assert "direction" in d
    assert "weight" in d
    assert "threshold" in d


def test_alter_side_analysis_does_not_block():
    """侧端分析应返回 pending 标记而非阻塞"""
    from agent.emotion.alter import AlterState

    state = AlterState()
    state.apply(10, "warm")
    # should_trigger 标记 pending 但不阻塞
    triggered = state.should_trigger(density=0.0)
    assert triggered is True
    # 标记已触发后，后续不重复触发
    state.mark_triggered()
    assert state.should_trigger(density=0.0) is False
