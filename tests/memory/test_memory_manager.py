"""测试 memory_manager 模块的缓存管理、上下文裁剪、去重逻辑"""

from __future__ import annotations

from collections import OrderedDict
from unittest.mock import AsyncMock, patch

import pytest

from memory.memory_manager import GRAGMemoryManager, _MAX_CONTEXT_CHARS


@pytest.fixture(autouse=True)
def reset_globals():
    import memory.memory_manager as mm

    mm._memory_manager_instance = None
    yield


@pytest.fixture
def manager():
    """创建一个 disabled 的 manager 用于测试工具方法"""
    with (
        patch("memory.memory_manager.get_grag_config") as mock_cfg,
        patch("memory.memory_manager.task_manager_module.get_task_manager"),
    ):
        cfg = mock_cfg.return_value
        cfg.enabled = False
        mgr = GRAGMemoryManager(ai_name="Test")
        return mgr


class TestCacheMarkDone:
    def test_cache_adds_entry(self, manager):
        manager._cache_mark_done("hash123")
        assert "hash123" in manager.extraction_cache
        assert manager.extraction_cache["hash123"] is True

    def test_cache_lru_eviction(self, manager):
        manager._max_cache_size = 3
        manager._cache_mark_done("a")
        manager._cache_mark_done("b")
        manager._cache_mark_done("c")
        assert len(manager.extraction_cache) == 3

        # 超过限制时淘汰最旧的
        manager._cache_mark_done("d")
        assert len(manager.extraction_cache) == 3
        assert "a" not in manager.extraction_cache
        assert "d" in manager.extraction_cache

    def test_cache_reorders_on_reaccess(self, manager):
        manager._max_cache_size = 3
        manager._cache_mark_done("a")
        manager._cache_mark_done("b")
        manager._cache_mark_done("c")

        # 重新访问"a"会把它移到末尾
        manager._cache_mark_done("a")
        # 然后添加"d"应该淘汰"b"而不是"a"
        manager._cache_mark_done("d")
        assert "a" in manager.extraction_cache
        assert "b" not in manager.extraction_cache


class TestTrimContextByChars:
    def test_empty_context(self, manager):
        manager.recent_context = []
        manager._trim_context_by_chars()
        assert manager.recent_context == []

    def test_within_limit(self, manager):
        texts = ["短文本"] * 5
        total = sum(len(t) for t in texts)
        assert total < _MAX_CONTEXT_CHARS
        manager.recent_context = list(texts)
        manager._trim_context_by_chars()
        assert len(manager.recent_context) == 5

    def test_exceeds_limit(self, manager):
        long_text = "a" * (_MAX_CONTEXT_CHARS // 2 + 100)
        manager.recent_context = [long_text, long_text]  # 总长 > limit
        manager._trim_context_by_chars()
        total = sum(len(s) for s in manager.recent_context)
        assert total <= _MAX_CONTEXT_CHARS

    def test_single_long_entry(self, manager):
        """单条超长文本也应被裁剪"""
        long_text = "a" * (_MAX_CONTEXT_CHARS + 1000)
        manager.recent_context = [long_text]
        manager._trim_context_by_chars()
        assert manager.recent_context == []  # 裁剪到 0 条

    def test_trim_preserves_latest(self, manager):
        """裁剪应该从头移除，保留最新（尾部）"""
        short = "短"
        long_text = "a" * (_MAX_CONTEXT_CHARS // 2)
        manager.recent_context = [long_text, long_text, short]
        manager._trim_context_by_chars()
        assert manager.recent_context[-1] == short


class TestHashText:
    def test_hash_consistency(self, manager):
        h1 = manager._hash_text("测试文本")
        h2 = manager._hash_text("测试文本")
        assert h1 == h2

    def test_hash_different(self, manager):
        h1 = manager._hash_text("文本A")
        h2 = manager._hash_text("文本B")
        assert h1 != h2

    def test_hash_not_empty(self, manager):
        h = manager._hash_text("")
        assert isinstance(h, str)
        assert len(h) > 0


class TestSubmitExtractionTask:
    @pytest.mark.asyncio
    async def test_skips_cached_text(self, manager):
        manager.enabled = True
        manager.extraction_cache["known_hash"] = True

        with (
            patch.object(manager, "_hash_text", return_value="known_hash"),
            patch("memory.memory_manager.task_manager_module.get_task_manager") as mock_tm,
        ):
            mgr = mock_tm.return_value
            mgr.is_running = True

            await manager._submit_extraction_task("已知文本")
            # 不应调用 add_task
            mgr.add_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_inflight_text(self, manager):
        manager.enabled = True
        manager._inflight_hashes.add("inflight_hash")

        with (
            patch.object(manager, "_hash_text", return_value="inflight_hash"),
            patch("memory.memory_manager.task_manager_module.get_task_manager") as mock_tm,
        ):
            mgr = mock_tm.return_value
            mgr.is_running = True

            await manager._submit_extraction_task("进行中的文本")
            mgr.add_task.assert_not_called()
            # inflight_hash 应仍在 _inflight_hashes 中
            assert "inflight_hash" in manager._inflight_hashes

    @pytest.mark.asyncio
    async def test_submit_adds_inflight(self, manager):
        manager.enabled = True

        with (
            patch.object(manager, "_hash_text", return_value="new_hash"),
            patch("memory.memory_manager.task_manager_module.get_task_manager") as mock_tm,
            patch.object(manager, "_extract_and_store_sync", AsyncMock(return_value=True)),
        ):
            mgr = mock_tm.return_value
            mgr.is_running = True
            mgr.add_task = AsyncMock(return_value="task_123")

            await manager._submit_extraction_task("新文本")
            assert "new_hash" in manager._inflight_hashes
            mgr.add_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_on_failure(self, manager):
        manager.enabled = True

        with (
            patch.object(manager, "_hash_text", return_value="fail_hash"),
            patch("memory.memory_manager.task_manager_module.get_task_manager") as mock_tm,
            patch.object(manager, "_extract_and_store_sync", AsyncMock(return_value=True)) as mock_fallback,
        ):
            mgr = mock_tm.return_value
            mgr.is_running = True
            mgr.add_task = AsyncMock(side_effect=RuntimeError("队列满"))

            await manager._submit_extraction_task("失败文本")
            # inflight_hash 应被清理
            assert "fail_hash" not in manager._inflight_hashes
            # 应调用回退
            mock_fallback.assert_called_once()


class TestInflightDedup:
    @pytest.mark.asyncio
    async def test_double_submit_same_text(self, manager):
        """短时间内两次提交相同文本，第二次应被 inflight 拦截"""
        manager.enabled = True
        call_count = 0

        async def fake_add_task(text, **kwargs):
            nonlocal call_count
            call_count += 1
            return f"task_{call_count}"

        with (
            patch.object(manager, "_hash_text", return_value="same_hash"),
            patch("memory.memory_manager.task_manager_module.get_task_manager") as mock_tm,
            patch.object(manager, "_extract_and_store_sync", AsyncMock(return_value=True)),
        ):
            mgr = mock_tm.return_value
            mgr.is_running = True
            mgr.add_task = fake_add_task

            await manager._submit_extraction_task("相同文本")
            await manager._submit_extraction_task("相同文本")

            # add_task 应只被调用一次（第二次被 inflight 拦截）
            assert call_count == 1


class TestClearMemory:
    @pytest.mark.asyncio
    async def test_clears_inflight_hashes(self, manager):
        manager.enabled = True
        manager._inflight_hashes.add("hash1")
        manager._inflight_hashes.add("hash2")
        manager.recent_context.append("一些内容")
        manager.extraction_cache["hash3"] = True

        with (
            patch("memory.memory_manager.task_manager_module.get_task_manager"),
            patch("memory.memory_manager.graph.clear_all_quintuples_async", AsyncMock(return_value=True)),
        ):
            result = await manager.clear_memory()
            assert result is True
            assert len(manager._inflight_hashes) == 0
            assert len(manager.recent_context) == 0
            assert len(manager.extraction_cache) == 0


class TestGetMemoryStats:
    def test_init_error_in_stats(self, manager):
        manager._init_error = "连接失败"
        manager.enabled = True

        with (
            patch("memory.memory_manager.task_manager_module.get_task_manager") as mock_tm,
            patch("memory.memory_manager.graph.get_graph_stats", return_value={}),
        ):
            mgr = mock_tm.return_value
            mgr.get_stats.return_value = {"total_tasks": 0}

            stats = manager.get_memory_stats()
            assert stats["enabled"]
            assert stats["init_error"] == "连接失败"
