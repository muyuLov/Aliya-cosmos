"""测试 AgentLoop 两阶段循环：骨架、工具阶段、灵魂阶段、确认等待、边界兜底"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.context import ContextBuilder
from agent.events import (
    CONFIRM_REQUEST,
    NOTICE,
    ProtocolEvent,
    RunFinished,
    RunStarted,
    StepStarted,
    TextMessageEnd,
    ToolCallResult,
)
from agent.loop import AgentLoop
from agent.tools import PermissionChecker, ToolDefinition, ToolRegistry
from core.llm.cache import ContextCache
from core.llm.models import ChatResponse, TokenUsage
from core.llm.service import ConversationService

PROMPTS_DIR = "data/prompts"


def make_tool_calls(name: str, call_id: str = "call_1", arguments: str = "{}") -> list[dict]:
    return [{"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}]


@pytest.fixture
def fake_registry():
    reg = ToolRegistry()

    async def fake_exec(_ctx, _args):
        return "工具执行结果"

    reg.register(
        ToolDefinition(
            id="memory_query",
            name="memory_query",
            description="查询记忆",
            input_schema={"type": "object", "properties": {}},
            risk="safe",
        ),
        fake_exec,
    )
    return reg


@pytest.fixture
def allow_checker(tmp_path):
    p = tmp_path / "Permissions.yml"
    p.write_text("tools:\n  memory_query: always_allow\n", encoding="utf-8")
    return PermissionChecker(str(p))


@pytest.fixture
def confirm_checker(tmp_path):
    p = tmp_path / "Permissions.yml"
    p.write_text("tools:\n  memory_query: confirm\n", encoding="utf-8")
    return PermissionChecker(str(p))


@pytest.fixture
def deny_checker(tmp_path):
    p = tmp_path / "Permissions.yml"
    p.write_text("tools:\n  memory_query: never_allow\n", encoding="utf-8")
    return PermissionChecker(str(p))


def make_provider(tool_rounds: int = 0, stream_tokens: list[str] | None = None):
    """mock provider：先返回 N 轮 tool_calls，然后返回普通文本；流式返回 tokens。"""
    provider = MagicMock()
    provider.model = "test-model"
    provider.provider_name = "test"
    provider.supports_thinking = True
    calls = [make_tool_calls("memory_query", call_id=f"call_{i}") for i in range(tool_rounds)]
    responses = [ChatResponse(content="", finish_reason="tool_calls", tool_calls=cs) for cs in calls]
    responses.append(ChatResponse(content="最终回答", usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)))
    provider.async_chat_completion = AsyncMock(side_effect=responses)
    provider.last_stream_usage = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    tokens = list(stream_tokens or ["你", "好", "！"])

    async def fake_stream(_request):
        for token in tokens:
            yield token

    provider.stream_chat_completion = fake_stream
    provider.aclose = AsyncMock()
    return provider


def make_service(provider, conversation_id="test-session"):
    return ConversationService(
        provider=provider,
        cache=ContextCache(ttl=3600),
        conversation_id=conversation_id,
    )


def make_loop(provider, registry, checker, memory=None, **kwargs):
    service = make_service(provider)
    return service, AgentLoop(
        service=service,
        registry=registry,
        checker=checker,
        context=ContextBuilder(PROMPTS_DIR),
        memory=memory,
        **kwargs,
    )


async def collect(agen):
    return [ev async for ev in agen]


class TestLoopSkeleton:
    def test_interrupt_reset(self):
        _, loop = make_loop(MagicMock(), MagicMock(), MagicMock())
        assert not loop._abort
        loop.interrupt()
        assert loop._abort
        loop.reset_abort()
        assert not loop._abort

    @pytest.mark.asyncio
    async def test_submit_yields_run_started_first(self, allow_checker):
        registry = ToolRegistry()
        provider = make_provider(tool_rounds=0)
        service, loop = make_loop(provider, registry, allow_checker)
        gen = loop.submit_user_message("hi")
        first = await gen.__anext__()
        assert isinstance(first, RunStarted)
        assert first.session_id == "test-session"
        await gen.aclose()
        await service.aclose()


class TestToolPhase:
    @pytest.mark.asyncio
    async def test_event_sequence_two_tool_rounds(self, fake_registry, allow_checker):
        """两次 tool_calls 后返回空 → 事件序列包含 ToolCallStart/Result/End"""
        provider = make_provider(tool_rounds=2, stream_tokens=["好"])
        service, loop = make_loop(provider, fake_registry, allow_checker)
        events = await collect(loop.submit_user_message("查记忆"))
        kinds = [type(ev).__name__ for ev in events]
        assert kinds[0] == "RunStarted"
        assert "StepStarted" in kinds
        assert "ToolCallStart" in kinds
        assert "ToolCallResult" in kinds
        assert "ToolCallEnd" in kinds
        assert kinds.count("ToolCallStart") == 2
        # 工具结果已写入历史（tool 角色消息）
        history = await service.get_history()
        tool_msgs = [m for m in history if m.role == "tool"]
        assert len(tool_msgs) == 2
        await service.aclose()

    @pytest.mark.asyncio
    async def test_no_tool_calls_skips_tool_execution(self, fake_registry, allow_checker):
        provider = make_provider(tool_rounds=0)
        service, loop = make_loop(provider, fake_registry, allow_checker)
        events = await collect(loop.submit_user_message("你好"))
        kinds = [type(ev).__name__ for ev in events]
        assert "ToolCallStart" not in kinds
        assert "StepStarted" in kinds
        await service.aclose()

    @pytest.mark.asyncio
    async def test_tool_phase_sets_tools_system(self, fake_registry, allow_checker):
        """TOOL_PHASE 应设置工具调度 system（tools_system.md）"""
        provider = make_provider(tool_rounds=0)
        service, loop = make_loop(provider, fake_registry, allow_checker)
        await collect(loop._tool_phase("你好", []))
        prompt = service._context.system_prompt
        assert prompt is not None
        assert "工具调度" in prompt
        assert "tool_calls" in prompt
        await service.aclose()


class TestSoulPhase:
    @pytest.mark.asyncio
    async def test_streaming_event_sequence(self, fake_registry, allow_checker):
        provider = make_provider(tool_rounds=0, stream_tokens=["你", "好", "！"])
        service, loop = make_loop(provider, fake_registry, allow_checker)
        events = await collect(loop.submit_user_message("你好"))
        kinds = [type(ev).__name__ for ev in events]
        assert "TextMessageStart" in kinds
        assert kinds.count("TextMessageDelta") == 3
        assert "TextMessageEnd" in kinds
        assert "RunFinished" in kinds
        end = next(ev for ev in events if isinstance(ev, TextMessageEnd))
        assert end.full_text == "你好！"
        await service.aclose()

    @pytest.mark.asyncio
    async def test_add_conversation_memory_called(self, fake_registry, allow_checker):
        memory = MagicMock()
        memory.add_conversation_memory = AsyncMock(return_value=True)
        memory.get_relevant_memories = AsyncMock(return_value=[])
        provider = make_provider(tool_rounds=0)
        service, loop = make_loop(provider, fake_registry, allow_checker, memory=memory)
        await collect(loop.submit_user_message("你好"))
        memory.add_conversation_memory.assert_awaited_once()
        args = memory.add_conversation_memory.call_args[0]
        assert args[0] == "你好"
        assert args[1] == "你好！"
        await service.aclose()

    @pytest.mark.asyncio
    async def test_soul_phase_injects_relevant_memory(self, fake_registry, allow_checker):
        """SOUL_PHASE 应检索相关记忆并注入 system prompt"""
        memory = MagicMock()
        memory.get_relevant_memories = AsyncMock(
            return_value=[("Kane", "人物", "喜欢", "哲学书", "概念")]
        )
        memory.add_conversation_memory = AsyncMock(return_value=True)
        provider = make_provider(tool_rounds=0)
        service, loop = make_loop(provider, fake_registry, allow_checker, memory=memory)
        await collect(loop.submit_user_message("我记得 Kane 喜欢什么"))
        memory.get_relevant_memories.assert_awaited_once()
        # system prompt 应包含格式化后的记忆文本
        prompt = service._context.system_prompt
        assert prompt is not None
        assert "相关记忆" in prompt
        assert "Kane 喜欢 哲学书" in prompt
        await service.aclose()

    @pytest.mark.asyncio
    async def test_soul_phase_memory_unavailable_degrades(self, fake_registry, allow_checker):
        """memory 为 None 时记忆注入降级为空，不阻塞流程"""
        provider = make_provider(tool_rounds=0)
        service, loop = make_loop(provider, fake_registry, allow_checker, memory=None)
        events = await collect(loop.submit_user_message("你好"))
        assert any(isinstance(ev, TextMessageEnd) for ev in events)
        prompt = service._context.system_prompt
        assert prompt is not None
        assert "相关记忆" not in prompt
        await service.aclose()

    @pytest.mark.asyncio
    async def test_interrupt_stops_and_notice(self, fake_registry, allow_checker):
        provider = make_provider(tool_rounds=0, stream_tokens=["你", "好", "！"])
        service, loop = make_loop(provider, fake_registry, allow_checker)

        gen = loop.submit_user_message("你好")
        events = []
        async for ev in gen:
            events.append(ev)
            if isinstance(ev, StepStarted) and ev.phase == "soul":
                loop.interrupt()
        kinds = [type(ev).__name__ for ev in events]
        assert "TextMessageEnd" in kinds
        notices = [ev for ev in events if isinstance(ev, ProtocolEvent) and ev.type == NOTICE]
        assert notices, "中断后应发送 notice"
        await service.aclose()


class TestConfirmation:
    @pytest.mark.asyncio
    async def test_confirm_request_and_allow(self, fake_registry, confirm_checker):
        provider = make_provider(tool_rounds=1)
        service, loop = make_loop(provider, fake_registry, confirm_checker)

        gen = loop.submit_user_message("调用工具")
        events = []
        confirm_ev = None
        async for ev in gen:
            events.append(ev)
            if isinstance(ev, ProtocolEvent) and ev.type == CONFIRM_REQUEST:
                confirm_ev = ev
                await loop.resolve_confirmation(confirm_ev.payload["call_id"], allowed=True)

        assert confirm_ev is not None
        assert confirm_ev.payload["tool"] == "memory_query"
        assert "ToolCallStart" in [type(e).__name__ for e in events]
        await service.aclose()

    @pytest.mark.asyncio
    async def test_confirm_reject(self, fake_registry, confirm_checker):
        provider = make_provider(tool_rounds=1)
        service, loop = make_loop(provider, fake_registry, confirm_checker)

        gen = loop.submit_user_message("调用工具")
        events = []
        async for ev in gen:
            events.append(ev)
            if isinstance(ev, ProtocolEvent) and ev.type == CONFIRM_REQUEST:
                await loop.resolve_confirmation(ev.payload["call_id"], allowed=False)

        assert "ToolCallStart" not in [type(e).__name__ for e in events]
        # 拒绝结果已写入历史
        history = await service.get_history()
        assert any("已拒绝" in m.content for m in history if m.role == "tool")
        await service.aclose()

    @pytest.mark.asyncio
    async def test_interrupt_releases_pending_confirmation(self, fake_registry, confirm_checker):
        """interrupt 应立即解除挂起的工具确认（视为拒绝），无需等待超时"""
        provider = make_provider(tool_rounds=1)
        service, loop = make_loop(provider, fake_registry, confirm_checker)

        gen = loop.submit_user_message("调用工具")
        events = []
        async for ev in gen:
            events.append(ev)
            if isinstance(ev, ProtocolEvent) and ev.type == CONFIRM_REQUEST:
                loop.interrupt()  # 不 resolve，靠 interrupt 解除

        assert "ToolCallStart" not in [type(e).__name__ for e in events]
        # 中断后确认挂起表已清空
        assert loop.pending_confirmations == {}
        await service.aclose()

    @pytest.mark.asyncio
    async def test_confirm_timeout_rejects(self, fake_registry, confirm_checker):
        provider = make_provider(tool_rounds=1)
        service, loop = make_loop(
            provider, fake_registry, confirm_checker, confirm_timeout=0.05
        )
        # 不 resolve，靠超时拒绝
        events = await collect(loop.submit_user_message("调用工具"))
        assert "ToolCallStart" not in [type(e).__name__ for e in events]
        await service.aclose()


class TestBoundary:
    @pytest.mark.asyncio
    async def test_executor_exception_downgraded(self, allow_checker):
        reg = ToolRegistry()

        async def broken(_ctx, _args):
            raise RuntimeError("boom")

        reg.register(
            ToolDefinition(
                id="memory_query", name="memory_query", description="d", input_schema={}, risk="safe"
            ),
            broken,
        )
        provider = make_provider(tool_rounds=1)
        service, loop = make_loop(provider, reg, allow_checker)
        events = await collect(loop.submit_user_message("查"))
        results = [ev for ev in events if isinstance(ev, ToolCallResult)]
        assert results
        assert "工具执行失败" in results[0].output
        await service.aclose()

    @pytest.mark.asyncio
    async def test_max_rounds_forced_soul(self, fake_registry, allow_checker):
        """工具轮数达到上限后仍进入 SOUL_PHASE"""
        provider = make_provider(tool_rounds=5)
        service, loop = make_loop(
            provider, fake_registry, allow_checker, max_tool_rounds=2
        )
        events = await collect(loop.submit_user_message("查"))
        kinds = [type(e).__name__ for e in events]
        assert kinds.count("ToolCallStart") == 2
        assert "StepStarted" in kinds  # SOUL_PHASE 进入
        await service.aclose()

    @pytest.mark.asyncio
    async def test_tool_phase_timeout_breaks(self, fake_registry, allow_checker):
        """单轮超时 → break 进 SOUL_PHASE"""
        provider = make_provider(tool_rounds=1)
        provider.async_chat_completion = AsyncMock(side_effect=asyncio.TimeoutError())
        service, loop = make_loop(
            provider, fake_registry, allow_checker, tool_timeout=0.05
        )
        events = await collect(loop.submit_user_message("查"))
        kinds = [type(e).__name__ for e in events]
        assert "ToolCallStart" not in kinds
        assert "StepStarted" in kinds
        await service.aclose()

    @pytest.mark.asyncio
    async def test_soul_phase_exception_emits_error(self, fake_registry, allow_checker):
        provider = make_provider(tool_rounds=0)

        async def broken_stream(_request):
            raise RuntimeError("stream broken")
            yield  # pragma: no cover

        provider.stream_chat_completion = broken_stream
        service, loop = make_loop(provider, fake_registry, allow_checker)
        events = await collect(loop.submit_user_message("你好"))
        errors = [ev for ev in events if isinstance(ev, ProtocolEvent) and ev.type == "error"]
        assert errors
        assert any(isinstance(ev, RunFinished) for ev in events)
        await service.aclose()
