# Agent 管线式重构实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 `agent/agent.py`（约 680 行主编排器）重构为「管线（Pipeline）+ 阶段模块 + 混合钩子」架构，同时修复 `Brain` 对 `ConversationService` 私有状态的侵入。

**Architecture:** `AgentContext` 作为会话级统一依赖容器；`AgentPipeline` 负责驱动阶段流转与触发钩子；`stages/`（assemble/think/soul）承载各阶段逻辑；`HookRegistry` 提供 before_turn / after_tool / after_turn / after_reply 四个钩子点接入横切能力（认知/情绪/记忆/TTS/通知）。对外 `AliyaAgent` 门面 API 与 WS 协议零破坏。

**Tech Stack:** Python 3.12+、asyncio、pytest-asyncio、pytest-mock、dataclasses

---

## 重要约束

- **不自动 git commit**（用户明确"确定但不提交"）。每个任务以「测试全部通过」为完成标志，是否提交由用户决定。
- 每一步实施前先跑 `git status` 确认工作区干净，避免误操作。
- 现有 139 个测试用例不得破坏（`tests/agent/` 与 `tests/memory/` 等）。

---

## 任务总览

| # | 任务 | 文件 |
|---|------|------|
| 1 | ConversationService 补公开方法 | `core/llm/service.py` + `tests/llm/test_conversation_service.py` |
| 2 | Brain 改用公开 API | `agent/brain.py`（验证 `tests/agent/test_agent.py`） |
| 3 | 钩子系统 | `agent/hooks.py` + `tests/agent/test_hooks.py` |
| 4 | AgentContext 依赖容器 | `agent/context.py` + `tests/agent/test_context.py` |
| 5 | 阶段模块 | `agent/stages/{__init__,assemble,think,soul}.py` + `tests/agent/test_stages.py` |
| 6 | AgentPipeline 编排器 | `agent/pipeline.py` + `tests/agent/test_pipeline.py` |
| 7 | agent.py 瘦身门面 + ws.py 接线 | `agent/agent.py`、`agent/ws.py`、`agent/__init__.py` + 全量回归 |

---

## Task 1: ConversationService 补公开方法

**Files:**
- Modify: `core/llm/service.py`（历史管理区，`discard_messages` 之后）
- Test: `tests/llm/test_conversation_service.py`

**Step 1: 写失败测试**

在 `tests/llm/test_conversation_service.py` 末尾追加：

```python
class TestReplaceLastMessage:
    @pytest.mark.asyncio
    async def test_replace_last_assistant_message(self, service):
        await service.asend("你好")
        await service.replace_last_message("[已完成工具阶段分析]", reasoning_content="")
        history = await service.get_history()
        assert history[-1].content == "[已完成工具阶段分析]"
        assert history[-1].reasoning_content == ""

    @pytest.mark.asyncio
    async def test_replace_preserves_earlier_messages(self, service):
        await service.asend("第一句")
        await service.asend("第二句")
        before = await service.get_history()
        await service.replace_last_message("替换")
        after = await service.get_history()
        assert after[:-1] == before[:-1]
        assert after[-1].content == "替换"


class TestTruncateMessages:
    @pytest.mark.asyncio
    async def test_truncate_keeps_tail(self, service):
        for _ in range(5):
            await service.asend("对话")
        await service.truncate_messages(keep=2)
        history = await service.get_history()
        assert len(history) == 2
        assert history[-1].role == "assistant"

    @pytest.mark.asyncio
    async def test_truncate_keep_zero_clears_all(self, service):
        await service.asend("你好")
        await service.truncate_messages(keep=0)
        assert await service.get_history() == []
```

**Step 2: 运行确认失败**

Run: `pytest tests/llm/test_conversation_service.py -v`
Expected: FAIL，`AttributeError: 'ConversationService' object has no attribute 'replace_last_message'`

**Step 3: 实现**

在 `core/llm/service.py` 的 `discard_messages` 方法之后追加：

```python
    async def replace_last_message(
        self, content: str, reasoning_content: str = "",
    ) -> None:
        """替换历史最后一条消息（通常为工具阶段生成的 JSON 决策消息）。

        用于工具阶段消息净化：将纯 JSON 决策替换为纯文本标记，
        避免诱导后续阶段 LLM 模仿 JSON 输出。
        """
        async with self._lock:
            if not self._context.messages:
                return
            last = self._context.messages[-1]
            replaced = last.model_copy(
                update={"content": content, "reasoning_content": reasoning_content}
            )
            self._context.messages[-1] = replaced
            self._context.updated_at = time.time()
            self._save()

    async def truncate_messages(self, keep: int) -> None:
        """截断历史，仅保留最后 keep 条消息（压缩对话时使用）。"""
        if keep < 0:
            raise ValueError("keep 必须为非负数")
        async with self._lock:
            if not self._context.messages:
                return
            self._context.messages = self._context.messages[-keep:] if keep else []
            self._context.updated_at = time.time()
            self._save()
```

**Step 4: 运行确认通过**

Run: `pytest tests/llm/test_conversation_service.py -v`
Expected: PASS（全部，含既有用例）

---

## Task 2: Brain 改用公开 API

**Files:**
- Modify: `agent/brain.py`（`_sanitize_tool_phase_message` 与 `compress_conversation` 两处）
- Test: `tests/agent/test_agent.py`（既有 `TestToolPhaseSanitize` 即为验证）

**Step 1: 改造 Brain**

`_sanitize_tool_phase_message` 中，将私有状态操作块替换为调用公开方法：

```python
        content = (result.reply or "").strip()
        if content:
            return  # 工具阶段已产出正式回复，无需净化
        content = "[已完成工具阶段分析]" if result.tool_calls else "[无需调用工具，继续对话]"
        try:
            await self._conv.replace_last_message(content)
        except Exception as e:
            logger.debug("[Sanitize] 工具阶段消息净化失败（不阻塞）: %s", e)
```

`compress_conversation` 中，将截断历史的私有操作块替换为：

```python
            self._compressed_context = response.content.strip()

            # 从历史中移除已被压缩的旧消息
            await self._conv.truncate_messages(_COMPRESSION_KEEP)
```

（其余部分不变，`get_history()` / `_provider` 已是公开或 `getattr` 访问。）

**Step 2: 运行验证**

Run: `pytest tests/agent/test_agent.py -v`
Expected: PASS。同时用 grep 确认 `agent/brain.py` 不再出现 `_lock` / `_context` / `_save()`：
`Select-String -Path agent/brain.py -Pattern '_lock|_context|_save'`

**Step 3: 全量回归**

Run: `pytest tests/llm tests/agent -q`
Expected: PASS

---

## Task 3: 钩子系统

**Files:**
- Create: `agent/hooks.py`
- Test: `tests/agent/test_hooks.py`

**Step 1: 写失败测试**

```python
"""测试钩子注册表：注册 / 触发 / 异常隔离 / 异步可丢调度"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent.hooks import HookPoint, HookRegistry


@pytest.mark.asyncio
async def test_register_and_run_in_order():
    registry = HookRegistry()
    calls: list[str] = []

    async def h1(text: str) -> None:
        calls.append("h1")

    async def h2(text: str) -> None:
        calls.append("h2")

    registry.register(HookPoint.BEFORE_TURN, h1)
    registry.register(HookPoint.BEFORE_TURN, h2)
    await registry.run(HookPoint.BEFORE_TURN, "hi")
    assert calls == ["h1", "h2"]


@pytest.mark.asyncio
async def test_run_isolates_handler_exception():
    registry = HookRegistry()

    async def boom(*args: Any) -> None:
        raise RuntimeError("boom")

    async def ok(*args: Any) -> None:
        pass

    registry.register(HookPoint.AFTER_TOOL, boom)
    registry.register(HookPoint.AFTER_TOOL, ok)
    # 异常被隔离，后续钩子仍执行，run 不抛出
    await registry.run(HookPoint.AFTER_TOOL, "t", None)


@pytest.mark.asyncio
async def test_run_later_fire_and_forget():
    registry = HookRegistry()
    done = asyncio.Event()

    async def slow(*args: Any) -> None:
        await asyncio.sleep(0.01)
        done.set()

    registry.register(HookPoint.AFTER_REPLY, slow)
    registry.run_later(HookPoint.AFTER_REPLY, "reply")
    await asyncio.wait_for(done.wait(), timeout=1.0)


@pytest.mark.asyncio
async def test_unregister():
    registry = HookRegistry()

    async def h(*args: Any) -> None:
        raise AssertionError("不应被调用")

    registry.register(HookPoint.AFTER_TURN, h)
    registry.unregister(HookPoint.AFTER_TURN, h)
    await registry.run(HookPoint.AFTER_TURN, "reply")
```

**Step 2: 运行确认失败**

Run: `pytest tests/agent/test_hooks.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'agent.hooks'`

**Step 3: 实现 `agent/hooks.py`**

```python
"""钩子注册表：横切能力（认知 / 情绪 / 记忆 / TTS / 通知）的接入点

四个钩子点：
- before_turn(text)    同步：认知准备（阻塞，结果注入上下文）
- after_tool(name, result)  同步：工具学习（顺序敏感）
- after_turn(reply)    同步：对话收尾（记忆保存、情绪推进调度）
- after_reply(reply)   异步可丢：通知类（TTS 播放、brain_complete）

同步钩子用 run() 顺序 await；异步可丢钩子用 run_later() 以
create_task 调度并带错误回调，不阻塞主流程。
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from enum import Enum
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class HookPoint(str, Enum):
    BEFORE_TURN = "before_turn"
    AFTER_TOOL = "after_tool"
    AFTER_TURN = "after_turn"
    AFTER_REPLY = "after_reply"


HookHandler = Callable[..., Awaitable[None]]


class HookRegistry:
    """按钩子点注册与触发处理器的注册表。"""

    def __init__(self) -> None:
        self._handlers: dict[HookPoint, list[HookHandler]] = defaultdict(list)

    def register(self, point: HookPoint, handler: HookHandler) -> None:
        if handler not in self._handlers[point]:
            self._handlers[point].append(handler)

    def unregister(self, point: HookPoint, handler: HookHandler) -> None:
        try:
            self._handlers[point].remove(handler)
        except ValueError:
            pass

    async def run(self, point: HookPoint, *args: Any) -> None:
        """按注册顺序 await 所有处理器，单个异常被隔离（记录并继续）。"""
        for handler in list(self._handlers.get(point, ())):
            try:
                await handler(*args)
            except Exception as e:
                logger.warning("[Hook] %s 处理器异常（已隔离）: %s", point.value, e)

    def run_later(self, point: HookPoint, *args: Any) -> None:
        """以 fire-and-forget 方式调度处理器，不阻塞调用方。"""
        for handler in list(self._handlers.get(point, ())):
            task = asyncio.create_task(handler(*args))
            task.add_done_callback(self._log_error)

    @staticmethod
    def _log_error(task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception():
            logger.error("[Hook] 异步钩子异常: %s", task.exception())


__all__ = ["HookPoint", "HookRegistry"]
```

**Step 4: 运行确认通过**

Run: `pytest tests/agent/test_hooks.py -v`
Expected: PASS

---

## Task 4: AgentContext 依赖容器

**Files:**
- Create: `agent/context.py`
- Test: `tests/agent/test_context.py`

**Step 1: 写失败测试**

```python
"""测试 AgentContext：统一依赖容器与 ToolContext 派生"""
from __future__ import annotations

from unittest.mock import MagicMock

from agent.context import AgentContext
from agent.config import AgentConfig
from agent.tools.base import ToolContext


def test_make_tool_context_derives_all_fields():
    tts = MagicMock()
    player = MagicMock()
    mem = MagicMock()
    confirm = MagicMock()
    perm = MagicMock()

    async def notify(data: dict) -> None:
        pass

    ctx = AgentContext(
        conv=MagicMock(), registry=MagicMock(), config=AgentConfig(),
        prompt_manager=MagicMock(), style_switcher=MagicMock(),
        brain=MagicMock(), emotion=MagicMock(), cognition=None,
        memory_manager=mem, tts_service=tts, audio_player=player,
        notify=notify, confirm_callback=confirm,
        permission_config=perm,
    )
    tc = ctx.make_tool_context()
    assert isinstance(tc, ToolContext)
    assert tc.tts_service is tts
    assert tc.audio_player is player
    assert tc.memory_manager is mem
    assert tc.confirm_callback is confirm
    assert tc.permission_config is perm
    assert tc.send_message is notify
```

**Step 2: 运行确认失败**

Run: `pytest tests/agent/test_context.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'agent.context'`

**Step 3: 实现 `agent/context.py`**

```python
"""AgentContext — 会话级统一依赖容器

一次构造收拢 Agent 运行所需的全部依赖，管线各阶段 / 工具 / 钩子
订阅者均从容器取用，杜绝依赖分散与 ToolContext 重复构造。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from core.llm import ConversationService

from agent.brain import Brain
from agent.config import AgentConfig
from agent.emotion.engine import EmotionEngine
from agent.prompts import PromptManager
from agent.prompts.style_switcher import StyleSwitcher
from agent.tools.base import ToolContext
from agent.tools.registry import ToolRegistry


@dataclass(frozen=True)
class AgentContext:
    """会话级依赖容器（构造后不可变）。"""

    conv: ConversationService
    registry: ToolRegistry
    config: AgentConfig
    prompt_manager: PromptManager
    style_switcher: StyleSwitcher
    brain: Brain
    emotion: EmotionEngine
    cognition: Any | None = None  # CognitionEngine | None
    memory_manager: Any | None = None
    tts_service: Any | None = None
    audio_player: Any | None = None
    audio_relay: Callable[[dict], Awaitable[None]] | None = None
    notify: Callable[[dict], Awaitable[None]] | None = None
    confirm_callback: Callable[[str, dict], Awaitable[bool]] | None = None
    permission_config: Any | None = None

    def make_tool_context(self) -> ToolContext:
        """派生 ToolContext（唯一构造点）。"""
        return ToolContext(
            tts_service=self.tts_service,
            audio_player=self.audio_player,
            memory_manager=self.memory_manager,
            send_message=self.notify,
            audio_relay=self.audio_relay,
            permission_config=self.permission_config,
            confirm_callback=self.confirm_callback,
        )


__all__ = ["AgentContext"]
```

**Step 4: 运行确认通过**

Run: `pytest tests/agent/test_context.py -v`
Expected: PASS

---

## Task 5: 阶段模块

**Files:**
- Create: `agent/stages/__init__.py`
- Create: `agent/stages/assemble.py`
- Create: `agent/stages/think.py`
- Create: `agent/stages/soul.py`
- Test: `tests/agent/test_stages.py`

**Step 1: 设计说明**

阶段模块承载原 `agent.py` 的 `_enter_tool_phase` / `_tool_loop` / `_enter_soul_phase` + `_generate_soul_reply` 逻辑。轮次状态（turn / has_called_tools / reply_tool_called）由调用方（pipeline）持有并通过参数传入，阶段保持无状态函数式设计。

**Step 2: 写失败测试**

```python
"""测试阶段模块：assemble / think / soul 独立逻辑"""
from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.brain import BrainResult
from agent.config import AgentConfig
from agent.context import AgentContext
from agent.tools.base import ToolResult
from agent.stages.assemble import assemble_tool_phase
from agent.stages.think import run_tool_loop
from agent.stages.soul import run_soul_phase


@dataclass
class TurnState:
    turn: int = 0
    has_called_tools: bool = False
    reply_tool_called: bool = False
    last_user_input: str = ""
    final_reply: str = ""


def _make_ctx(**overrides) -> AgentContext:
    brain = MagicMock()
    brain.think = AsyncMock(return_value=BrainResult(reply="", tool_calls=[]))
    brain.think_with_context = AsyncMock(
        return_value=BrainResult(reply="最终回复", tool_calls=[])
    )
    brain.compress_conversation = AsyncMock()
    brain.compressed_context = ""
    emotion = MagicMock()
    conv = MagicMock()
    conv.set_system_prompt = AsyncMock()
    conv.set_context_injection = AsyncMock()
    conv.set_emotion_patch = AsyncMock()
    conv.append_message = AsyncMock()
    conv.discard_messages = AsyncMock()
    registry = MagicMock()
    registry.dispatch_all = AsyncMock(return_value=[("reply", ToolResult(success=True, data="hi"))])
    registry.format_tool_summary = MagicMock(return_value="工具结果摘要")
    registry.list = MagicMock(return_value=["reply", "memory_query"])
    pm = MagicMock()
    pm.build_tool_system_prompt = MagicMock(return_value="tool-system")
    pm.build_soul_system_prompt = MagicMock(return_value="soul-system")
    pm.build_emotion_patch = MagicMock(return_value="emotion-patch")
    notify = AsyncMock()
    return AgentContext(
        conv=conv, registry=registry, config=AgentConfig(),
        prompt_manager=pm, style_switcher=MagicMock(),
        brain=brain, emotion=emotion, cognition=None,
        memory_manager=None, notify=notify,
    )


@pytest.mark.asyncio
async def test_assemble_sets_tool_prompt_and_injection():
    ctx = _make_ctx()
    await assemble_tool_phase(ctx)
    ctx.conv.set_system_prompt.assert_awaited_once_with("tool-system")
    ctx.conv.set_context_injection.assert_awaited_once()


@pytest.mark.asyncio
async def test_think_loop_calls_tools_and_injects_result():
    ctx = _make_ctx()
    # 第一轮 think 返回工具调用，第二轮 think_with_context 返回最终回复
    results = [
        BrainResult(reply="", tool_calls=[{"name": "reply", "params": {"text": "hi"}}]),
        BrainResult(reply="最终回复", tool_calls=[]),
    ]
    ctx.brain.think = AsyncMock(side_effect=results)
    state = TurnState()
    reply = await run_tool_loop(ctx, "你好", state, notify=ctx.notify)
    assert reply == "最终回复"
    assert state.has_called_tools is True
    assert state.turn == 1
    ctx.registry.dispatch_all.assert_awaited_once()
    ctx.conv.append_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_soul_phase_switches_context_and_replies():
    ctx = _make_ctx()
    ctx.brain.generate_soul_reply = AsyncMock(return_value="灵魂回复")
    reply = await run_soul_phase(ctx)
    assert reply == "灵魂回复"
    ctx.conv.set_system_prompt.assert_awaited_once_with("soul-system")
    ctx.conv.set_context_injection.assert_awaited_once()
```

**Step 3: 运行确认失败**

Run: `pytest tests/agent/test_stages.py -v`
Expected: FAIL，`ModuleNotFoundError`

**Step 4: 实现阶段模块**

`agent/stages/__init__.py`:

```python
"""Agent 管线阶段模块"""

from agent.stages.assemble import assemble_tool_phase
from agent.stages.think import run_tool_loop
from agent.stages.soul import run_soul_phase

__all__ = ["assemble_tool_phase", "run_tool_loop", "run_soul_phase"]
```

`agent/stages/assemble.py`:

```python
"""阶段 1：上下文组装（工具阶段 system prompt + 认知注入 + 对话压缩）"""

from __future__ import annotations

from agent.context import AgentContext

_MEMORY_HEADER = "[记忆]"


async def assemble_tool_phase(ctx: AgentContext) -> None:
    """切换到工具阶段：tools_system.md 作为 system prompt（无角色人格）。"""
    tool_system = ctx.prompt_manager.build_tool_system_prompt()
    await ctx.conv.set_system_prompt(tool_system)

    # 注入认知上下文（需求状态 + 记忆召回），帮助工具决策
    cognition_context = ""
    if ctx.cognition:
        parts = [ctx.cognition.build_context_injection(limit=4, max_sections=3)]
        mem = ctx.cognition.build_memory_context(limit=3)
        if mem:
            parts.append(f"{_MEMORY_HEADER}\n{mem}")
        cognition_context = "\n\n".join(p for p in parts if p)
    await ctx.conv.set_context_injection(tools="", memory=cognition_context)

    # 尝试对话压缩
    await ctx.brain.compress_conversation()
```

`agent/stages/think.py`:

```python
"""阶段 2：工具阶段循环（Think → Act → Observe）"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from agent.context import AgentContext

logger = logging.getLogger(__name__)

# 注入到临时消息中的前缀标记，用于后续 cleanup
TOOL_RESULT_MARKER = "tool_result"


async def run_tool_loop(
    ctx: AgentContext,
    text: str,
    state: "TurnState",
    notify: Callable[[dict], Awaitable[None]] | None = None,
) -> str:
    """执行 Think → Act → Observe 循环，返回最终回复文本（可能为空）。

    Args:
        ctx: 依赖容器
        text: 用户输入
        state: 轮次状态（pipeline 持有，本函数修改 turn / 调用标记）
        notify: 通知回调
    """
    result = await ctx.brain.think(text)
    final_reply = result.reply

    while result.tool_calls:
        state.has_called_tools = True
        state.turn += 1

        if state.turn > ctx.config.max_turns:
            logger.warning("[Plan] 达到最大循环轮次，强制进入灵魂阶段 | turn=%d | max_turns=%d",
                           state.turn, ctx.config.max_turns)
            break

        tools_list = [c.get("name") for c in result.tool_calls]
        logger.debug("[Tool] 执行工具调用 | turn=%d | tools=%s", state.turn, tools_list)
        if notify:
            await notify({
                "type": "brain_progress",
                "message": f"执行工具调用（第 {state.turn} 轮）",
                "tools": tools_list,
            })

        tool_results = await ctx.registry.dispatch_all(result.tool_calls, ctx.make_tool_context())

        # 认知学习（after_tool）：需求更新、情景记忆、世界模型、自我模型
        if ctx.cognition:
            for name, tres in tool_results:
                detail = tres.data if tres.success else tres.error
                ctx.cognition.after_tool(name, tres.success, detail=detail)

        # 记录本轮是否调用了 ReplyTool（供调用方判断是否跳过 brain_complete）
        if not state.reply_tool_called:
            state.reply_tool_called = any(name == "reply" for name, _ in tool_results)

        # 观察 — 将工具结果注入上下文
        summary = ctx.registry.format_tool_summary(tool_results)
        logger.debug("[Observe] 工具结果注入 | turn=%d | tools=%s", state.turn, tools_list)
        await ctx.conv.append_message(
            "assistant",
            f"[工具执行结果]\n{summary}",
            metadata={"injected": True, "prefix": TOOL_RESULT_MARKER},
        )

        # 继续思考
        if notify:
            await notify({
                "type": "brain_progress",
                "message": f"根据工具结果继续推理（第 {state.turn} 轮）",
            })
        result = await ctx.brain.think_with_context()
        final_reply = result.reply

        if notify:
            await notify({
                "type": "brain_refine",
                "reply": result.reply,
                "thought": result.thought,
                "turn": state.turn,
            })

    return final_reply
```

`agent/stages/soul.py`:

```python
"""阶段 3：灵魂阶段（人格上下文 + 记忆注入 + 最终回复）"""

from __future__ import annotations

import logging

from agent.context import AgentContext

logger = logging.getLogger(__name__)

_AUTONOMY_HEADER = "[主动建议]"
_MEMORY_HEADER = "[相关记忆]"
_SUMMARY_HEADER = "[历史对话摘要]"


async def run_soul_phase(ctx: AgentContext) -> str:
    """切换到灵魂阶段（恢复人格、注入记忆、设置情绪补丁），生成最终回复。"""
    soul_system = ctx.prompt_manager.build_soul_system_prompt(style=ctx.prompt_manager.current_style
                                                              if hasattr(ctx.prompt_manager, "current_style")
                                                              else "")
    await ctx.conv.set_system_prompt(soul_system)

    current_emotion = ctx.emotion.current_emotion
    if current_emotion:
        patch = ctx.prompt_manager.build_emotion_patch(current_emotion)
        await ctx.conv.set_emotion_patch(patch)

    extra_parts: list[str] = []
    if ctx.brain.compressed_context:
        extra_parts.append(f"{_SUMMARY_HEADER}\n{ctx.brain.compressed_context}")
    if ctx.cognition:
        cognition_ctx = ctx.cognition.build_context_injection(limit=4, max_sections=5)
        if cognition_ctx:
            extra_parts.append(cognition_ctx)
        try:
            proposals = ctx.cognition.get_autonomy_proposals()
            high = [p for p in proposals if p.get("priority") == "high"]
            if high:
                lines = [f"- {p['action']}（{p['reason']}）" for p in high[:2]]
                extra_parts.append(f"{_AUTONOMY_HEADER}\n" + "\n".join(lines))
        except Exception:
            pass

    memory_context = "\n\n".join(extra_parts) if extra_parts else ""
    await ctx.conv.set_context_injection(memory=memory_context)

    logger.debug("[SoulPhase] 已完成 | emotion=%s | has_summary=%s | has_memory=%s",
                 current_emotion or "none",
                 bool(ctx.brain.compressed_context), bool(memory_context))
    return await ctx.brain.generate_soul_reply()


__all__ = ["run_soul_phase", "TOOL_RESULT_MARKER"]
```

注意：`style` 参数需要 pipeline 传入（当前 `_current_style` 由 agent 持有）。第 6 节 pipeline 会调整 `run_soul_phase` 签名接收 `style`。若 Task 5 测试先行，此处先用 `""` 占位，Task 6 中修正为 `run_soul_phase(ctx, style)`。

**Step 5: 运行确认通过**

Run: `pytest tests/agent/test_stages.py -v`
Expected: PASS

---

## Task 6: AgentPipeline 编排器

**Files:**
- Create: `agent/pipeline.py`
- Test: `tests/agent/test_pipeline.py`

**Step 1: 设计说明**

`AgentPipeline` 替代原 `AliyaAgent` 主体：持有 `AgentContext` + `HookRegistry`，实现 `handle_user_message` 完整流转（before_turn → assemble → think → soul → after_turn → after_reply）、状态通知（`AgentState` + `_STATE_DISPLAY`）、错误处理与 `_finalize` 收尾。同时负责 TTS 自动播放、记忆保存、情绪推进的**默认钩子订阅者**注册。

`run_soul_phase` 签名改为 `run_soul_phase(ctx, style)`。

**Step 2: 写失败测试**

```python
"""测试 AgentPipeline：完整流转 / 钩子触发 / 状态通知 / 错误降级"""
from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.brain import BrainResult
from agent.config import AgentConfig
from agent.context import AgentContext
from agent.hooks import HookPoint, HookRegistry
from agent.pipeline import AgentPipeline
from agent.tools.base import ToolResult
from agent.stages.think import run_tool_loop


def _make_ctx(**overrides) -> AgentContext:
    brain = MagicMock()
    brain.think = AsyncMock(return_value=BrainResult(reply="", tool_calls=[]))
    brain.think_with_context = AsyncMock(
        return_value=BrainResult(reply="最终回复", tool_calls=[])
    )
    brain.generate_soul_reply = AsyncMock(return_value="灵魂回复")
    brain.force_summary_reply = AsyncMock(return_value="兜底回复")
    brain.compress_conversation = AsyncMock()
    brain.compressed_context = ""
    brain.cot_enabled = True
    brain.use_native_thinking = False

    emotion = MagicMock()
    emotion.current_emotion = "happy"
    emotion.get_state = MagicMock(return_value={})

    conv = MagicMock()
    conv.set_system_prompt = AsyncMock()
    conv.set_context_injection = AsyncMock()
    conv.set_emotion_patch = AsyncMock()
    conv.append_message = AsyncMock()
    conv.discard_messages = AsyncMock()
    conv.conversation_id = "test-id"

    registry = MagicMock()
    registry.dispatch_all = AsyncMock(
        return_value=[("reply", ToolResult(success=True, data="hi"))]
    )
    registry.format_tool_summary = MagicMock(return_value="summary")
    registry.list = MagicMock(return_value=["reply"])

    pm = MagicMock()
    pm.build_tool_system_prompt = MagicMock(return_value="tool-system")
    pm.build_soul_system_prompt = MagicMock(return_value="soul-system")
    pm.build_emotion_patch = MagicMock(return_value="patch")

    return AgentContext(
        conv=conv, registry=registry, config=AgentConfig(),
        prompt_manager=pm, style_switcher=MagicMock(),
        brain=brain, emotion=emotion, cognition=None,
        memory_manager=None, notify=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_handle_message_no_tools_goes_soul_phase():
    ctx = _make_ctx()
    pipe = AgentPipeline(ctx)
    await pipe.handle_user_message("你好")
    ctx.brain.think.assert_awaited_once_with("你好")
    ctx.brain.generate_soul_reply.assert_awaited_once()
    ctx.conv.set_system_prompt.assert_awaited()
    assert ctx.notify.await_count > 0  # 有通知推送


@pytest.mark.asyncio
async def test_hooks_triggered_in_order():
    ctx = _make_ctx()
    hooks = HookRegistry()
    calls: list[str] = []

    async def bt(text: str) -> None:
        calls.append("before_turn")

    async def at(name: str, result: object) -> None:
        calls.append("after_tool")

    async def atn(reply: str) -> None:
        calls.append("after_turn")

    hooks.register(HookPoint.BEFORE_TURN, bt)
    hooks.register(HookPoint.AFTER_TOOL, at)
    hooks.register(HookPoint.AFTER_TURN, atn)
    pipe = AgentPipeline(ctx, hooks=hooks)
    await pipe.handle_user_message("你好")
    assert "before_turn" in calls
    assert "after_turn" in calls


@pytest.mark.asyncio
async def test_error_falls_back_to_force_summary():
    ctx = _make_ctx()
    ctx.brain.think = AsyncMock(side_effect=RuntimeError("llm down"))
    pipe = AgentPipeline(ctx)
    await pipe.handle_user_message("你好")
    ctx.brain.force_summary_reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_state_transitions_notified():
    ctx = _make_ctx()
    pipe = AgentPipeline(ctx)
    await pipe.handle_user_message("hi")
    notified_types = [c.kwargs["data"]["type"] for c in ctx.notify.call_args_list]
    assert "brain_start" in notified_types
    assert "state_change" in notified_types
```

**Step 3: 运行确认失败**

Run: `pytest tests/agent/test_pipeline.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'agent.pipeline'`

**Step 4: 实现 `agent/pipeline.py`**

```python
"""AgentPipeline — 管线式编排器

职责：
- 驱动一轮对话的阶段流转：before_turn → assemble → think → soul → after_turn → after_reply
- 触发钩子（HookRegistry）接入横切能力（认知 / 情绪 / 记忆 / TTS / 通知）
- 状态通知（AgentState + 友好状态展示）与错误降级

不承担具体能力实现：TTS、记忆保存、情绪推进等均为默认钩子订阅者。
"""

from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Any

from core.logger import get_logger

from agent.context import AgentContext
from agent.hooks import HookPoint, HookRegistry
from agent.stages.assemble import assemble_tool_phase
from agent.stages.soul import run_soul_phase
from agent.stages.think import TOOL_RESULT_MARKER, run_tool_loop

logger = get_logger(__name__)


class AgentState(Enum):
    """Agent 循环状态"""
    IDLE = "idle"
    CONTEXT_ASSEMBLY = "context_assembly"
    THINKING = "thinking"
    TOOL_EXECUTION = "tool_execution"
    OBSERVING = "observing"
    SOUL_PHASE = "soul_phase"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"


# Agent 状态 → 前端展示映射
_STATE_DISPLAY: dict[AgentState, str] = {
    AgentState.IDLE: "陪伴中",
    AgentState.CONTEXT_ASSEMBLY: "聆听中",
    AgentState.THINKING: "思考中",
    AgentState.TOOL_EXECUTION: "工作中",
    AgentState.OBSERVING: "工作中",
    AgentState.SOUL_PHASE: "思考中",
    AgentState.COMPLETED: "陪伴中",
    AgentState.ERROR: "陪伴中",
    AgentState.CANCELLED: "陪伴中",
}


@dataclass
class TurnState:
    """单轮对话的流转状态。"""
    turn: int = 0
    has_called_tools: bool = False
    reply_tool_called: bool = False
    last_user_input: str = ""
    final_reply: str = ""


class AgentPipeline:
    """管线式编排器：阶段流转 + 钩子触发 + 状态通知。"""

    def __init__(
        self,
        ctx: AgentContext,
        hooks: HookRegistry | None = None,
    ) -> None:
        self._ctx = ctx
        self._hooks = hooks or HookRegistry()
        self._state: AgentState = AgentState.IDLE
        self._turn_state = TurnState()
        self._current_style: str = ctx.config.prompt_style
        self._progress_task: asyncio.Task | None = None
        self._register_default_hooks()

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def turn(self) -> int:
        return self._turn_state.turn

    @property
    def hooks(self) -> HookRegistry:
        return self._hooks

    # ── 默认钩子订阅者（横切能力） ──────────────────────────────────────────

    def _register_default_hooks(self) -> None:
        ctx = self._ctx
        if ctx.cognition:
            self._hooks.register(HookPoint.BEFORE_TURN, ctx.cognition.before_turn)
            self._hooks.register(HookPoint.AFTER_TURN, ctx.cognition.after_turn)
        self._hooks.register(HookPoint.AFTER_TURN, self._hook_save_memory)

    async def _hook_save_memory(self, reply: str) -> None:
        """记忆保存（after_turn 钩子）。"""
        ctx = self._ctx
        if not ctx.memory_manager or not hasattr(ctx.memory_manager, "add_conversation_memory"):
            return
        try:
            day_date = time.strftime("%Y-%m-%d")
            session_id = ctx.conv.conversation_id[:12]
            await ctx.memory_manager.add_conversation_memory(
                self._turn_state.last_user_input, reply,
                session_id=session_id,
                day_date=day_date,
                timeline="aliya|user",
            )
        except Exception as e:
            logger.warning("记忆保存失败: %s", e)

    # ── 主入口 ──────────────────────────────────────────────────────────────

    async def handle_user_message(self, text: str) -> None:
        """处理用户消息：钩子准备 → 阶段流转 → 收尾。"""
        self._begin_round()
        self._turn_state.last_user_input = text
        final_reply = ""

        await self._notify({"type": "brain_start"})
        self._progress_task = asyncio.create_task(self._push_progress())

        try:
            # 认知准备（before_turn）
            await self._hooks.run(HookPoint.BEFORE_TURN, text)

            # 阶段 1：上下文组装
            await self._transition(AgentState.CONTEXT_ASSEMBLY)
            await assemble_tool_phase(self._ctx)

            # 阶段 2：工具阶段循环
            await self._transition(AgentState.THINKING)
            final_reply = await run_tool_loop(
                self._ctx, text, self._turn_state, notify=self._notify,
            )

            # 阶段 3：灵魂阶段
            if self._turn_state.has_called_tools or not final_reply:
                await self._transition(AgentState.SOUL_PHASE)
                await self._notify({"type": "brain_progress", "message": "进入灵魂表达阶段"})
                final_reply = await run_soul_phase(self._ctx, style=self._current_style)
                logger.debug("[Soul] 灵魂阶段回复 | reply_len=%d", len(final_reply))

            self._turn_state.final_reply = final_reply

        except asyncio.CancelledError:
            self._state = AgentState.CANCELLED
            logger.info("[Plan] Agent 循环被取消 | turn=%d", self.turn)
            raise
        except Exception as e:
            self._state = AgentState.ERROR
            logger.error("[Plan] Agent 循环异常 | turn=%d | error=%s", self.turn, e, exc_info=True)
            await self._notify({"type": "brain_error", "message": str(e)})
            if not final_reply:
                final_reply = await self._ctx.brain.force_summary_reply()
        finally:
            await self._finalize(final_reply)

    def _begin_round(self) -> None:
        self._turn_state = TurnState(last_user_input=self._turn_state.last_user_input)
        self._state = AgentState.IDLE
        self._ctx.brain.reset()
        self._ctx.brain.reset_compressed_context()

    async def _finalize(self, final_reply: str) -> None:
        """收尾：停进度、清理注入消息、触发 after_turn / after_reply 钩子。"""
        if self._progress_task and not self._progress_task.done():
            self._progress_task.cancel()
        self._progress_task = None

        if self._state not in (AgentState.ERROR, AgentState.CANCELLED):
            await self._transition(AgentState.COMPLETED)
            logger.info("[Complete] 回复完成 | turn=%d | reply_len=%d",
                        self.turn, len(final_reply))

        # 清理临时注入消息
        try:
            await self._ctx.conv.discard_messages(TOOL_RESULT_MARKER, self._ctx.config.max_refine_accum)
        except Exception:
            pass

        if final_reply:
            # after_turn：记忆保存 + 认知后续处理 + 情绪推进（同步钩子）
            await self._hooks.run(HookPoint.AFTER_TURN, final_reply)
            # after_reply：TTS / 通知（异步可丢）
            self._hooks.run_later(HookPoint.AFTER_REPLY, final_reply)

        # 发送最终回复通知到 UI：仅在 ReplyTool 未发送过时执行
        if final_reply and not self._turn_state.reply_tool_called:
            await self._notify({
                "type": "brain_complete",
                "reply": final_reply,
                "emotion": self._ctx.emotion.current_emotion,
                "emotion_state": self._ctx.emotion.get_state(),
            })

    # ── 状态管理 ────────────────────────────────────────────────────────────

    async def _transition(self, new_state: AgentState) -> None:
        old_state = self._state
        if old_state != new_state:
            self._state = new_state
            await self._notify({
                "type": "state_change",
                "from": old_state.value,
                "to": new_state.value,
                "turn": self.turn,
            })
            new_display = _STATE_DISPLAY.get(new_state, "陪伴中")
            old_display = _STATE_DISPLAY.get(old_state, "")
            if new_display != old_display:
                await self._notify({
                    "type": "status_changed",
                    "status": new_display,
                    "state": new_state.value,
                })

    async def _push_progress(self) -> None:
        while True:
            await asyncio.sleep(self._ctx.config.progress_interval)
            if self._state in (AgentState.THINKING, AgentState.SOUL_PHASE):
                await self._notify({"type": "brain_progress", "message": "思考中"})

    async def _notify(self, data: dict) -> None:
        if self._ctx.notify:
            await self._ctx.notify(data)


__all__ = ["AgentState", "AgentPipeline", "TurnState"]
```

同时修正 `agent/stages/soul.py` 签名：

```python
async def run_soul_phase(ctx: AgentContext, style: str = "") -> str:
    ...
    soul_system = ctx.prompt_manager.build_soul_system_prompt(style=style)
    ...
```

**Step 5: 运行确认通过**

Run: `pytest tests/agent/test_pipeline.py -v`
Expected: PASS

---

## Task 7: agent.py 瘦身门面 + ws.py 接线 + 清理

**Files:**
- Modify: `agent/agent.py`（重写为门面）
- Modify: `agent/ws.py`（build_agent 组装 AgentContext）
- Modify: `agent/__init__.py`（导出不变，AgentState 改从 pipeline 导入）
- Modify: `CLAUDE.md`（架构说明更新）
- Test: 全量回归

**Step 1: 重写 `agent/agent.py` 为门面**

```python
"""AliyaAgent — Agent 门面

对外 API 稳定层：包装 AgentPipeline 与 AgentContext，
WS / GUI / 测试层不感知内部管线化重构。
"""

from __future__ import annotations

from typing import Any

from agent.brain import Brain
from agent.config import AgentConfig
from agent.context import AgentContext
from agent.emotion.engine import EmotionEngine
from agent.pipeline import AgentPipeline, AgentState, TurnState
from agent.tools.registry import ToolRegistry


class AliyaAgent:
    """门面：对外暴露稳定 API，内部委托 AgentPipeline。"""

    def __init__(
        self,
        conversation_service: Any,
        tool_registry: ToolRegistry,
        memory_manager: Any | None = None,
        send_message: Any = None,
        tts_service: Any | None = None,
        audio_player: Any | None = None,
        audio_relay: Any = None,
        config: AgentConfig | None = None,
        confirm_callback: Any = None,
        prompt_manager: Any = None,
    ) -> None:
        from agent.prompts import get_prompt_manager
        from agent.prompts.style_switcher import get_style_switcher

        self._config = config or AgentConfig()

        # ── 大脑 / 情感引擎 / 认知引擎（与重构前一致的组装逻辑） ──
        brain = Brain(conversation_service, self._config)
        emotion = EmotionEngine(
            personality=self._config.emotion_personality,
            classifier_mode=self._config.emotion_classifier,
            max_samples_per_emotion=self._config.emotion_max_samples,
        )
        cognition = None
        if self._config.cognition_enabled:
            try:
                from agent.cognition.engine import CognitionConfig, CognitionEngine
                cognition = CognitionEngine(
                    CognitionConfig(maintenance_interval=self._config.cognition_maintenance_interval)
                )
            except Exception:
                cognition = None

        # 权限配置
        permission_config = self._init_permission_config()

        pm = prompt_manager or get_prompt_manager()
        self._ctx = AgentContext(
            conv=conversation_service,
            registry=tool_registry,
            config=self._config,
            prompt_manager=pm,
            style_switcher=get_style_switcher(),
            brain=brain,
            emotion=emotion,
            cognition=cognition,
            memory_manager=memory_manager,
            tts_service=tts_service,
            audio_player=audio_player,
            audio_relay=audio_relay,
            notify=send_message,
            confirm_callback=confirm_callback,
            permission_config=permission_config,
        )
        self._pipeline = AgentPipeline(self._ctx)
        # 自动 TTS 播放钩子
        self._pipeline.hooks.register("after_reply", self._speak)
        self._current_style = self._config.prompt_style

    # ── 对外 API（与重构前完全一致） ──

    @property
    def state(self) -> AgentState:
        return self._pipeline.state

    @property
    def turn(self) -> int:
        return self._pipeline.turn

    async def handle_user_message(self, text: str) -> None:
        await self._pipeline.handle_user_message(text)

    async def handle_clear_history(self, confirm: bool = False) -> None:
        if confirm:
            loop = asyncio.get_running_loop()
            reply = await loop.run_in_executor(
                None, lambda: input("确认清空历史？(y/n): ").strip().lower()
            )
            if reply != "y":
                return
        await self._ctx.conv.clear_history()

    def set_style(self, style: str) -> None:
        self._current_style = style
        self._pipeline._current_style = style

    def get_style(self) -> str:
        return self._current_style

    def set_emotion(self, feeling: str) -> None:
        self._ctx.emotion.set_emotion(feeling)

    def get_emotion(self) -> str:
        return self._ctx.emotion.current_emotion

    def get_emotion_state(self) -> dict:
        return self._ctx.emotion.get_state()

    async def warmup(self) -> None:
        await self._ctx.emotion.warmup()

    async def close_emotion_classifier(self) -> None:
        await self._ctx.emotion.close_classifier()

    def get_prompt_config(self) -> dict:
        return {
            "style": self._current_style,
            "emotion": self._ctx.emotion.current_emotion or "none",
            "styles": self._ctx.prompt_manager.list_styles(),
        }

    def get_cognition_status(self) -> dict:
        if not self._ctx.cognition:
            return {"enabled": False}
        return {"enabled": True, **self._ctx.cognition.get_status()}

    # ── 辅助 ──

    def _init_permission_config(self) -> Any:
        if not self._config.permission_config_path:
            return None
        try:
            from agent.tools.permission_config import PermissionConfigManager
            return PermissionConfigManager(self._config.permission_config_path)
        except Exception:
            return None

    async def _speak(self, reply: str) -> None:
        """自动 TTS 播放（after_reply 异步钩子），失败不影响主流程。"""
        if not self._ctx.tts_service:
            return
        try:
            from agent.tools.tts_speak import speak_text
            await speak_text(reply, self._ctx.make_tool_context())
        except Exception as e:
            logger.warning("TTS 自动播放失败（已忽略）: %s", e)


__all__ = ["AgentState", "AliyaAgent"]
```

注意：本文件需要 `import asyncio`、`from core.logger import get_logger`、`logger = get_logger(__name__)`，并按现有 ws.py / GUI 实际调用点校验 API 完整性（`cot_enabled` / `use_native_thinking` 属性若被 GUI 使用则从 `self._ctx.brain` 透传）。

**Step 2: 更新 `agent/ws.py` 的 build_agent**

`build_agent` 主体不变（仍创建 registry + config + prompt_manager + 返回 `AliyaAgent(...)`）。由于 `AliyaAgent` 构造签名未变，`ws.py` **无需修改**。仅需验证。

**Step 3: 更新 `agent/__init__.py`**

```python
"""Agent 模块"""

from agent.agent import AgentState, AliyaAgent
from agent.config import AgentConfig, agent_config_from_yaml
from agent.brain import BrainResult, parse_llm_response

__all__ = [
    "AgentConfig",
    "AgentState",
    "AliyaAgent",
    "BrainResult",
    "agent_config_from_yaml",
    "parse_llm_response",
]
```

（导出不变——`AgentState` 仍可从 `agent.agent` 导入，故无需改 `__init__.py` 内容；如 `tests/agent/test_agent.py` 从 `agent.agent import AgentState` 导入，保持兼容。）

**Step 4: 更新 CLAUDE.md**

将 `agent/` 层描述更新为：

```
**agent/ — AI Agent 引擎（管线式架构）**
- `agent.py` — `AliyaAgent` 门面（对外 API 稳定层）
- `pipeline.py` — `AgentPipeline` 管线编排器：阶段流转 + 钩子触发 + 状态通知
- `stages/` — 阶段模块：`assemble`（上下文组装）/ `think`（工具循环）/ `soul`（灵魂阶段）
- `hooks.py` — `HookRegistry` 钩子注册表（before_turn / after_tool / after_turn / after_reply）
- `context.py` — `AgentContext` 会话级统一依赖容器（含 `make_tool_context()`）
- `brain.py` — LLM 交互层（思考 / 灵魂 / 压缩 / 降级解析）
- `ws.py` — WebSocket 端点处理器，每个 WS 连接一个独立 `AliyaAgent` 实例
- `tools/` — 工具系统：`BaseTool`、`ToolRegistry`、`ToolContext`（由 `AgentContext` 派生）
```

并删除 CLAUDE.md 中"Brain 直接持有 ConversationService"等过时描述。

**Step 5: 全量回归**

Run: `pytest tests/agent tests/llm tests/memory -q`
Expected: PASS（全量，现有 139 用例 + 新增约 20 用例）

Run: `python -m compileall agent core`（语法检查）
Expected: 无错误

Run: `black --check agent core/llm tests/agent tests/llm`（格式检查；若失败运行 `black agent core/llm tests/agent tests/llm`）
Expected: 通过

---

## 验收清单

- [x] `agent/brain.py` 不再出现 `_lock` / `_context` / `_save()`
- [x] `ToolContext` 仅通过 `AgentContext.make_tool_context()` 构造
- [x] `agent/agent.py` 无情感/认知/TTS/记忆的业务逻辑（仅委托 + 钩子注册）
- [x] `AliyaAgent` 对外 API 与重构前一致（`handle_user_message` / `set_style` / `get_emotion_state` / `warmup` / `get_cognition_status`）
- [x] WS 协议消息类型不变
- [x] 全量测试通过（现有 + 新增）
- [x] 未执行任何 git commit（用户要求）

---

## 完成情况

> 执行日期：2026-08-06。全部 7 个任务完成，全量回归 620 passed。

### 计划外修正（执行中确认）

1. **补齐 3 处功能迁移**（用户确认：补齐为钩子订阅者，对齐设计文档第 2 节）：
   - 自动风格切换 → `AgentPipeline._hook_auto_switch_style`（BEFORE_TURN 钩子）
   - 情绪推进 → `AgentPipeline._hook_advance_emotion` / `_observe_emotion_async`（AFTER_TURN 钩子，fire-and-forget）
   - 灵魂阶段外部记忆检索 → `stages/soul.py` 的 `run_soul_phase(ctx, style, user_input)`（`memory_manager.get_relevant_memories` 注入 `[相关记忆]`）
2. **Task 6 实现缺陷修正**：`pipeline.py` 补 `from dataclasses import dataclass`；`HookRegistry.run` 兼容同步/异步处理器（cognition 钩子为同步方法），`run_later` 仅调度 awaitable。
3. **Task 5 笔误修正**：`stages/soul.py` 的 `__all__` 移除不存在的 `TOOL_RESULT_MARKER`（该常量在 `stages/think.py`）。
4. **通知调用约定**：`_notify` 恢复位置参数 `notify(data)`（与既有 `tests/agent/test_emotion.py` 集成测试一致）；计划新增 `test_pipeline.py` 读取方式相应改为 `c.args[0]`。
5. **既有回归修复**：`parse_llm_response` 第 3 层正则兜底改为未匹配时返回原文（修复重构前已存在的 `test_fallback_no_reply_match` 失败）。
6. **门面补属性**：`AliyaAgent` 保留 `cot_enabled` / `use_native_thinking` 属性透传（对外 API 一致）。
7. **测试适配**：`tests/agent/test_emotion.py` 4 个集成测试内部路径迁移（`agent._emotion` → `agent._ctx.emotion`、`agent._observe_emotion` → `agent._pipeline._observe_emotion`）。

### 验证记录

| 验证项 | 结果 |
|---|---|
| `pytest tests/agent tests/llm tests/memory -q` | 550 items，546 passed / 4 failed（test_emotion 内部路径，已适配后全过） |
| `pytest tests -q`（全量） | **620 passed**（既有约 600 + 新增 20） |
| `python -m compileall agent core` | 无错误 |
| `black --check`（新增文件 11 个） | 通过（7 个新增文件已格式化） |
| `agent/brain.py` 私有访问 grep | 无 `_conv._lock` / `_conv._context` / `_conv._save` |
| `agent/agent.py` 行数 | ~680 → 181（旧业务逻辑 grep 零残留） |

### 说明

- `ws.py` 的 `build_agent` 签名与接线无需修改（测试全部通过）。
- `agent/__init__.py` 导出已与计划目标一致，未改动。
- `CLAUDE.md` 已在本次会话前被删除（`git status` 确认），计划 Step 4 跳过。
- 全量 `black --check` 仍有 47 个**既有文件**不合规（重构前即存在，非本次引入），本次仅保证新增文件合规。
