"""测试情感系统 — 完整移植 @soullink-emotion/sdk 的逻辑

覆盖：VAD 预设、getVADPreset、EmotionStateController 状态机、
EmbeddingMessageClassifier 向量分类器、以及 AliyaAgent 情感接口集成。
"""

# pyright: reportAttributeAccessIssue=false

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agent.emotion import (
    EmotionPersonality,
    EmotionStateController,
    EmbeddingMessageClassifier,
    VADRuntimeState,
    emotionVADPresets,
    getVADPreset,
    magnitude,
    nearestVADPreset,
    neutralVAD,
)
from agent.emotion.vad import VADVector, EmotionIntent, seeded_random


class TestVADPresets:
    def test_neutral_preset(self):
        """neutral 预设为零向量"""
        assert emotionVADPresets["neutral"] == neutralVAD
        assert emotionVADPresets["neutral"] == VADVector(0, 0, 0)

    def test_preset_count(self):
        """预设数量 = SDK 15 个（含 angry 别名）+ agent 扩展 4 个"""
        assert len(emotionVADPresets) == 19
        for key in ("calm", "happy", "excited", "shy", "affectionate", "curious",
                    "confused", "tired", "sad", "anxiety", "anger", "angry",
                    "concerned", "surprised"):
            assert key in emotionVADPresets

    def test_extended_presets(self):
        """agent 扩展情绪预设存在且符合 PAD 八分位"""
        assert "bored" in emotionVADPresets
        assert "grateful" in emotionVADPresets
        assert "relieved" in emotionVADPresets
        assert "disgusted" in emotionVADPresets
        # bored: -P -A -D
        assert emotionVADPresets["bored"].valence < 0
        assert emotionVADPresets["bored"].arousal < 0
        assert emotionVADPresets["bored"].dominance < 0
        # grateful: +P +A -D
        assert emotionVADPresets["grateful"].valence > 0.7
        assert emotionVADPresets["grateful"].arousal > 0
        assert emotionVADPresets["grateful"].dominance < 0
        # relieved: +P -A +D
        assert emotionVADPresets["relieved"].valence > 0.3
        assert emotionVADPresets["relieved"].arousal < 0
        # disgusted: -P +A +D
        assert emotionVADPresets["disgusted"].valence < 0
        assert emotionVADPresets["disgusted"].arousal > 0
        assert emotionVADPresets["disgusted"].dominance > 0

    def test_get_vad_preset_plain(self):
        """普通情绪名按表查"""
        assert getVADPreset("happy") == emotionVADPresets["happy"]

    def test_get_vad_preset_variant_shy(self):
        """variant 含 shy → shy 预设"""
        assert getVADPreset("affectionate", "bashful_shy") == emotionVADPresets["shy"]

    def test_get_vad_preset_variant_comfort_concerned(self):
        """comfort + concerned → concerned"""
        assert getVADPreset("concerned", "comfort") == emotionVADPresets["concerned"]

    def test_get_vad_preset_variant_comfort_other(self):
        """comfort + 非 concerned → affectionate"""
        assert getVADPreset("sad", "comfort") == emotionVADPresets["affectionate"]

    def test_get_vad_preset_variant_startled(self):
        """variant 含 startled → surprised"""
        assert getVADPreset("neutral", "startled") == emotionVADPresets["surprised"]

    def test_get_vad_preset_unknown(self):
        """未知情绪回退 neutral"""
        assert getVADPreset("not_a_emotion") == neutralVAD

    def test_magnitude_happy(self):
        """happy 预设的强度在合理范围"""
        m = magnitude(emotionVADPresets["happy"])
        assert 0.4 < m < 0.7

    def test_nearest_preset(self):
        """最近邻情绪推断"""
        assert nearestVADPreset(emotionVADPresets["happy"]) == "happy"
        assert nearestVADPreset(emotionVADPresets["sad"]) == "sad"
        # 远离所有预设的点 → neutral
        assert nearestVADPreset(VADVector(1.0, 1.0, -1.0)) == "neutral"

    def test_seeded_random_deterministic(self):
        """确定性随机源：相同 seed 产生相同序列"""
        r1 = seeded_random(9137)
        r2 = seeded_random(9137)
        seq1 = [r1() for _ in range(5)]
        seq2 = [r2() for _ in range(5)]
        assert seq1 == seq2
        assert all(0.0 <= v < 1.0 for v in seq1)


class TestEmotionStateController:
    def test_initial_state(self):
        """初始主导情绪为 neutral"""
        esc = EmotionStateController()
        assert esc.dominantEmotion == "neutral"
        assert esc.update(0).dominantEmotion == "neutral"

    def test_update_returns_runtime_state(self):
        """update 返回 VADRuntimeState 快照"""
        esc = EmotionStateController()
        state = esc.update(1 / 30)
        assert isinstance(state, VADRuntimeState)
        assert state.dominantEmotion == "neutral"
        assert "valence" in state.current.to_dict()

    def test_nudge_happy_sets_dominant(self):
        """nudge happy 后主导情绪立即为 happy"""
        esc = EmotionStateController()
        esc.nudge(EmotionIntent(emotion="happy", intensity=0.75))
        assert esc.dominantEmotion == "happy"
        assert esc.holdRemainingSeconds > 0

    def test_nudge_target_approaches_preset(self):
        """nudge 后 target 向 happy 预设靠近"""
        esc = EmotionStateController()
        esc.nudge(EmotionIntent(emotion="happy", intensity=0.75))
        assert esc.target.valence > 0.5
        assert esc.target.arousal > 0.3

    def test_nudge_variant_shy_overrides(self):
        """variant 含 shy 子串时主导情绪为 shy"""
        esc = EmotionStateController()
        esc.nudge(EmotionIntent(emotion="affectionate", variant="shy_smile", intensity=0.8))
        assert esc.dominantEmotion == "shy"

    def test_nudge_variant_not_shy_keeps_emotion(self):
        """variant 不含 shy 时主导情绪保持 naturalEmotion"""
        esc = EmotionStateController()
        esc.nudge(EmotionIntent(emotion="affectionate", variant="bashful", intensity=0.8))
        assert esc.dominantEmotion == "affectionate"

    def test_update_hold_prevents_decay(self):
        """保持期内 target 不衰减"""
        esc = EmotionStateController()
        esc.nudge(EmotionIntent(emotion="happy", intensity=0.75))
        target_before = VADVector(esc.target.valence, esc.target.arousal, esc.target.dominance)
        esc.update(1.0)
        assert esc.holdRemainingSeconds > 0
        assert abs(esc.target.valence - target_before.valence) < 1e-9

    def test_update_decay_back_to_baseline(self):
        """保持期结束后长时间推进衰减回 baseline（无环境漂移）"""
        esc = EmotionStateController(EmotionPersonality(ambientDriftStrength=0))
        esc.nudge(EmotionIntent(emotion="happy", intensity=0.75))
        esc.update(5.0)
        assert esc.dominantEmotion == "happy"
        esc.update(30.0)  # 保持期（6+0.75*18=19.5s）耗尽
        esc.update(300.0)  # target 衰减回基线
        esc.update(300.0)  # current 追上新 target
        assert esc.dominantEmotion == "neutral"
        assert magnitude(esc.current) < 0.0018

    def test_ambient_drift_accumulates(self):
        """环境漂移随推进产生非零分量"""
        esc = EmotionStateController(EmotionPersonality(ambientDriftStrength=0.09))
        for _ in range(20):
            esc.update(1.0)
        assert magnitude(esc.ambientDrift) > 0

    def test_configure_personality(self):
        """人格参数生效并受 clamp 限制"""
        esc = EmotionStateController(
            EmotionPersonality(reactivity=0.5, decayRate=0.1, emotionHoldSeconds=30)
        )
        assert esc.reactivity == 0.5
        assert esc.decayRate == 0.1
        assert esc.emotionHoldSeconds == 30
        # 超界值被 clamp
        esc.configure(EmotionPersonality(reactivity=99))
        assert esc.reactivity == 2.5

    def test_emotion_bias(self):
        """emotionBias 放大/缩小推入量"""
        esc = EmotionStateController(EmotionPersonality(emotionBias={"happy": 2.0}))
        esc.nudge(EmotionIntent(emotion="happy", intensity=0.75))
        amount = (0.28 + 0.75 * 0.58) * 1 * 2.0
        assert 0.96 - 1e-9 <= min(amount, 0.96)
        # 无 bias 的对照：推入量更小
        esc2 = EmotionStateController()
        esc2.nudge(EmotionIntent(emotion="happy", intensity=0.75))
        assert esc.target.valence > esc2.target.valence

    def test_emotion_bias_zero_suppresses(self):
        """emotionBias=0 时对应情绪被完全抑制（不被 or 回退为默认值）"""
        esc = EmotionStateController(EmotionPersonality(emotionBias={"happy": 0.0}))
        esc.nudge(EmotionIntent(emotion="happy", intensity=0.85))
        assert esc.target.valence == 0.0  # 推入量为 0，target 保持中性
        # variant 维度同样受 0 抑制
        esc2 = EmotionStateController(EmotionPersonality(emotionBias={"bashful": 0.0}))
        esc2.nudge(EmotionIntent(emotion="affectionate", variant="bashful", intensity=0.85))
        assert esc2.target.valence == 0.0

    def test_blend_to(self):
        """blendTo 混合 target 并延长保持"""
        esc = EmotionStateController()
        esc.blendTo({"valence": 0.9, "arousal": 0.5}, amount=0.65)
        assert esc.target.valence > 0.5
        assert esc.holdRemainingSeconds > 0

    def test_nudge_vad(self):
        """nudgeVAD 按增量调整 target"""
        esc = EmotionStateController()
        esc.nudgeVAD({"valence": 0.2, "arousal": -0.1}, amount=1)
        assert esc.target.valence > 0.1

    def test_infer_dominant_rules(self):
        """主导情绪推断的规则分支"""
        esc = EmotionStateController()
        assert esc.inferDominantEmotion(VADVector(0.5, -0.6, -0.3)) == "shy"
        assert esc.inferDominantEmotion(VADVector(-0.5, 0.5, -0.3)) == "anxiety"
        assert esc.inferDominantEmotion(VADVector(-0.5, 0.5, 0.3)) == "anger"
        assert esc.inferDominantEmotion(VADVector(0.7, 0.7, 0.4)) == "excited"
        assert esc.inferDominantEmotion(VADVector(0.4, -0.5, 0.2)) == "calm"
        assert esc.inferDominantEmotion(emotionVADPresets["happy"]) == "happy"

    def test_infer_subtle(self):
        """低强度细微情绪推断"""
        esc = EmotionStateController()
        assert esc.inferSubtleEmotion(VADVector(0.01, 0.01, 0)) == "soft-happy"
        assert esc.inferSubtleEmotion(VADVector(0.01, -0.01, 0)) == "soft-calm"
        assert esc.inferSubtleEmotion(VADVector(-0.01, 0.01, 0)) == "soft-uneasy"
        assert esc.inferSubtleEmotion(VADVector(0, 0, 0)) == "neutral"

    def test_nudge_extended_emotions(self):
        """agent 扩展情绪可稳定推入为主导情绪（无环境漂移干扰）"""
        for emotion, intensity in (
            ("bored", 0.7),
            ("grateful", 0.7),
            ("relieved", 0.65),
            ("disgusted", 0.6),
        ):
            esc = EmotionStateController(EmotionPersonality(ambientDriftStrength=0))
            esc.nudge(EmotionIntent(emotion=emotion, intensity=intensity))
            esc.update(5.0)
            assert esc.dominantEmotion == emotion, f"{emotion} → {esc.dominantEmotion}"


class TestAgentEmotionIntegration:
    """AliyaAgent 情感接口集成测试"""

    @pytest.mark.asyncio
    async def test_initial_emotion_neutral(self, mocker):
        conv = mocker.AsyncMock()
        reg = mocker.MagicMock()
        agent = _make_agent(mocker, conv, reg)
        assert agent.get_emotion() == "neutral"
        state = agent.get_emotion_state()
        assert state["dominantEmotion"] == "neutral"

    @pytest.mark.asyncio
    async def test_set_emotion_happy(self, mocker):
        conv = mocker.AsyncMock()
        reg = mocker.MagicMock()
        agent = _make_agent(mocker, conv, reg)
        agent.set_emotion("happy")
        assert agent.get_emotion() == "happy"
        state = agent.get_emotion_state()
        assert state["dominantEmotion"] == "happy"
        assert state["intensity"] > 0.1

    @pytest.mark.asyncio
    async def test_set_emotion_unknown(self, mocker):
        conv = mocker.AsyncMock()
        reg = mocker.MagicMock()
        agent = _make_agent(mocker, conv, reg)
        agent.set_emotion("不存在")
        assert agent.get_emotion() == "不存在"  # 透传保留

    @pytest.mark.asyncio
    async def test_observe_emotion_pushes_notification(self, mocker):
        conv = mocker.AsyncMock()
        reg = mocker.MagicMock()
        send = mocker.AsyncMock()
        agent = _make_agent(mocker, conv, reg, send=send)
        _stub_vector_classifier(agent, mocker, emotion="happy", variant="bright_smile")
        await agent._pipeline._observe_emotion("我成功上岸了！")
        assert agent.get_emotion() == "happy"
        # 发送了 emotion_changed 通知
        calls = [c.args[0] for c in send.await_args_list if c.args and c.args[0].get("type") == "emotion_changed"]
        assert calls, "应推送 emotion_changed"
        assert calls[0]["emotion"] == "happy"

    @pytest.mark.asyncio
    async def test_observe_emotion_neutral_no_notification(self, mocker):
        """首次 neutral 对话：状态仍为 neutral，不推送变更"""
        conv = mocker.AsyncMock()
        reg = mocker.MagicMock()
        send = mocker.AsyncMock()
        agent = _make_agent(mocker, conv, reg, send=send)
        await agent._pipeline._observe_emotion("你好")  # data/emotion_corpus.json neutral 语料样本，精确命中
        assert agent.get_emotion() == "neutral"
        calls = [c.args[0] for c in send.await_args_list if c.args and c.args[0].get("type") == "emotion_changed"]
        assert not calls

    @pytest.mark.asyncio
    async def test_observe_extended_emotion_grateful(self, mocker):
        """向量分类→状态机→主导情绪链路生效"""
        conv = mocker.AsyncMock()
        reg = mocker.MagicMock()
        agent = _make_agent(mocker, conv, reg)
        _stub_vector_classifier(agent, mocker, emotion="grateful", variant="warm_thanks")
        await agent._pipeline._observe_emotion("谢谢你的帮助")
        assert agent.get_emotion() == "grateful"
        state = agent.get_emotion_state()
        assert state["dominantEmotion"] == "grateful"

    @pytest.mark.asyncio
    async def test_close_emotion_classifier(self, mocker):
        """close_emotion_classifier 释放向量分类器并置空（幂等）"""
        conv = mocker.AsyncMock()
        reg = mocker.MagicMock()
        agent = _make_agent(mocker, conv, reg)
        vc = _stub_vector_classifier(agent, mocker)
        await agent.close_emotion_classifier()
        vc.aclose.assert_awaited_once()
        assert agent._ctx.emotion._classifier is None
        # 二次调用幂等
        await agent.close_emotion_classifier()


def _make_agent(mocker, conv, reg, send=None):
    from agent.agent import AliyaAgent
    from agent.config import AgentConfig
    from agent.prompts import PromptManager

    pm = PromptManager()
    agent = AliyaAgent(
        conversation_service=conv,
        tool_registry=reg,
        send_message=send or mocker.AsyncMock(),
        config=AgentConfig(permission_config_path=""),
        prompt_manager=pm,
    )
    return agent


def _stub_vector_classifier(agent, mocker, emotion="happy", variant="bright_smile", intensity=0.85):
    """注入一个已启用、固定返回指定意图的向量分类器桩。"""
    from agent.emotion import EmbeddingMessageClassifier
    from agent.emotion.vad import EmotionIntent

    stub = mocker.AsyncMock(spec=EmbeddingMessageClassifier)
    stub.initialized = True
    stub.enabled = True
    stub.classify.return_value = EmotionIntent(
        emotion=emotion,
        variant=variant,
        intensity=intensity,
        contextTags=[],
    )
    agent._ctx.emotion._classifier = stub
    return stub


# ── 向量情绪分类器（集成 core.vector） ─────────────────────────────────────


class _FakeStore:
    """模拟 VectorStore：get / add / search_async。"""

    def __init__(self, results, existing_ids=()):
        self._results = list(results)
        self.existing_ids = set(existing_ids)
        self.added = []
        self.search_calls = 0
        self.dimension = 384

    def get(self, item_id):
        # 与真实 VectorStore.get 一致：存在返回条目，不存在返回 None
        return {"id": item_id} if item_id in self.existing_ids else None

    async def add(self, text, metadata=None, item_id=None):
        self.existing_ids.add(item_id)
        self.added.append((text, metadata, item_id))
        return item_id

    async def add_many(self, items):
        ids = []
        for item in items:
            iid = item.get("item_id")
            self.existing_ids.add(iid)
            self.added.append((item.get("text"), item.get("metadata"), iid))
            ids.append(iid)
        return ids

    async def search_async(self, query, top_k=None, threshold=None):
        _ = (query, top_k, threshold)
        self.search_calls += 1
        return self._results


def _result(emotion, score, variant="", intensity=0.7):
    return SimpleNamespace(
        id=f"{emotion}-{score}",
        text=emotion,
        score=score,
        metadata={
            "kind": "emotion",
            "emotion": emotion,
            "variant": variant,
            "intensity": intensity,
            "contextTags": [],
        },
    )


class TestEmbeddingMessageClassifier:
    @pytest.mark.asyncio
    async def test_initialize_then_vote(self):
        """初始化入库语料后，检索结果按情绪加权投票"""
        store = _FakeStore([
            _result("happy", 0.91),
            _result("happy", 0.88),
            _result("sad", 0.72),
        ])
        clf = EmbeddingMessageClassifier(store=store)
        assert clf.configured is True  # 注入 store 即视为配置就绪
        assert clf.enabled is False    # 未入库前不可用
        assert await clf.initialize() is True
        assert clf.enabled is True
        assert len(store.added) > 0  # 语料已入库

        intent = await clf.classify("我今天真的好开心啊")
        assert intent.emotion == "happy"
        # happy 权重 1.79 / 总权重 2.51 ≈ 0.71
        assert 0.6 < intent.intensity < 0.85
        assert intent.naturalVAD is not None
        assert intent.naturalVAD["valence"] > 0.3

    @pytest.mark.asyncio
    async def test_exact_hit_skips_search(self):
        """规范化文本精确命中语料时直接返回，不调用检索"""
        store = _FakeStore([])
        clf = EmbeddingMessageClassifier(store=store)
        await clf.initialize()
        intent = await clf.classify("今天心情不错")  # data 语料 happy 前 30 条样本，精确命中
        assert intent.emotion == "happy"
        assert store.search_calls == 0

    @pytest.mark.asyncio
    async def test_query_cache(self):
        """相同查询走 LRU 缓存，只检索一次"""
        store = _FakeStore([_result("happy", 0.9)])
        clf = EmbeddingMessageClassifier(store=store)
        await clf.initialize()
        await clf.classify("好开心啊")
        await clf.classify("好开心啊")
        assert store.search_calls == 1

    @pytest.mark.asyncio
    async def test_initialize_idempotent(self):
        """重复初始化不重复入库已有语料"""
        store = _FakeStore([])
        clf1 = EmbeddingMessageClassifier(store=store)
        await clf1.initialize()
        added_once = len(store.added)
        clf2 = EmbeddingMessageClassifier(store=store)
        await clf2.initialize()
        assert len(store.added) == added_once

    @pytest.mark.asyncio
    async def test_empty_results_neutral_fallback(self):
        """检索无结果时返回 neutral 兜底"""
        store = _FakeStore([])
        clf = EmbeddingMessageClassifier(store=store)
        await clf.initialize()
        intent = await clf.classify("我成功上岸了！")
        assert intent.emotion == "neutral"
        assert intent.variant == "neutral_ack"

    def test_disabled_neutral_fallback(self, mocker):
        """向量模块未启用时返回 neutral 兜底"""
        from core.vector.config import VectorConfig
        mocker.patch(
            "core.vector.config.get_vector_config",
            return_value=VectorConfig(enabled=False),
        )
        clf = EmbeddingMessageClassifier()
        assert clf.enabled is False
        intent = asyncio.run(clf.classify("我成功上岸了！"))
        assert intent.emotion == "neutral"

    @pytest.mark.asyncio
    async def test_search_error_neutral_fallback(self):
        """检索异常时返回 neutral 兜底，不抛出"""
        store = _FakeStore([_result("happy", 0.9)])
        clf = EmbeddingMessageClassifier(store=store)
        await clf.initialize()

        async def boom(query, top_k=None, threshold=None):
            _ = (query, top_k, threshold)
            raise RuntimeError("api down")

        store.search_async = boom
        intent = await clf.classify("出问题了吗")
        assert intent.emotion == "neutral"

    @pytest.mark.asyncio
    async def test_aclose_releases_store(self):
        """aclose 释放底层 store 并重置状态（幂等）"""
        store = _FakeStore([_result("happy", 0.9)])
        store.aclosed = False

        async def fake_aclose():
            store.aclosed = True

        store.aclose = fake_aclose
        clf = EmbeddingMessageClassifier(store=store)
        await clf.initialize()
        assert clf.enabled is True

        await clf.aclose()
        assert store.aclosed is True
        assert clf.enabled is False
        # 关闭后 classify 走 neutral 兜底
        intent = await clf.classify("好开心啊")
        assert intent.emotion == "neutral"
        # 幂等：二次关闭不抛错
        await clf.aclose()

    @pytest.mark.asyncio
    async def test_aclose_no_store(self):
        """未配置向量库时 aclose 幂等不抛错"""
        clf = EmbeddingMessageClassifier(store=_FakeStore([]))
        await clf.aclose()
