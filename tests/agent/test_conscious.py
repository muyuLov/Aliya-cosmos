"""测试意识流（conscious.py）"""

from __future__ import annotations

import pytest

from agent.cognition.conscious import AttentionEngine, ConsciousStream, Modality, Quale


class TestQuale:
    def test_salience(self):
        quale = Quale(content="x", intensity=0.5, valence=-0.6)
        assert quale.salience() == pytest.approx(0.5 * 1.6)

    def test_to_dict(self):
        quale = Quale(content="你好", modality=Modality.PERCEPTION, intensity=0.7)
        data = quale.to_dict()
        assert data["modality"] == "perception"
        assert data["intensity"] == 0.7


class TestAttentionEngine:
    def test_update_and_focus(self):
        att = AttentionEngine()
        att.update(Modality.PERCEPTION, 0.8)
        att.update(Modality.EMOTION, 0.3)
        assert att.focus() == Modality.PERCEPTION

    def test_decay(self):
        att = AttentionEngine()
        att.update(Modality.PERCEPTION, 0.8)
        att.decay(rate=0.3)
        assert att.weights()[Modality.PERCEPTION.value] == pytest.approx(0.5)

    def test_weights(self):
        att = AttentionEngine()
        att.update(Modality.COGNITION, 0.6)
        assert "cognition" in att.weights()


class TestConsciousStream:
    def test_experience(self):
        stream = ConsciousStream()
        stream.experience("用户说：今天心情不好", Modality.PERCEPTION, valence=-0.6)
        assert stream._experienced_count == 1
        assert len(stream._frames) == 1

    def test_current_frame(self):
        stream = ConsciousStream()
        stream.experience("a", Modality.PERCEPTION, intensity=0.3)
        stream.experience("b", Modality.EMOTION, intensity=0.9, valence=0.8)
        frame = stream.current_frame()
        assert frame[0]["content"] == "b"  # 显著性最高者优先

    def test_focus_qualia(self):
        stream = ConsciousStream()
        stream.experience("感知", Modality.PERCEPTION, intensity=0.9)
        stream.experience("情绪", Modality.EMOTION, intensity=0.2)
        focused = stream.focus_qualia()
        assert all(q["modality"] == "perception" for q in focused)

    def test_narrative(self):
        stream = ConsciousStream()
        stream.experience("用户说：你好", Modality.PERCEPTION)
        text = stream.narrative(limit=2)
        assert "用户说：你好" in text

    def test_reflect(self):
        stream = ConsciousStream()
        stream.experience("用户表达感激", Modality.PERCEPTION, valence=0.8)
        stream.experience("我很开心", Modality.EMOTION, valence=0.9)
        reflection = stream.reflect()
        assert "注意力" in reflection
        assert len(stream._reflections) == 1

    def test_reflect_empty(self):
        stream = ConsciousStream()
        assert stream.reflect() == ""

    def test_get_status(self):
        stream = ConsciousStream()
        stream.experience("x", Modality.PERCEPTION)
        status = stream.get_status()
        assert status["experienced"] == 1
        assert "attention" in status
