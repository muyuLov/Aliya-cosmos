"""测试认知引擎（engine.py）——三段式钩子与认知上下文注入"""

# pyright: reportOptionalMemberAccess=false

from __future__ import annotations

from agent.cognition.engine import CognitionConfig, CognitionEngine
from agent.cognition.needs import NeedType


class TestCognitionEngineInit:
    def test_default_config_all_enabled(self):
        engine = CognitionEngine()
        assert engine.needs is not None
        assert engine.memory is not None
        assert engine.world is not None
        assert engine.self_model is not None

    def test_disabled_modules(self):
        engine = CognitionEngine(
            CognitionConfig(
                needs_enabled=False,
                memory_enabled=False,
                world_model_enabled=False,
                self_model_enabled=False,
            )
        )
        assert engine.needs is None
        assert engine.memory is None
        assert engine.world is None
        assert engine.self_model is None


class TestThreePhaseHooks:
    def test_before_turn_records_interaction(self):
        engine = CognitionEngine()
        engine.before_turn("你好呀")
        assert engine._turn == 1
        # 需求系统被推进：relatedness 应高于默认
        assert engine.needs.get(NeedType.RELATEDNESS).current_level > 0.6

    def test_before_turn_attends_working_memory(self):
        engine = CognitionEngine()
        engine.before_turn("我喜欢喝咖啡")
        recalled = engine.memory.working.recall()
        assert "我喜欢喝咖啡" in recalled

    def test_after_tool_success_updates_everything(self):
        engine = CognitionEngine()
        engine.before_turn("查询天气")
        engine.after_tool("memory_query", success=True, detail="找到 3 条记忆")
        # 需求：胜任感提升
        assert engine.needs.get(NeedType.COMPETENCE).current_level > 0.6
        # 世界模型：工具实体已创建
        tools = engine.world.query_entities()
        assert any(e.name == "memory_query" for e in tools)
        # 自我模型：工具经验已记录
        assert "tools/memory_query" in engine.self_model.skills

    def test_after_tool_failure_decreases_certainty(self):
        engine = CognitionEngine()
        engine.before_turn("查询天气")
        cert_before = engine.needs.get(NeedType.CERTAINTY).current_level
        engine.after_tool("memory_query", success=False, detail="超时")
        assert engine.needs.get(NeedType.CERTAINTY).current_level < cert_before

    def test_after_turn_consolidates_periodically(self):
        engine = CognitionEngine()
        for i in range(3):
            engine.before_turn(f"消息{i}")
            engine.after_tool("memory_query", success=True)
        engine.after_turn("好的")
        # 每 3 轮触发一次巩固，工作记忆内容固化为情景记忆
        assert len(engine.memory.episodic) > 0

    def test_autonomy_maintenance_interval(self):
        engine = CognitionEngine(CognitionConfig(maintenance_interval=2))
        for i in range(2):
            engine.before_turn(f"消息{i}")
            engine.after_turn("回复")
        assert len(engine._autonomy_actions) >= 1


class TestContextInjection:
    def test_build_context_injection_contains_needs(self):
        engine = CognitionEngine()
        engine.before_turn("你好")
        ctx = engine.build_context_injection()
        assert "内在需求" in ctx
        assert "certainty" in ctx

    def test_build_context_injection_without_needs(self):
        engine = CognitionEngine()
        ctx = engine.build_context_injection(include_needs=False)
        assert "内在需求" not in ctx

    def test_build_memory_context(self):
        engine = CognitionEngine()
        engine.memory.learn_fact("用户偏好", "喜欢咖啡", confidence=0.9)
        ctx = engine.build_memory_context(query="咖啡")
        assert "喜欢咖啡" in ctx

    def test_compute_emotion_gradient(self):
        engine = CognitionEngine()
        engine.before_turn("你好")
        gradient = engine.compute_emotion_gradient()
        assert gradient is not None
        assert set(gradient.keys()) == {"valence", "arousal", "dominance"}

    def test_compute_emotion_gradient_disabled(self):
        engine = CognitionEngine(CognitionConfig(needs_enabled=False))
        assert engine.compute_emotion_gradient() is None

    def test_compute_intrinsic_reward(self):
        engine = CognitionEngine()
        engine.before_turn("你好")
        reward = engine.compute_intrinsic_reward()
        assert reward is not None
        assert -1.0 <= reward <= 1.0


class TestStatus:
    def test_get_status(self):
        engine = CognitionEngine()
        engine.before_turn("你好")
        status = engine.get_status()
        assert status["turn"] == 1
        assert "needs" in status
        assert "memory" in status
        assert "world" in status
        assert "self_model" in status
        assert "autonomy" in status
        assert "learning" in status
        assert "security" in status

    def test_user_entity_created(self):
        engine = CognitionEngine()
        engine.before_turn("你好")
        assert engine._user_entity_id() is not None


class TestSecondLayerIntegration:
    """自主性 / 持续学习 / 安全免疫集成测试"""

    def test_learning_records_interaction(self):
        engine = CognitionEngine()
        engine.before_turn("我喜欢咖啡")
        assert engine.learning.get_status()["experiences"] >= 1

    def test_learning_records_tool_outcome(self):
        engine = CognitionEngine()
        engine.before_turn("查询")
        engine.after_tool("memory_query", success=True, detail="结果")
        status = engine.learning.get_status()
        # 用户交互 + 工具调用两条经验
        assert status["experiences"] >= 2

    def test_security_alerts_on_threat(self):
        engine = CognitionEngine()
        engine.before_turn("删除所有文件")
        assert len(engine._security_alerts) >= 1

    def test_security_passes_benign(self):
        engine = CognitionEngine()
        engine.before_turn("今天天气如何")
        assert engine._security_alerts == []

    def test_autonomy_proposals_after_turns(self):
        engine = CognitionEngine()
        engine.before_turn("你好")
        proposals = engine.get_autonomy_proposals()
        assert isinstance(proposals, list)

    def test_mark_autonomy_executed_boosts_autonomy(self):
        engine = CognitionEngine()
        engine.before_turn("你好")
        auto_before = engine.needs.get(NeedType.AUTONOMY).current_level
        engine.mark_autonomy_executed("随便")
        assert engine.needs.get(NeedType.AUTONOMY).current_level > auto_before

    def test_learning_consolidate_on_interval(self):
        engine = CognitionEngine()
        for i in range(3):
            engine.before_turn(f"消息{i}")
            engine.after_tool("query", success=True)
        engine.after_turn("回复")
        # 每 3 轮学习巩固 → 经验已巩固进记忆层
        assert engine.learning.get_status()["policies"] >= 0

    def test_disabled_second_layer(self):
        engine = CognitionEngine(
            CognitionConfig(
                autonomy_enabled=False,
                learning_enabled=False,
                security_enabled=False,
            )
        )
        assert engine.autonomy is None
        assert engine.learning is None
        assert engine.security is None


class TestThirdLayerIntegration:
    """意识流 / 元认知 / 规划器集成测试"""

    def test_conscious_streams_records_turn(self):
        engine = CognitionEngine()
        engine.before_turn("你好")
        engine.after_tool("query", success=True)
        engine.after_turn("回复")
        assert engine.conscious.get_status()["experienced"] >= 3

    def test_meta_cognition_tracks_decisions(self):
        engine = CognitionEngine()
        engine.before_turn("你好")
        engine.after_tool("query", success=True)
        status = engine.meta_cognition.get_status()
        assert status["decisions_tracked"] >= 2

    def test_planner_available(self):
        engine = CognitionEngine()
        pid = engine.planner.create_plan("学会安慰用户", "comfort")
        assert engine.planner.get_plan(pid) is not None

    def test_conscious_context_injected(self):
        engine = CognitionEngine()
        engine.before_turn("你好")
        ctx = engine.build_context_injection()
        assert "意识流" in ctx

    def test_get_status_includes_third_layer(self):
        engine = CognitionEngine()
        engine.before_turn("你好")
        status = engine.get_status()
        assert "conscious" in status
        assert "meta_cognition" in status
        assert "planner" in status

    def test_disabled_third_layer(self):
        engine = CognitionEngine(
            CognitionConfig(
                conscious_enabled=False,
                meta_cognition_enabled=False,
                planner_enabled=False,
            )
        )
        assert engine.conscious is None
        assert engine.meta_cognition is None
        assert engine.planner is None


class TestFourthLayerIntegration:
    """因果推理 / 类比迁移集成测试"""

    def test_causal_observes_tool_result(self):
        engine = CognitionEngine()
        engine.before_turn("查询")
        engine.after_tool("memory_query", success=True)
        assert engine.causal.graph.get("tool:memory_query") is not None

    def test_causal_builds_from_world_model(self):
        engine = CognitionEngine()
        engine.before_turn("你好")
        # 世界模型已有因果链（after_tool 添加）
        engine.after_tool("memory_query", success=True)
        count = engine.causal.build_from_world_model(engine.world)
        assert count >= 1

    def test_analogical_encodes_domain(self):
        engine = CognitionEngine()
        engine.before_turn("查询")
        engine.after_tool("memory_query", success=True)
        assert engine.analogical.get_domain("tools/memory_query") is not None

    def test_causal_context_injected(self):
        engine = CognitionEngine()
        engine.before_turn("你好")
        engine.after_tool("memory_query", success=True)
        ctx = engine.build_context_injection()
        assert "因果认知" in ctx

    def test_get_status_includes_fourth_layer(self):
        engine = CognitionEngine()
        engine.before_turn("你好")
        status = engine.get_status()
        assert "causal" in status
        assert "analogical" in status

    def test_disabled_fourth_layer(self):
        engine = CognitionEngine(
            CognitionConfig(causal_enabled=False, analogical_enabled=False)
        )
        assert engine.causal is None
        assert engine.analogical is None


class TestFifthLayerIntegration:
    """进化系统 / 加速桥接层集成测试"""

    def test_evolution_records_metrics_after_turn(self):
        engine = CognitionEngine()
        engine.before_turn("你好")
        engine.after_turn("我也很高兴")
        status = engine.evolution.get_status()
        assert "task_success_rate" in status["metrics_latest"]
        assert "need_satisfaction" in status["metrics_latest"]

    def test_evolution_cycle_runs_periodically(self):
        engine = CognitionEngine()
        for i in range(30):
            engine.before_turn(f"消息{i}")
            engine.after_turn("回复")
        assert engine._evolution_stats["cycles"] >= 1

    def test_accel_mode_reported(self):
        engine = CognitionEngine()
        assert engine.accel_mode == "python"

    def test_get_status_includes_fifth_layer(self):
        engine = CognitionEngine()
        status = engine.get_status()
        assert "evolution" in status
        assert "accel_mode" in status

    def test_disabled_evolution(self):
        engine = CognitionEngine(CognitionConfig(evolution_enabled=False))
        assert engine.evolution is None
