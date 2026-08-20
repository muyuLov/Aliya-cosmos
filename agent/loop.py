"""两阶段 FC 对话循环（TOOL_PHASE + SOUL_PHASE）

参考 Cyrene-Agent 的 two-phase-fc-loop.ts：
- TOOL_PHASE：携带 tools schema 让 LLM 决策工具调用，逐轮执行直至无工具调用
- SOUL_PHASE：注入人设 + 记忆 + 情绪 + 工具结果摘要，流式产出最终回复
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import TYPE_CHECKING, Any, AsyncGenerator

from agent.context import ContextBuilder, inject_soul_context
from agent.events import (
    CONFIRM_REQUEST,
    ERROR,
    NOTICE,
    AgentEvent,
    ProtocolEvent,
    RunFinished,
    RunStarted,
    StepFinished,
    StepStarted,
    TextMessageDelta,
    TextMessageEnd,
    TextMessageStart,
    ToolCallEnd,
    ToolCallResult,
    ToolCallStart,
)
from agent.tools import Permission, PermissionChecker, ToolContext, ToolRegistry

if TYPE_CHECKING:
    from agent.emotion.engine import EmotionEngine

_CONFIRM_REJECT_TEXT = "[已拒绝] 用户未授权执行该工具"
_NOT_REGISTERED_TEXT = "[工具未注册]"


class AgentLoop:
    """单个会话的两阶段循环，逐条产出事件（AgentEvent 或 ProtocolEvent）。"""

    def __init__(
        self,
        service,
        registry: ToolRegistry,
        checker: PermissionChecker,
        context: ContextBuilder,
        *,
        max_tool_rounds: int = 20,
        tool_timeout: float = 30.0,
        confirm_timeout: float = 30.0,
        memory: Any = None,
        emotion_engine: EmotionEngine | None = None,
    ) -> None:
        self.service = service
        self.registry = registry
        self.checker = checker
        self.context = context
        self.memory = memory
        self.emotion_engine = emotion_engine
        self.max_tool_rounds = max_tool_rounds
        self.tool_timeout = tool_timeout
        self.confirm_timeout = confirm_timeout
        self._abort = False
        # 工具确认挂起表：call_id -> Future[bool]
        self.pending_confirmations: dict[str, asyncio.Future[bool]] = {}
        # 绑定 emotion_engine 到 service（如果提供了）
        if self.emotion_engine is not None:
            self.emotion_engine.bind_service(service)

    # ── 中断控制 ────────────────────────────────────────────────────────────

    def interrupt(self) -> None:
        self._abort = True

    def reset_abort(self) -> None:
        self._abort = False

    async def resolve_confirmation(self, call_id: str, allowed: bool) -> None:
        """外部（WS 层）响应用户确认，解除挂起。"""
        fut = self.pending_confirmations.get(call_id)
        if fut is not None and not fut.done():
            fut.set_result(allowed)

    def _new_confirmation(self, call_id: str) -> asyncio.Future[bool]:
        fut = asyncio.get_running_loop().create_future()
        self.pending_confirmations[call_id] = fut
        return fut

    # ── 主入口 ───────────────────────────────────────────────────────────────

    async def submit_user_message(self, text: str) -> AsyncGenerator[AgentEvent | ProtocolEvent, None]:
        """异步生成器：逐条产出事件，由 WS 网关/渠道消费。"""
        self.reset_abort()
        yield RunStarted(session_id=self.service.conversation_id)

        tool_summary_parts: list[str] = []
        interrupted = False

        # ── TOOL_PHASE ────────────────────────────────────────────────────────
        try:
            async for event in self._tool_phase(text, tool_summary_parts):
                yield event
        except Exception as exc:  # pragma: no cover - 兜底
            yield ProtocolEvent(type=ERROR, payload={"message": f"工具阶段异常: {exc}"})

        if self._abort:
            interrupted = True

        # ── SOUL_PHASE ────────────────────────────────────────────────────────
        try:
            async for event in self._soul_phase(text, tool_summary_parts, interrupted):
                yield event
        except Exception as exc:  # pragma: no cover - 兜底
            yield ProtocolEvent(type=ERROR, payload={"message": f"回复生成异常: {exc}"})
            yield RunFinished(session_id=self.service.conversation_id)

    # ── TOOL_PHASE ───────────────────────────────────────────────────────────

    async def _tool_phase(
        self, text: str, tool_summary_parts: list[str]
    ) -> AsyncGenerator[AgentEvent | ProtocolEvent, None]:
        yield StepStarted(phase="tool")
        # 工具阶段 system：工具调度规则（tools_system.md）
        await self.service.set_system_prompt(self.context.build_tool_system())
        timeout_count = 0

        for _round in range(self.max_tool_rounds):
            if self._abort:
                break
            try:
                response = await asyncio.wait_for(
                    self.service.asend_chat(
                        text,
                        store_history=(_round == 0),
                        tools=self.registry.build_tools_schema(),
                        tool_choice="auto",
                        commit_content=False,
                    ),
                    timeout=self.tool_timeout,
                )
            except asyncio.TimeoutError:
                timeout_count += 1
                if timeout_count >= 3:
                    tool_summary_parts.append("[任务中断] LLM 工具决策连续超时")
                    break
                continue
            except Exception as exc:  # pragma: no cover - 兜底
                tool_summary_parts.append(f"[工具阶段失败] {exc}")
                break

            timeout_count = 0
            tool_calls = response.tool_calls or []
            if not tool_calls:
                break

            for call in tool_calls:
                if self._abort:
                    break
                name = (call.get("function") or {}).get("name", "")
                call_id = call.get("id", "")
                arguments = self._safe_arguments(call)
                tool_id = name  # 注册表按 name 匹配（内置工具 id == name）

                # 权限检查
                entry = self.registry.get(tool_id)
                risk = entry[0].risk if entry else "safe"
                permission = self.checker.check(tool_id, risk=risk)

                if permission == Permission.DENY:
                    denied = "[已拒绝] 该工具被禁止使用"
                    await self.service.append_message("tool", denied, tool_call_id=call_id)
                    tool_summary_parts.append(f"{name}: {denied}")
                    continue

                if permission == Permission.CONFIRM:
                    # 发起确认请求并挂起等待用户响应；超时视为拒绝
                    fut = self._new_confirmation(call_id)
                    yield ProtocolEvent(
                        type=CONFIRM_REQUEST,
                        payload={"tool": name, "params": arguments, "call_id": call_id},
                    )
                    try:
                        allowed = await asyncio.wait_for(fut, timeout=self.confirm_timeout)
                    except asyncio.TimeoutError:
                        allowed = False
                    finally:
                        self.pending_confirmations.pop(call_id, None)
                    if not allowed:
                        await self.service.append_message("tool", _CONFIRM_REJECT_TEXT, tool_call_id=call_id)
                        tool_summary_parts.append(f"{name}: {_CONFIRM_REJECT_TEXT}")
                        continue
                    if self._abort:
                        break

                # 执行工具
                yield ToolCallStart(call_id=call_id, tool_name=name, arguments=arguments)
                ctx = ToolContext(
                    user_query=text,
                    conversation_id=self.service.conversation_id,
                    memory=self.memory,
                )
                output = await self._execute_tool(tool_id, ctx, arguments)
                yield ToolCallResult(call_id=call_id, output=output)
                yield ToolCallEnd(call_id=call_id)
                await self.service.append_message("tool", output, tool_call_id=call_id)
                tool_summary_parts.append(f"{name}: {self._truncate(output)}")

            if self._abort:
                break

        yield StepFinished(phase="tool")

    def _safe_arguments(self, call: dict) -> dict[str, Any]:
        raw = (call.get("function") or {}).get("arguments", "{}")
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    async def _execute_tool(self, tool_id: str, ctx: ToolContext, arguments: dict) -> str:
        entry = self.registry.get(tool_id)
        if not entry:
            return _NOT_REGISTERED_TEXT
        _, executor = entry
        try:
            return await executor(ctx, arguments)
        except Exception as exc:
            return f"[工具执行失败] {exc}"

    @staticmethod
    def _truncate(text: str, limit: int = 200) -> str:
        text = text.strip()
        return text if len(text) <= limit else text[:limit] + "…"

    # ── SOUL_PHASE ───────────────────────────────────────────────────────────

    async def _soul_phase(
        self, text: str, tool_summary_parts: list[str], interrupted: bool
    ) -> AsyncGenerator[AgentEvent | ProtocolEvent, None]:
        yield StepStarted(phase="soul")
        tool_summary = "\n".join(tool_summary_parts) if tool_summary_parts else ""

        # 记忆注入：调用 get_relevant_memories 检索相关五元组，格式化为可读文本；
        # memory 不可用或检索失败时降级为空，不阻塞主流程
        memory_text = ""
        if self.memory is not None:
            try:
                quintuples = await self.memory.get_relevant_memories(query=text, limit=3)
                if quintuples:
                    memory_text = "\n".join(
                        f"- {h} {r} {t}" for h, _ht, r, t, _tt in quintuples
                    )
            except Exception:  # pragma: no cover - 记忆检索失败不阻塞
                memory_text = ""

        await inject_soul_context(
            self.service,
            self.context,
            memory_text=memory_text,
            emotion_patch="",
            tool_summary=tool_summary,
        )

        message_id = str(uuid.uuid4())
        yield TextMessageStart(message_id=message_id)
        full_parts: list[str] = []
        try:
            async for token in self.service.astream_send(text, store_history=False):
                if self._abort:
                    break
                full_parts.append(token)
                yield TextMessageDelta(message_id=message_id, text=token)
        except Exception as exc:  # pragma: no cover - 兜底
            yield ProtocolEvent(type=ERROR, payload={"message": f"流式生成失败: {exc}"})
            yield TextMessageEnd(message_id=message_id, full_text="".join(full_parts))
            yield RunFinished(session_id=self.service.conversation_id)
            return

        full_reply = "".join(full_parts)
        if interrupted or self._abort:
            full_reply = full_reply or "[已停止回复]"
            yield ProtocolEvent(type=NOTICE, payload={"message": "已停止回复"})
        yield TextMessageEnd(message_id=message_id, full_text=full_reply)
        yield RunFinished(session_id=self.service.conversation_id)

        # 收尾副作用：写入长期记忆 + 情绪引擎更新（失败不抛异常）
        if self.memory is not None:
            try:
                await self.memory.add_conversation_memory(
                    text, full_reply, session_id=self.service.conversation_id
                )
            except Exception:  # pragma: no cover - 记忆写入失败不阻塞
                pass

        # 情绪引擎：观察本轮对话 → 平滑 → 注入语气到下一轮
        if self.emotion_engine is not None:
            try:
                history = await self.service.get_history()
                messages = [{"role": m.role, "content": m.content} for m in history[-6:]]
                await self.emotion_engine.on_turn_complete(messages)
            except Exception:  # pragma: no cover - 情绪观察失败不阻塞
                pass
