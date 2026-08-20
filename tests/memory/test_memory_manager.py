"""测试 memory_manager 模块的缓存管理、上下文裁剪、去重逻辑"""

from __future__ import annotations

from collections import deque
from unittest.mock import AsyncMock, patch

import pytest

from core.memory.memory_manager import (
    GRAGMemoryManager,
    _FORGET_CHECK_INTERVAL,
    _FORGET_MAX_INTERVAL,
    _MAX_CONTEXT_CHARS,
)


@pytest.fixture(autouse=True)
def reset_globals():
    import core.memory.memory_manager as mm

    mm._memory_manager_instance = None
    yield


@pytest.fixture
def manager():
    """创建一个 disabled 的 manager 用于测试工具方法"""
    with (
        patch("core.memory.memory_manager.get_grag_config") as mock_cfg,
        patch("core.memory.memory_manager.task_manager_module.get_task_manager"),
    ):
        cfg = mock_cfg.return_value
        cfg.enabled = False
        cfg.context_length = 10
        cfg.auto_extract = True
        cfg.similarity_threshold = 0.7
        cfg.session_tracking = True
        cfg.extractor.max_retries = 2
        cfg.extractor.timeout = 30
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
        manager.recent_context = deque()
        manager._trim_context_by_chars()
        assert len(manager.recent_context) == 0

    def test_within_limit(self, manager):
        texts = deque(["短文本"] * 5)
        total = sum(len(t) for t in texts)
        assert total < _MAX_CONTEXT_CHARS
        manager.recent_context = texts
        manager._trim_context_by_chars()
        assert len(manager.recent_context) == 5

    def test_exceeds_limit(self, manager):
        long_text = "a" * (_MAX_CONTEXT_CHARS // 2 + 100)
        manager.recent_context = deque([long_text, long_text])  # 总长 > limit
        manager._context_char_count = sum(len(s) for s in manager.recent_context)
        manager._trim_context_by_chars()
        total = sum(len(s) for s in manager.recent_context)
        assert total <= _MAX_CONTEXT_CHARS

    def test_single_long_entry(self, manager):
        """单条超长文本也应被裁剪"""
        long_text = "a" * (_MAX_CONTEXT_CHARS + 1000)
        manager.recent_context = deque([long_text])
        manager._context_char_count = len(long_text)
        manager._trim_context_by_chars()
        assert len(manager.recent_context) == 0  # 裁剪到 0 条

    def test_trim_preserves_latest(self, manager):
        """裁剪应该从头移除，保留最新（尾部）"""
        short = "短"
        long_text = "a" * (_MAX_CONTEXT_CHARS // 2)
        manager.recent_context = deque([long_text, long_text, short])
        manager._context_char_count = sum(len(s) for s in manager.recent_context)
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
            patch("core.memory.memory_manager.task_manager_module.get_task_manager") as mock_tm,
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
            patch("core.memory.memory_manager.task_manager_module.get_task_manager") as mock_tm,
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
            patch("core.memory.memory_manager.task_manager_module.get_task_manager") as mock_tm,
        ):
            mgr = mock_tm.return_value
            mgr.is_running = True
            mgr.add_task = AsyncMock(return_value="task_123")

            await manager._submit_extraction_task("新文本")
            assert "new_hash" in manager._inflight_hashes
            mgr.add_task.assert_called_once()

class TestInflightDedup:
    @pytest.mark.asyncio
    async def test_double_submit_same_text(self, manager):
        """短时间内两次提交相同文本，第二次应被 inflight 拦截"""
        manager.enabled = True
        call_count = 0

        async def fake_add_task(_text, **_kwargs):
            nonlocal call_count
            call_count += 1
            return f"task_{call_count}"

        with (
            patch.object(manager, "_hash_text", return_value="same_hash"),
            patch("core.memory.memory_manager.task_manager_module.get_task_manager") as mock_tm,
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
            patch("core.memory.memory_manager.task_manager_module.get_task_manager"),
            patch("core.memory.memory_manager.graph.clear_all_quintuples_async", AsyncMock(return_value=True)),
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
            patch("core.memory.memory_manager.task_manager_module.get_task_manager") as mock_tm,
            patch("core.memory.memory_manager.graph.get_graph_stats", return_value={}),
        ):
            mgr = mock_tm.return_value
            mgr.get_stats.return_value = {"total_tasks": 0}

            stats = manager.get_memory_stats()
            assert stats["enabled"]
            assert stats["init_error"] == "连接失败"

    def test_stats_includes_hierarchical(self, manager):
        manager.enabled = True

        with (
            patch("core.memory.memory_manager.task_manager_module.get_task_manager") as mock_tm,
            patch("core.memory.memory_manager.graph.get_graph_stats", return_value={}),
        ):
            mgr = mock_tm.return_value
            mgr.get_stats.return_value = {"total_tasks": 0}

            stats = manager.get_memory_stats()
            assert "hierarchical" in stats
            assert "semantic" in stats["hierarchical"]
            assert "episodic" in stats["hierarchical"]


class TestHierarchicalIntegration:
    """五层层次化记忆与 GRAG 记忆管理器的集成行为"""

    @pytest.mark.asyncio
    async def test_add_conversation_writes_hierarchical(self, manager):
        manager.enabled = True
        manager.hierarchical._vector_enabled = False

        with (
            patch(
                "core.memory.memory_manager.task_manager_module.get_task_manager"
            ) as mock_tm,
            patch(
                "core.memory.memory_manager.graph.decay_memory_nodes_async",
                AsyncMock(return_value=0),
            ),
        ):
            mgr = mock_tm.return_value
            mgr.is_running = True
            mgr.add_task = AsyncMock(return_value="t1")

            await manager.add_conversation_memory("我喜欢喝咖啡", "好的，我记住了")

            # 工作/情景层写入；感知层为瞬态缓冲，consolidate 后已冲刷进工作层；
            # 单次对话未达巩固阈值，语义层为空
            assert len(manager.hierarchical.working) > 0
            assert len(manager.hierarchical.sensory) == 0  # 已冲刷
            assert len(manager.hierarchical.episodic) > 0
            assert len(manager.hierarchical.semantic) == 0

    @pytest.mark.asyncio
    async def test_on_task_completed_passes_memory_attrs(self, manager):
        import time as _time

        from core.memory.task_manager import ExtractionTask, TaskStatus

        manager.enabled = True
        manager.hierarchical._vector_enabled = False
        manager.hierarchical.learn_fact("用户偏好", "喜欢咖啡", confidence=0.9)

        task = ExtractionTask(
            task_id="t1",
            text="咖啡",
            text_hash="h1",
            source_text="用户：我喜欢喝咖啡",
            session_id="s1",
            day_date="2026-08-20",
            timeline="aliya",
            created_at=_time.time(),
            status=TaskStatus.COMPLETED,
            result=[("我", "人物", "喜欢", "咖啡", "物品")],
            result_categories=["偏好"],
        )
        with (
            patch(
                "core.memory.memory_manager.graph.store_quintuples_async",
                AsyncMock(return_value=True),
            ) as mock_store,
            patch("core.memory.memory_manager.task_manager_module.get_task_manager"),
        ):
            await manager._on_task_completed(task)

            kwargs = mock_store.call_args.kwargs
            assert "memory_attrs_by_entity" in kwargs
            coffee_attrs = kwargs["memory_attrs_by_entity"]["咖啡"]
            assert "semantic" in coffee_attrs["layers"]
            assert coffee_attrs["confidence"] == pytest.approx(0.9)

    @pytest.mark.asyncio
    async def test_clear_memory_resets_hierarchical(self, manager):
        manager.enabled = True
        manager.hierarchical.remember_episode("一些内容", importance=0.5)
        manager.recent_context.append("一些内容")
        manager.extraction_cache["h"] = True
        manager._conversation_count = 30
        manager._last_forgetting_at = 123.0
        manager._last_forgetting_stats = {"x": 1}

        with (
            patch("core.memory.memory_manager.task_manager_module.get_task_manager"),
            patch(
                "core.memory.memory_manager.graph.clear_all_quintuples_async",
                AsyncMock(return_value=True),
            ),
            patch(
                "core.memory.memory_manager.graph.cleanup_orphan_entities_async",
                AsyncMock(return_value=2),
            ) as mock_orphan,
        ):
            result = await manager.clear_memory()
            assert result is True
            assert len(manager.hierarchical.episodic) == 0
            assert len(manager.recent_context) == 0
            assert len(manager.extraction_cache) == 0
            # 遗忘维护状态一并重置
            assert manager._conversation_count == 0
            assert manager._last_forgetting_at == 0.0
            assert manager._last_forgetting_stats == {}
            # 孤立节点清理被调用（双重保险）
            mock_orphan.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_memory_forgetting_orchestrates(self, manager):
        """run_memory_forgetting 编排内存遗忘 + 图节点衰减/清理。"""
        import time as _time

        manager.enabled = True
        manager.hierarchical._vector_enabled = False
        manager.hierarchical.remember_episode("旧事件", importance=0.9)

        with (
            patch(
                "core.memory.memory_manager.graph.decay_memory_nodes_async",
                AsyncMock(return_value=3),
            ) as mock_decay,
            patch(
                "core.memory.memory_manager.graph.prune_memory_nodes_async",
                AsyncMock(return_value=1),
            ) as mock_prune,
            patch(
                "core.memory.memory_manager.graph.cleanup_orphan_entities_async",
                AsyncMock(return_value=2),
            ) as mock_orphan,
        ):
            result = await manager.run_memory_forgetting()

            mock_decay.assert_awaited_once()
            mock_prune.assert_awaited_once()
            mock_orphan.assert_awaited_once()  # 自动清理孤立节点
            assert result["graph"]["decayed_nodes"] == 3
            assert result["graph"]["pruned_nodes"] == 1
            assert result["graph"]["orphans_removed"] == 2
            assert "in_memory" in result
            assert manager._last_forgetting_at > 0
            assert manager._last_forgetting_at <= _time.time()

    @pytest.mark.asyncio
    async def test_run_memory_forgetting_skips_nodes(self, manager):
        """可跳过图节点操作，仅执行内存层遗忘。"""
        manager.enabled = True
        with (
            patch(
                "core.memory.memory_manager.graph.decay_memory_nodes_async",
                AsyncMock(),
            ) as mock_decay,
            patch(
                "core.memory.memory_manager.graph.prune_memory_nodes_async",
                AsyncMock(),
            ) as mock_prune,
            patch(
                "core.memory.memory_manager.graph.cleanup_orphan_entities_async",
                AsyncMock(),
            ) as mock_orphan,
        ):
            result = await manager.run_memory_forgetting(
                decay_nodes=False, prune_nodes=False, cleanup_orphans=False
            )
            mock_decay.assert_not_awaited()
            mock_prune.assert_not_awaited()
            mock_orphan.assert_not_awaited()
            assert result["graph"] == {}

    @pytest.mark.asyncio
    async def test_default_writes_dual_timelines(self, manager):
        """不传 timeline 时默认双写 aliya|user 双平行时间链（记忆同时属于千年后 Aliya 与当下用户）。"""
        manager.enabled = True
        manager.hierarchical._vector_enabled = False

        with (
            patch(
                "core.memory.memory_manager.task_manager_module.get_task_manager"
            ) as mock_tm,
            patch(
                "core.memory.memory_manager.graph.decay_memory_nodes_async",
                AsyncMock(return_value=0),
            ),
            patch.object(
                manager, "_submit_extraction_task", AsyncMock()
            ) as mock_submit,
        ):
            mgr = mock_tm.return_value
            mgr.is_running = True
            mgr.add_task = AsyncMock(return_value="t1")

            await manager.add_conversation_memory("我喜欢喝咖啡", "好的，我记住了")

            # 默认应提交 aliya + user 两条链（_submit_extraction_task 按位置传参：
            # args = (text, session_id, day_date, timeline)）
            assert mock_submit.await_count == 2
            timelines = [c.args[3] for c in mock_submit.await_args_list]
            assert timelines == ["aliya", "user"]
            # aliya 链只提取 AI 发言，user 链只提取用户发言
            aliya_text = mock_submit.await_args_list[0].args[0]
            user_text = mock_submit.await_args_list[1].args[0]
            assert "好的，我记住了" in aliya_text
            assert "我喜欢喝咖啡" in user_text

    @pytest.mark.asyncio
    async def test_explicit_single_timeline_not_duplicated(self, manager):
        """显式只传单条链时不重复写入（如 timeline="user" 只写 user）。"""
        manager.enabled = True
        manager.hierarchical._vector_enabled = False

        with (
            patch(
                "core.memory.memory_manager.task_manager_module.get_task_manager"
            ) as mock_tm,
            patch(
                "core.memory.memory_manager.graph.decay_memory_nodes_async",
                AsyncMock(return_value=0),
            ),
            patch.object(
                manager, "_submit_extraction_task", AsyncMock()
            ) as mock_submit,
        ):
            mgr = mock_tm.return_value
            mgr.is_running = True
            mgr.add_task = AsyncMock(return_value="t1")

            await manager.add_conversation_memory(
                "你好", "你好呀", timeline="user"
            )

            assert mock_submit.await_count == 1
            assert mock_submit.await_args_list[0].args[3] == "user"

    @pytest.mark.asyncio
    async def test_forgetting_triggered_by_conversation_count(self, manager):
        """对话计数达到间隔时自动触发内存层遗忘。"""
        import time as _time

        manager.enabled = True
        manager.hierarchical._vector_enabled = False
        manager._conversation_count = _FORGET_CHECK_INTERVAL - 1  # 即将触发
        manager._last_forgetting_at = _time.time()  # 刚维护过，排除时间驱动

        with (
            patch(
                "core.memory.memory_manager.task_manager_module.get_task_manager"
            ) as mock_tm,
            patch(
                "core.memory.memory_manager.graph.decay_memory_nodes_async",
                AsyncMock(return_value=0),
            ) as mock_decay,
            patch(
                "core.memory.memory_manager.graph.cleanup_orphan_entities_async",
                AsyncMock(return_value=0),
            ) as mock_orphan,
            patch.object(
                manager.hierarchical,
                "apply_forgetting",
                wraps=manager.hierarchical.apply_forgetting,
            ) as mock_forget,
        ):
            mgr = mock_tm.return_value
            mgr.is_running = True
            mgr.add_task = AsyncMock(return_value="t1")

            await manager.add_conversation_memory("你好", "你好呀")

            mock_forget.assert_called_once()
            mock_decay.assert_awaited_once()  # 自动路径联动图节点衰减
            mock_orphan.assert_awaited_once()  # 自动清理孤立节点
            assert manager._last_forgetting_at > 0
            assert manager._conversation_count == _FORGET_CHECK_INTERVAL

    @pytest.mark.asyncio
    async def test_forgetting_triggered_by_time_elapsed(self, manager):
        """对话计数未达到时，距上次维护超时也会触发遗忘。"""
        import time as _time

        manager.enabled = True
        manager.hierarchical._vector_enabled = False
        manager._conversation_count = 1  # 远未达到计数阈值
        manager._last_forgetting_at = _time.time() - _FORGET_MAX_INTERVAL - 1  # 超过 24h

        with (
            patch(
                "core.memory.memory_manager.task_manager_module.get_task_manager"
            ) as mock_tm,
            patch(
                "core.memory.memory_manager.graph.decay_memory_nodes_async",
                AsyncMock(return_value=0),
            ) as mock_decay,
            patch(
                "core.memory.memory_manager.graph.cleanup_orphan_entities_async",
                AsyncMock(return_value=0),
            ) as mock_orphan,
            patch.object(
                manager.hierarchical,
                "apply_forgetting",
                wraps=manager.hierarchical.apply_forgetting,
            ) as mock_forget,
        ):
            mgr = mock_tm.return_value
            mgr.is_running = True
            mgr.add_task = AsyncMock(return_value="t1")

            await manager.add_conversation_memory("你好", "你好呀")

            mock_forget.assert_called_once()
            mock_decay.assert_awaited_once()
            mock_orphan.assert_awaited_once()
            assert manager._last_forgetting_stats["graph"]["decayed_nodes"] == 0

    @pytest.mark.asyncio
    async def test_forgetting_not_triggered_when_fresh(self, manager):
        """计数未到且距上次维护未超时 → 不触发遗忘。"""
        import time as _time

        manager.enabled = True
        manager.hierarchical._vector_enabled = False
        manager._conversation_count = 1
        manager._last_forgetting_at = _time.time()  # 刚维护过

        with (
            patch(
                "core.memory.memory_manager.task_manager_module.get_task_manager"
            ) as mock_tm,
            patch(
                "core.memory.memory_manager.graph.decay_memory_nodes_async",
                AsyncMock(return_value=0),
            ) as mock_decay,
            patch.object(
                manager.hierarchical,
                "apply_forgetting",
                wraps=manager.hierarchical.apply_forgetting,
            ) as mock_forget,
        ):
            mgr = mock_tm.return_value
            mgr.is_running = True
            mgr.add_task = AsyncMock(return_value="t1")

            await manager.add_conversation_memory("你好", "你好呀")

            mock_forget.assert_not_called()
            mock_decay.assert_not_awaited()
            assert manager._conversation_count == 2

    @pytest.mark.asyncio
    async def test_forgetting_first_conversation_only_init(self, manager):
        """首次对话不执行空维护，仅初始化时间基准（避免空记忆无意义衰减）。"""
        import time as _time

        manager.enabled = True
        manager.hierarchical._vector_enabled = False
        manager._conversation_count = 1  # 首次对话
        assert manager._last_forgetting_at == 0.0

        with (
            patch(
                "core.memory.memory_manager.task_manager_module.get_task_manager"
            ) as mock_tm,
            patch(
                "core.memory.memory_manager.graph.decay_memory_nodes_async",
                AsyncMock(return_value=0),
            ) as mock_decay,
            patch.object(
                manager.hierarchical,
                "apply_forgetting",
                wraps=manager.hierarchical.apply_forgetting,
            ) as mock_forget,
        ):
            mgr = mock_tm.return_value
            mgr.is_running = True
            mgr.add_task = AsyncMock(return_value="t1")

            await manager.add_conversation_memory("你好", "你好呀")

            mock_forget.assert_not_called()
            mock_decay.assert_not_awaited()
            # 时间基准已初始化，后续对话才可能触发遗忘
            assert manager._last_forgetting_at > 0
            assert manager._last_forgetting_at <= _time.time()
            assert manager._last_forgetting_stats == {}

    @pytest.mark.asyncio
    async def test_on_task_completed_empty_result_touches_day(self, manager):
        """提取结果为 0 个五元组时，仍确保时间链节点存在（防断链）。"""
        import time as _time

        from core.memory.task_manager import ExtractionTask, TaskStatus

        manager.enabled = True
        task = ExtractionTask(
            task_id="t_empty",
            text="",
            text_hash="h_empty",
            source_text="Aliya: 嗯嗯",
            session_id="s1",
            day_date="2025-07-11",
            timeline="aliya",
            created_at=_time.time(),
            status=TaskStatus.COMPLETED,
            result=[],
            result_categories=[],
        )
        with (
            patch(
                "core.memory.memory_manager.graph.touch_day_async",
                AsyncMock(return_value=True),
            ) as mock_touch,
            patch(
                "core.memory.memory_manager.graph.store_quintuples_async",
                AsyncMock(return_value=True),
            ) as mock_store,
            patch("core.memory.memory_manager.task_manager_module.get_task_manager"),
        ):
            await manager._on_task_completed(task)

            mock_touch.assert_awaited_once_with("2025-07-11", "aliya")
            mock_store.assert_not_awaited()  # 无五元组不落库

    @pytest.mark.asyncio
    async def test_on_task_completed_empty_result_no_day_skipped(self, manager):
        """无日期时，空提取结果不调用 touch_day。"""
        import time as _time

        from core.memory.task_manager import ExtractionTask, TaskStatus

        manager.enabled = True
        task = ExtractionTask(
            task_id="t_empty2",
            text="",
            text_hash="h_empty2",
            source_text="Aliya: 嗯嗯",
            session_id="s1",
            day_date="",
            timeline="aliya",
            created_at=_time.time(),
            status=TaskStatus.COMPLETED,
            result=[],
            result_categories=[],
        )
        with (
            patch(
                "core.memory.memory_manager.graph.touch_day_async",
                AsyncMock(return_value=True),
            ) as mock_touch,
            patch(
                "core.memory.memory_manager.graph.store_quintuples_async",
                AsyncMock(return_value=True),
            ) as mock_store,
            patch("core.memory.memory_manager.task_manager_module.get_task_manager"),
        ):
            await manager._on_task_completed(task)

            mock_touch.assert_not_awaited()
            mock_store.assert_not_awaited()
