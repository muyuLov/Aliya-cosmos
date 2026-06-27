"""测试 task_manager 模块的状态机、并发、取消"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from memory.task_manager import (
    QuintupleTaskManager,
    TaskStatus,
    start_task_manager,
    stop_task_manager,
    get_task_manager,
)


@pytest.fixture(autouse=True)
def reset_task_manager():
    """每个测试前重置任务管理器单例"""
    import memory.task_manager as tm

    tm._task_manager_instance = None
    yield


class TestTaskStatus:
    def test_enum_values(self):
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.RUNNING.value == "running"
        assert TaskStatus.COMPLETED.value == "completed"
        assert TaskStatus.FAILED.value == "failed"
        assert TaskStatus.CANCELLED.value == "cancelled"

    def test_enum_transitions(self):
        pending = TaskStatus.PENDING
        running = TaskStatus.RUNNING
        completed = TaskStatus.COMPLETED
        failed = TaskStatus.FAILED
        cancelled = TaskStatus.CANCELLED

        assert pending.value < running.value  # 字符串排序，仅作存在性验证
        assert completed in (TaskStatus.COMPLETED,)
        assert failed in (TaskStatus.FAILED,)
        assert cancelled in (TaskStatus.CANCELLED,)


class TestQuintupleTaskManager:
    @pytest.mark.asyncio
    async def test_initial_state(self):
        mgr = QuintupleTaskManager(max_workers=2, max_queue_size=10)
        assert not mgr.is_running
        assert mgr.max_workers == 2
        assert mgr.max_queue_size == 10
        assert mgr.task_queue is None
        assert mgr.lock is None

    @pytest.mark.asyncio
    async def test_start_stop(self):
        mgr = QuintupleTaskManager(max_workers=2, max_queue_size=10)
        assert not mgr.is_running

        await mgr.start()
        assert mgr.is_running
        assert mgr.task_queue is not None
        assert mgr.lock is not None
        assert len(mgr.worker_tasks) == 2

        await mgr.shutdown()
        assert not mgr.is_running

    @pytest.mark.asyncio
    async def test_add_and_complete_task(self):
        mgr = QuintupleTaskManager(max_workers=2, max_queue_size=10)
        await mgr.start()

        # Mock extractor to return quickly
        with patch("memory.extractor.extract_quintuples", AsyncMock(return_value=[("a", "人物", "喜欢", "b", "物品")])):
            task_id = await mgr.add_task("测试文本")
            assert task_id.startswith("extract_")

            result, error = await mgr.get_task_result(task_id, timeout=5)
            assert error is None
            assert result == [("a", "人物", "喜欢", "b", "物品")]

        await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_add_empty_text_raises(self):
        mgr = QuintupleTaskManager(max_workers=2, max_queue_size=10)
        await mgr.start()

        with pytest.raises(ValueError):
            await mgr.add_task("")

        with pytest.raises(ValueError):
            await mgr.add_task("   ")

        await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_duplicate_task_detection(self):
        mgr = QuintupleTaskManager(max_workers=2, max_queue_size=10)
        await mgr.start()

        with patch("memory.extractor.extract_quintuples", AsyncMock(return_value=[])):
            task_id_1 = await mgr.add_task("相同的文本")
            task_id_2 = await mgr.add_task("相同的文本")
            # 应该返回相同的 task_id（去重）
            assert task_id_1 == task_id_2

        await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_cancel_pending_task(self):
        mgr = QuintupleTaskManager(max_workers=2, max_queue_size=10)
        await mgr.start()

        # 创建一个会阻塞的任务（用 sleep 模拟）
        async def slow_extract(text):
            await asyncio.sleep(100)
            return []

        with patch("memory.extractor.extract_quintuples", slow_extract):
            task_id = await mgr.add_task("等待取消的文本")

            # 任务仍在 pending 或 running
            status = mgr.get_task_status(task_id)
            assert status is not None
            assert status["status"] in ("pending", "running")

            # 取消任务
            cancelled = await mgr.cancel_task(task_id)
            assert cancelled

            # 确认状态
            status = mgr.get_task_status(task_id)
            assert status["status"] == TaskStatus.CANCELLED.value

        await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_task(self):
        mgr = QuintupleTaskManager(max_workers=2, max_queue_size=10)
        await mgr.start()

        result = await mgr.cancel_task("nonexistent_id")
        assert not result

        await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_get_task_result_nonexistent(self):
        mgr = QuintupleTaskManager(max_workers=2, max_queue_size=10)
        await mgr.start()

        result, error = await mgr.get_task_result("nonexistent_id")
        assert result is None
        assert "不存在" in (error or "")

        await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_stats_after_tasks(self):
        mgr = QuintupleTaskManager(max_workers=2, max_queue_size=10)
        await mgr.start()

        with patch("memory.extractor.extract_quintuples", AsyncMock(return_value=[("x", "人物", "喜欢", "y", "物品")])):
            await mgr.add_task("文本A")
            await mgr.add_task("文本B")

            # 等待任务完成
            await asyncio.sleep(1)

            stats = mgr.get_stats()
            assert stats["is_running"]
            assert stats["total_tasks"] >= 2
            assert stats["completed_tasks"] >= 2
            assert stats["failed_tasks"] == 0

        await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_failed_task_counted(self):
        mgr = QuintupleTaskManager(max_workers=2, max_queue_size=10)
        await mgr.start()

        async def failing_extract(text):
            raise RuntimeError("提取失败")

        with patch("memory.extractor.extract_quintuples", failing_extract):
            await mgr.add_task("会失败的文本")
            await asyncio.sleep(1)

            stats = mgr.get_stats()
            assert stats["failed_tasks"] >= 1

        await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_get_all_tasks(self):
        mgr = QuintupleTaskManager(max_workers=2, max_queue_size=10)
        await mgr.start()

        with patch("memory.extractor.extract_quintuples", AsyncMock(return_value=[])):
            await mgr.add_task("文本1")
            await mgr.add_task("文本2")
            await asyncio.sleep(0.5)

            all_tasks = mgr.get_all_tasks()
            assert len(all_tasks) >= 2

        await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_clear_completed_tasks(self):
        mgr = QuintupleTaskManager(max_workers=2, max_queue_size=10)
        await mgr.start()

        with patch("memory.extractor.extract_quintuples", AsyncMock(return_value=[])):
            await mgr.add_task("文本1")
            await mgr.add_task("文本2")
            await asyncio.sleep(0.5)

            removed = await mgr.clear_completed_tasks(max_age_hours=0)
            assert removed >= 2

        await mgr.shutdown()
