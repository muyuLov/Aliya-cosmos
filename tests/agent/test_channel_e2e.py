"""集成测试：真实 AgentLoop + FeishuEventSink 全链路。

覆盖「飞书消息 → AgentSession.submit → sink → 飞书回复」双向闭环，
以及高风险工具经渠道交互确认（CONFIRM_REQUEST → 卡片 → resolve_confirmation）的护栏。
"""

from __future__ import annotations

import asyncio
import pathlib
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.channels.feishu_sink import FeishuEventSink
from agent.context import ContextBuilder
from agent.events import RunFinished
from agent.loop import AgentLoop
from agent.session import AgentSession
from agent.tools import PermissionChecker, ToolDefinition, ToolRegistry
from core.llm.cache import ContextCache
from core.llm.models import ChatResponse, TokenUsage
from core.llm.service import ConversationService


class FakeFeishuClient:
    def __init__(self):
        self.sent_texts: list[tuple[str, str]] = []
        self.sent_cards: list[tuple[str, dict]] = []

    async def send_text(self, chat_id: str, text: str) -> None:
        self.sent_texts.append((chat_id, text))

    async def send_card(self, chat_id: str, card: dict) -> None:
        self.sent_cards.append((chat_id, card))


async def _build_session(*, tool_name: str = "test_op", risk: str = "safe", tool_call: bool = True):
    """生产式装配：真实 AgentLoop + mock LLM provider + 自定义工具 + 空权限配置。"""
    provider = MagicMock()
    provider.model = "test-model"
    provider.provider_name = "test"
    provider.supports_thinking = True
    provider.last_stream_usage = TokenUsage(prompt_tokens=5, completion_tokens=3, total_tokens=8)
    if tool_call:
        effects = [
            ChatResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    {"id": "call_1", "type": "function", "function": {"name": tool_name, "arguments": "{}"}}
                ],
            ),
            ChatResponse(
                content="无需更多工具",
                usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            ),
        ]
    else:
        effects = [
            ChatResponse(
                content="无需工具",
                usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            )
        ]
    provider.async_chat_completion = AsyncMock(side_effect=effects)

    async def fake_stream(_request):
        for tok in ["已", "完", "成"]:
            yield tok

    provider.stream_chat_completion = fake_stream
    provider.aclose = AsyncMock()

    tmp = pathlib.Path(tempfile.mkdtemp()) / "Permissions.yml"
    tmp.write_text("tools: {}\n", encoding="utf-8")
    checker = PermissionChecker(str(tmp))

    service = ConversationService(
        provider=provider,
        cache=ContextCache(ttl=3600),
        conversation_id="cid",
    )
    registry = ToolRegistry()
    executor = AsyncMock(return_value=f"执行结果:{tool_name}")
    registry.register(
        ToolDefinition(id=tool_name, name=tool_name, description="测试工具", input_schema={}, risk=risk),
        executor,
    )
    loop = AgentLoop(
        service=service,
        registry=registry,
        checker=checker,
        context=ContextBuilder("data/prompts"),
    )
    return AgentSession("cid", service, loop), executor


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("等待条件超时")
        await asyncio.sleep(0.01)


def _card_call_id(card: dict) -> str:
    buttons = card["data"]["template_card"]["action"]["button_list"]
    return buttons[0]["value"]["call_id"]


@pytest.mark.asyncio
async def test_text_reply_forwarded_to_sink():
    session, _ = await _build_session(tool_call=False)
    fake = FakeFeishuClient()
    session.add_sink(FeishuEventSink(fake, "oc_1", confirm=True))

    events = [ev async for ev in session.submit("你好")]
    assert any(isinstance(ev, RunFinished) for ev in events)
    assert fake.sent_texts == [("oc_1", "已完成")]


@pytest.mark.asyncio
async def test_safe_tool_no_confirm():
    session, executor = await _build_session(tool_name="safe_op", risk="safe", tool_call=True)
    fake = FakeFeishuClient()
    session.add_sink(FeishuEventSink(fake, "oc_1", confirm=True))

    [ev async for ev in session.submit("执行安全操作")]
    executor.assert_awaited_once()
    assert fake.sent_cards == []
    assert ("oc_1", "已完成") in fake.sent_texts


@pytest.mark.asyncio
async def test_high_risk_tool_confirm_allowed():
    session, executor = await _build_session(tool_name="high_op", risk="high", tool_call=True)
    fake = FakeFeishuClient()
    session.add_sink(FeishuEventSink(fake, "oc_1", confirm=True))

    async def consume():
        async for _ in session.submit("执行高风险操作"):
            pass

    task = asyncio.create_task(consume())
    # 挂起期间确认卡片已发出
    await _wait_until(lambda: bool(fake.sent_cards))
    chat_id, card = fake.sent_cards[0]
    assert chat_id == "oc_1"
    call_id = _card_call_id(card)
    assert call_id == "call_1"
    # 用户点确认 → 工具继续执行 → 回复送出
    await session.loop.resolve_confirmation(call_id, allowed=True)
    await asyncio.wait_for(task, timeout=5)
    executor.assert_awaited_once()
    assert ("oc_1", "已完成") in fake.sent_texts


@pytest.mark.asyncio
async def test_high_risk_tool_confirm_denied():
    session, executor = await _build_session(tool_name="high_op", risk="high", tool_call=True)
    fake = FakeFeishuClient()
    session.add_sink(FeishuEventSink(fake, "oc_1", confirm=True))

    async def consume():
        async for _ in session.submit("执行高风险操作"):
            pass

    task = asyncio.create_task(consume())
    await _wait_until(lambda: bool(fake.sent_cards))
    call_id = _card_call_id(fake.sent_cards[0][1])
    # 用户点拒绝 → 工具不执行
    await session.loop.resolve_confirmation(call_id, allowed=False)
    await asyncio.wait_for(task, timeout=5)
    executor.assert_not_awaited()
    assert ("oc_1", "已完成") in fake.sent_texts
