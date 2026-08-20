"""测试 graph 模块的工具函数（无需 Neo4j 连接）"""

from __future__ import annotations

from core.memory.graph import (
    _REL_TYPE_PATTERN,
    _TIMELINE_OFFSET_YEARS,
    _build_memory_props,
    _shift_timeline_date,
)


class TestRelTypePattern:
    def test_valid_chinese(self):
        assert _REL_TYPE_PATTERN.match("工作于")
        assert _REL_TYPE_PATTERN.match("居住在")
        assert _REL_TYPE_PATTERN.match("喜欢")

    def test_valid_english(self):
        assert _REL_TYPE_PATTERN.match("WORKS_AT")
        assert _REL_TYPE_PATTERN.match("lives-in")
        assert _REL_TYPE_PATTERN.match("IS_FRIEND_OF")

    def test_valid_mixed(self):
        assert _REL_TYPE_PATTERN.match("工作在Google")
        assert _REL_TYPE_PATTERN.match("study-at_清华")

    def test_invalid_chars(self):
        assert not _REL_TYPE_PATTERN.match("工作@公司")
        assert not _REL_TYPE_PATTERN.match("喜欢！跑步")
        assert not _REL_TYPE_PATTERN.match("has space")

    def test_empty(self):
        assert not _REL_TYPE_PATTERN.match("")

    def test_special_chars_rejected(self):
        assert not _REL_TYPE_PATTERN.match("a.b")
        assert not _REL_TYPE_PATTERN.match("a[b]")
        assert not _REL_TYPE_PATTERN.match("a(b)")
        assert not _REL_TYPE_PATTERN.match("a+b")


class TestBuildMemoryProps:
    """五层记忆属性构建（挂载到 Entity 节点的 memory_* map）"""

    def test_none_or_empty_returns_empty(self):
        assert _build_memory_props(None, now=1.0) == {}
        assert _build_memory_props({}, now=1.0) == {}

    def test_only_nonempty_keys(self):
        props = _build_memory_props(
            {"layers": "semantic", "confidence": 0.8}, now=123.0
        )
        assert props == {
            "memory_layers": "semantic",
            "memory_confidence": 0.8,
            "memory_updated_at": 123.0,
        }

    def test_zero_values_omitted(self):
        props = _build_memory_props(
            {
                "layers": "",
                "importance": 0.0,
                "confidence": 0.0,
                "success_rate": 0.0,
                "attention_weight": 0.0,
                "access_count": 0,
                "heat": 0.0,
            },
            now=1.0,
        )
        assert props == {}

    def test_full_attrs(self):
        props = _build_memory_props(
            {
                "layers": "episodic;semantic",
                "importance": 0.8,
                "confidence": 0.6,
                "success_rate": 0.5,
                "attention_weight": 1.0,
                "access_count": 3,
                "heat": 0.4,
            },
            now=9.0,
        )
        assert props["memory_layers"] == "episodic;semantic"
        assert props["memory_importance"] == 0.8
        assert props["memory_confidence"] == 0.6
        assert props["memory_success_rate"] == 0.5
        assert props["memory_attention_weight"] == 1.0
        assert props["memory_access_count"] == 3
        assert props["memory_heat"] == 0.4
        assert props["memory_updated_at"] == 9.0


class TestMemoryNodeOperations:
    """节点遗忘操作：衰减 / 清理 / 查询"""

    def test_decay_memory_nodes_counts_and_sets(self):
        from unittest.mock import MagicMock

        from core.memory.graph import GraphStore

        store = GraphStore()
        graph = MagicMock()
        graph.run.return_value.data.return_value = [{"cnt": 2}]
        store._graph = graph  # 注入 mock 连接

        count = store.decay_memory_nodes(now=1000.0)
        assert count == 2
        # 两次查询：count 统计 + SET 衰减
        assert graph.run.call_count == 2
        decay_query = graph.run.call_args_list[1][0][0]
        assert "memory_confidence" in decay_query
        assert "exp(-0.6931 * age" in decay_query

    def test_decay_no_nodes_returns_zero(self):
        from unittest.mock import MagicMock

        from core.memory.graph import GraphStore

        store = GraphStore()
        graph = MagicMock()
        graph.run.return_value.data.return_value = []
        store._graph = graph

        assert store.decay_memory_nodes() == 0
        assert graph.run.call_count == 1  # 仅 count 查询

    def test_prune_removes_memory_props_by_default(self):
        from unittest.mock import MagicMock

        from core.memory.graph import GraphStore

        store = GraphStore()
        graph = MagicMock()
        graph.run.return_value.data.return_value = [
            {"name": "咖啡", "day_date": "2026-08-20", "timeline": "aliya"}
        ]
        store._graph = graph

        count = store.prune_memory_nodes()
        assert count == 1
        # 选择查询 + 清理查询
        assert graph.run.call_count == 2
        remove_query = graph.run.call_args_list[1][0][0]
        assert "REMOVE e.memory_layers" in remove_query
        assert "DETACH DELETE" not in remove_query

    def test_prune_can_delete_entity(self):
        from unittest.mock import MagicMock

        from core.memory.graph import GraphStore

        store = GraphStore()
        graph = MagicMock()
        graph.run.return_value.data.return_value = [
            {"name": "咖啡", "day_date": "2026-08-20", "timeline": "aliya"}
        ]
        store._graph = graph

        count = store.prune_memory_nodes(delete_entity=True)
        assert count == 1
        delete_query = graph.run.call_args_list[1][0][0]
        assert "DETACH DELETE e" in delete_query

    def test_query_memory_nodes_converts_values(self):
        from unittest.mock import MagicMock

        from core.memory.graph import GraphStore

        store = GraphStore()
        graph = MagicMock()
        graph.run.return_value.data.return_value = [
            {
                "name": "咖啡", "day_date": "2026-08-20", "timeline": "aliya",
                "layers": "semantic", "importance": 0.8, "confidence": 0.9,
                "success_rate": None, "attention_weight": None,
                "access_count": 5, "heat": 0.4,
            }
        ]
        store._graph = graph

        nodes = store.query_memory_nodes()
        assert len(nodes) == 1
        node = nodes[0]
        assert node["name"] == "咖啡"
        assert node["importance"] == 0.8
        assert node["confidence"] == 0.9
        assert node["success_rate"] == 0.0
        assert node["attention_weight"] == 0.0
        assert node["access_count"] == 5
        assert node["heat"] == 0.4


class TestCleanupOrphanEntities:
    """孤立 Entity 节点清理（无任何关系：入边/出边/[:ON_DAY]）"""

    def test_cleanup_orphans_zero(self):
        from unittest.mock import MagicMock

        from core.memory.graph import GraphStore

        store = GraphStore()
        graph = MagicMock()
        graph.run.return_value.data.return_value = [{"cnt": 0}]
        store._graph = graph

        assert store.cleanup_orphan_entities() == 0
        assert graph.run.call_count == 1  # 仅 count 查询，不删任何节点

    def test_cleanup_orphans_executes_detach_delete(self):
        from unittest.mock import MagicMock

        from core.memory.graph import GraphStore

        store = GraphStore()
        graph = MagicMock()
        graph.run.return_value.data.return_value = [{"cnt": 5}]
        store._graph = graph

        assert store.cleanup_orphan_entities() == 5
        # 第二次 run 调用应为 DETACH DELETE
        delete_query = graph.run.call_args_list[1][0][0]
        assert "DETACH DELETE e" in delete_query
        assert "NOT (e)--()" in delete_query


class TestTouchDay:
    """无五元组时确保时间链节点存在（防断链）"""

    def test_touch_day_composite_timeline(self):
        from unittest.mock import MagicMock

        from core.memory.graph import GraphStore

        store = GraphStore()
        graph = MagicMock()
        graph.run.return_value.data.return_value = []
        store._graph = graph

        assert store.touch_day("2025-07-11", "aliya|user") is True
        # 每条链调用 _ensure_day_node（MERGE Day）+ _link_next_day
        assert graph.run.call_count >= 2

    def test_touch_day_empty_args_false(self):
        from core.memory.graph import GraphStore

        store = GraphStore()
        assert store.touch_day("", "aliya") is False
        assert store.touch_day("2025-07-11", "") is False

    def test_touch_day_no_graph_returns_false(self):
        from unittest.mock import patch

        from core.memory.graph import GraphStore, GraphConnectionError

        store = GraphStore()
        with patch.object(
            store, "_get_graph", side_effect=GraphConnectionError("连接失败")
        ):
            assert store.touch_day("2025-07-11", "aliya") is False

    def test_store_quintuples_empty_groups_still_touches_day(self):
        """五元组全被过滤时仍创建 Day 节点（时间链不因空提取而断）。"""
        from unittest.mock import MagicMock

        from core.memory.graph import GraphStore

        store = GraphStore()
        graph = MagicMock()
        graph.begin.return_value = MagicMock()
        graph.run.return_value.data.return_value = []
        store._graph = graph

        # 非法关系类型会被过滤 → groups 为空
        success = store.store_quintuples(
            [("a", "人物", "非法关系类型!!", "b", "物品")],
            day_date="2025-07-11",
            timeline="aliya",
        )
        assert success is True
        # groups 为空 → 无 UNWIND 写入，但调用了 Day 节点创建 + 串联
        day_queries = [c[0][0] for c in graph.run.call_args_list]
        assert any("MERGE (d:Day" in q for q in day_queries)


class TestShiftTimelineDate:
    """时间链日期偏移（幂等性：已偏移日期不重复 +1000）"""

    def test_user_timeline_unchanged(self):
        assert _shift_timeline_date("2026-07-11", "user") == "2026-07-11"

    def test_aliya_timeline_shifted(self):
        assert _shift_timeline_date("2026-07-11", "aliya") == "3026-07-11"

    def test_aliya_idempotent_on_pre_shifted(self):
        """调用方已预偏移（aliya 时间线年份）时不再重复 +1000，避免时间链重复。"""
        pre_shifted = f"{2026 + _TIMELINE_OFFSET_YEARS}-07-11"
        assert _shift_timeline_date(pre_shifted, "aliya") == pre_shifted

    def test_empty_date_unchanged(self):
        assert _shift_timeline_date("", "aliya") == ""
        assert _shift_timeline_date("", "user") == ""

    def test_invalid_date_unchanged(self):
        assert _shift_timeline_date("not-a-date", "aliya") == "not-a-date"

    def test_leap_day_kept_when_next_is_leap(self):
        """2/29 加 1000 年后仍为闰年时保持 2/29（2024→3024 均闰年）。"""
        assert _shift_timeline_date("2024-02-29", "aliya") == "3024-02-29"

    def test_case_insensitive_timeline(self):
        assert _shift_timeline_date("2026-07-11", "Aliya") == "3026-07-11"


class TestStoreQuintuplesMemoryProps:
    """store_quintuples 把五层记忆属性嵌入 items 挂载到 Entity 节点"""

    def test_memory_props_embedded_in_items(self):
        from unittest.mock import MagicMock

        from core.memory.graph import GraphStore

        store = GraphStore()
        graph = MagicMock()
        graph.begin.return_value = MagicMock()
        graph.run.return_value.data.return_value = []
        store._graph = graph  # 注入 mock 连接，绕过真实 Neo4j

        success = store.store_quintuples(
            [("咖啡", "物品", "属于", "饮品", "概念")],
            day_date="2026-08-20",
            timeline="aliya",
            memory_attrs_by_entity={
                "咖啡": {
                    "layers": "semantic;episodic",
                    "confidence": 0.8,
                    "importance": 0.6,
                },
                "饮品": {"layers": "semantic"},
            },
        )
        assert success

        items = graph.begin.return_value.run.call_args.kwargs["items"]
        head_attrs = items[0][5]
        tail_attrs = items[0][6]
        # head 实体命中两层 + 置信度 + 重要性
        assert head_attrs["memory_layers"] == "semantic;episodic"
        assert head_attrs["memory_confidence"] == 0.8
        assert head_attrs["memory_importance"] == 0.6
        assert "memory_updated_at" in head_attrs
        # tail 实体仅命中层信息
        assert tail_attrs["memory_layers"] == "semantic"
        assert tail_attrs.get("memory_confidence") is None
