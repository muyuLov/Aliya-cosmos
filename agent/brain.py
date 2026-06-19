from __future__ import annotations

import time
from typing import TYPE_CHECKING, Self

from agent.models import AgentResponse, Skill, ToolCall, compute_tool_signature
from agent.response_parser import ResponseParser
from agent.skill_loader import SkillLoader
from agent.tools.base import InternalTool
from core.llm import ConversationService, create_from_config
from core.logger import get_logger

if TYPE_CHECKING:
    from memory.memory_manager import GRAGMemoryManager

logger = get_logger(__name__)


class BrainEngine:
    def __init__(
        self,
        conversation_service: ConversationService,
        parser: ResponseParser,
        skill_loader: SkillLoader,
        tool_descriptions: str,
        memory_manager: GRAGMemoryManager | None = None,
        max_iterations: int = 5,
        internal_tools: dict[str, InternalTool] | None = None,
    ) -> None:
        self._conversation_service = conversation_service
        self._parser = parser
        self._skill_loader = skill_loader
        self._tool_descriptions = tool_descriptions
        self._memory_manager = memory_manager
        self._max_iterations = max_iterations
        self._internal_tools: dict[str, InternalTool] = internal_tools or {}

    @classmethod
    def from_config(
        cls,
        config_path: str = "data/config/main.yml",
        system_prompt_file: str = "agent/prompts/aliya_system_prompt.md",
        tool_descriptions: str = "",
        memory_manager: GRAGMemoryManager | None = None,
        max_iterations: int = 5,
        internal_tools: dict[str, InternalTool] | None = None,
    ) -> Self:
        service = create_from_config(
            config_path,
            system_prompt_file=system_prompt_file,
        )
        return cls(service, ResponseParser(), SkillLoader(), tool_descriptions, memory_manager, max_iterations, internal_tools=internal_tools)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._conversation_service.aclose()
        logger.debug("BrainEngine 已释放资源")

    async def think(
        self,
        user_input: str,
        memory_context: str = "",
        stream: bool = False,
        tool_results: str = "",
    ) -> AgentResponse:
        t_start = time.monotonic()
        self._conversation_service.reset_usage()

        skill_text = self._build_skill_text(user_input)
        memory_context = self._merge_tool_results(memory_context, tool_results)
        pending_agent_tools: list[ToolCall] = []
        injected_counts: dict[str, int] = {}
        prev_tool_signature: str | None = None

        try:
            for i in range(self._max_iterations):
                t_round = time.monotonic()
                await self._conversation_service.set_context_injection(
                    skills=skill_text,
                    tools=self._tool_descriptions,
                    memory=memory_context,
                )

                store_history = (i == 0) and not tool_results
                try:
                    if stream:
                        raw_reply = ""
                        async for chunk in self._conversation_service.astream_send(
                            user_input, store_history=store_history
                        ):
                            raw_reply += chunk
                    else:
                        raw_reply = await self._conversation_service.asend(
                            user_input, store_history=store_history
                        )
                    response = self._parser.parse(raw_reply)
                except Exception:
                    logger.exception("Brain 第%d轮 LLM 调用失败", i + 1)
                    if pending_agent_tools:
                        return AgentResponse("我遇到了一些问题，让我重新想想……", pending_agent_tools, **_usage(self))
                    raise

                round_ms = (time.monotonic() - t_round) * 1000

                if not response.tool_calls:
                    logger.info("Brain 第%d轮 完成 | tools=none | reply=%.60s | %dms",
                                i + 1, response.reply_text, round_ms)
                    return AgentResponse(response.reply_text, pending_agent_tools, **_usage(self))

                has_internal = any(
                    tc.tool_name in self._internal_tools for tc in response.tool_calls
                )

                if not has_internal:
                    external = [tc for tc in response.tool_calls if tc.tool_name != "reply"]
                    current_signature = compute_tool_signature(external)
                    if prev_tool_signature == current_signature and external:
                        logger.warning("Brain 检测到循环工具调用 | signature=%s", current_signature[:100])
                        return AgentResponse("我需要重新考虑一下这个问题……", [], **_usage(self))
                    prev_tool_signature = current_signature
                    pending_agent_tools.extend(external)
                    logger.info("Brain 第%d轮 完成 | tools=%s | reply=%.60s | %dms",
                                i + 1, [tc.tool_name for tc in external], response.reply_text, round_ms)
                    return AgentResponse(response.reply_text, pending_agent_tools, **_usage(self))

                await self._execute_internal_tools(response, pending_agent_tools, injected_counts)

                if injected_counts:
                    logger.info("Brain 第%d轮 内部工具 | prefixes=%s | external=%s | %dms",
                                i + 1, list(injected_counts),
                                [tc.tool_name for tc in pending_agent_tools],
                                round_ms)

            total_ms = (time.monotonic() - t_start) * 1000
            logger.warning("Brain 达到最大轮数 %s | pending=%s | %dms",
                           self._max_iterations,
                           [tc.tool_name for tc in pending_agent_tools],
                           total_ms)
            return AgentResponse("让我再想想……", pending_agent_tools, **_usage(self))

        finally:
            # 内部工具（如 MemoryQuery）注入的消息仅用于本轮迭代辅助 LLM 推理，
            # 其语义已体现在最终 AgentResponse.reply_text 中。所有正常/异常
            # 返回路径均通过此 finally 清理，确保临时消息不残留到下一轮对话。
            for prefix, count in injected_counts.items():
                try:
                    await self._conversation_service.discard_messages(prefix, count)
                except Exception:
                    logger.exception("清理注入消息失败: prefix=%s", prefix)

    # ── 内部工具执行 ─────────────────────────────────────────────────────────

    async def _execute_internal_tools(
        self,
        response: AgentResponse,
        pending_agent_tools: list[ToolCall],
        injected_counts: dict[str, int],
    ) -> None:
        """执行本轮内部工具，结果注入对话历史；非内部工具加入待分发列表。"""
        seen_pending = set()
        for tc in response.tool_calls:
            if tc.tool_name in self._internal_tools:
                tool = self._internal_tools[tc.tool_name]
                err = tool.validate_args(tc.arguments)
                if err:
                    text = f"{tool.message_prefix}参数错误: {err}"
                else:
                    text = await tool.execute_and_format(tc.arguments)
                try:
                    await self._conversation_service.append_message(
                        "assistant", text,
                        metadata={"injected": True, "prefix": tool.message_prefix},
                    )
                except Exception:
                    logger.exception("Brain 内部工具消息注入失败")
                injected_counts[tool.message_prefix] = injected_counts.get(tool.message_prefix, 0) + 1
            elif tc.tool_name != "reply":
                key = (tc.tool_name, str(sorted(tc.arguments.items())))
                if key not in seen_pending:
                    seen_pending.add(key)
                    pending_agent_tools.append(tc)

    # ── 上下文辅助 ────────────────────────────────────────────────────────────

    def _build_skill_text(self, user_input: str) -> str:
        active_skills = self._activate_skills(user_input)
        active_skill_names = [s.name for s in active_skills]
        logger.info("Brain 思考: input=%.60s | skills=%s",
                    user_input, active_skill_names)
        return "\n\n".join(skill.instructions for skill in active_skills)

    @staticmethod
    def _merge_tool_results(memory_context: str, tool_results: str) -> str:
        if not tool_results:
            return memory_context
        if memory_context:
            return f"{memory_context}\n\n## Tool Execution Results\n{tool_results}"
        return f"## Tool Execution Results\n{tool_results}"

    async def clear_history(self) -> None:
        await self._conversation_service.clear_history()

    def _activate_skills(self, user_input: str) -> list[Skill]:
        lowered = user_input.lower()
        matched: list[Skill] = []
        for skill in self._skill_loader.load_all():
            if any(pattern.search(lowered) for pattern in skill.trigger_patterns):
                matched.append(skill)
        return matched


def _usage(brain: BrainEngine) -> dict:
    u = brain._conversation_service.usage
    return dict(prompt_tokens=u.prompt_tokens, completion_tokens=u.completion_tokens, total_tokens=u.total_tokens)
