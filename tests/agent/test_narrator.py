"""Task 3.3: 主叙事器（narrator）测试

验证单次调用产出 script + 行为决策 + 结构化副产物，
空 script 语义失败 → 重试，超时降级纯文本。
"""

import asyncio
import json
import pytest


class FakeLLMService:
    """模拟 LLM 服务，返回预设 JSON。"""

    def __init__(self, response: str = "", delay: float = 0) -> None:
        self._response = response
        self._delay = delay
        self.call_count = 0

    async def create_completion(self, messages, **kwargs):
        self.call_count += 1
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        return type("FakeResponse", (), {
            "choices": [type("Choice", (), {
                "message": type("Message", (), {
                    "content": self._response,
                    "role": "assistant",
                })(),
            })()],
        })()


@pytest.mark.asyncio
async def test_narrator_returns_narrative_output():
    """narrator 应返回 NarrativeOutput"""
    from agent.narrator import Narrator
    from agent.metadata_parser import NarrativeOutput

    llm = FakeLLMService(response=json.dumps({
        "script": "她走出了门",
        "reply": {"mode": "immediate", "content": "你好啊！"},
        "memories": [],
        "intents": [],
        "actions": [],
    }))
    narrator = Narrator(llm_service=llm)
    result = await narrator.invoke(
        system_prompt="你是叙事者",
        context_json={"test": True},
    )
    assert isinstance(result, NarrativeOutput)
    assert result.script == "她走出了门"
    assert result.reply_content == "你好啊！"
    assert llm.call_count == 1


@pytest.mark.asyncio
async def test_narrator_retries_on_empty_script():
    """空 script 应触发重试（narrative_retry），不推进 cursor"""
    from agent.narrator import Narrator

    call_count = 0

    class ScriptThenEmpty:
        """第一次返回有 script，第二次返回空"""
        async def create_completion(self, messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                content = json.dumps({
                    "script": "",
                    "reply": {"mode": "immediate", "content": "空的"},
                    "memories": [],
                    "intents": [],
                    "actions": [],
                })
            else:
                content = json.dumps({
                    "script": "第二次成功了",
                    "reply": {"mode": "immediate", "content": "好了"},
                    "memories": [],
                    "intents": [],
                    "actions": [],
                })
            return type("FakeResponse", (), {
                "choices": [type("Choice", (), {
                    "message": type("Message", (), {
                        "content": content,
                        "role": "assistant",
                    })(),
                })()],
            })()

    narrator = Narrator(llm_service=ScriptThenEmpty())
    result = await narrator.invoke(
        system_prompt="你是叙事者",
        context_json={"test": True},
        max_retries=2,
    )
    assert result.script == "第二次成功了"
    assert call_count == 2


@pytest.mark.asyncio
async def test_narrator_fallback_to_plaintext_on_non_json():
    """非 JSON 响应应降级为纯文本模式"""
    from agent.narrator import Narrator
    from agent.metadata_parser import NarrativeOutput

    llm = FakeLLMService(response="这不是 JSON，是普通文本回复")
    narrator = Narrator(llm_service=llm)
    result = await narrator.invoke(
        system_prompt="你是叙事者",
        context_json={},
    )
    assert isinstance(result, NarrativeOutput)
    assert result.raw.get("_fallback") is True
    assert "普通文本" in result.reply_content


@pytest.mark.asyncio
async def test_narrator_timeout_returns_fallback():
    """超时应返回降级结果"""
    from agent.narrator import Narrator

    llm = FakeLLMService(response="回复", delay=10)
    narrator = Narrator(llm_service=llm)
    result = await narrator.invoke(
        system_prompt="你是叙事者",
        context_json={},
        timeout=0.05,  # 50ms 超时
    )
    # 应返回降级结果（不抛异常）
    assert result is not None
    assert result.reply_mode == "none"


@pytest.mark.asyncio
async def test_narrator_prompt_has_json_format():
    """system prompt 应包含 JSON 输出格式指令"""
    from agent.narrator import Narrator

    captured_messages = []

    class CapturingLLM:
        async def create_completion(self, messages, **kwargs):
            captured_messages.extend(messages)
            return type("FakeResponse", (), {
                "choices": [type("Choice", (), {
                    "message": type("Message", (), {
                        "content": json.dumps({
                            "script": "测试",
                            "reply": {"mode": "none", "content": ""},
                            "memories": [],
                            "intents": [],
                            "actions": [],
                        }),
                        "role": "assistant",
                    })(),
                })()],
            })()

    narrator = Narrator(llm_service=CapturingLLM())
    await narrator.invoke(system_prompt="你是叙事者", context_json={})

    # system 消息应包含 JSON 格式指令
    assert len(captured_messages) > 0
    sys_msg = captured_messages[0]["content"]
    assert "json" in sys_msg.lower() or "JSON" in sys_msg
