from __future__ import annotations

import time
from typing import TYPE_CHECKING, Self

from agent.models import AgentResponse, ToolCall, compute_tool_signature
from agent.response_parser import ResponseParser
from agent.skill_loader import SkillLoader
from agent.tools.base import InternalTool, ToolCategory
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
        visible_categories: set[ToolCategory] | None = None,
    ) -> None:
        self._conversation_service = conversation_service
        self._parser = parser
        self._skill_loader = skill_loader
        self._tool_descriptions = tool_descriptions
        self._memory_manager = memory_manager
        self._max_iterations = max_iterations
        self._internal_tools: dict[str, InternalTool] = internal_tools or {}
        self._visible_categories = {ToolCategory.CORE} if visible_categories is None else visible_categories
        self._skill_listing: str | None = None  # 技能列表缓存

    @classmethod
    def from_config(
        cls,
        config_path: str = "data/config/main.yml",
        system_prompt_file: str = "agent/prompts/aliya_system_prompt.md",
        tool_descriptions: str = "",
        memory_manager: GRAGMemoryManager | None = None,
        max_iterations: int = 5,
        internal_tools: dict[str, InternalTool] | None = None,
        visible_categories: set[ToolCategory] | None = None,
    ) -> Self:
        service = create_from_config(config_path, system_prompt_file=system_prompt_file)
        return cls(service, ResponseParser(), SkillLoader(), tool_descriptions,
                   memory_manager, max_iterations, internal_tools=internal_tools,
                   visible_categories=visible_categories)

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
        await self._conversation_service.reset_usage()

        skill_text = self._get_skill_listing()
        memory_context = self._merge_tool_results(memory_context, tool_results)
        pending: list[ToolCall] = []
        injected: dict[str, int] = {}
        prev_sig: str | None = None

        try:
            for i in range(self._max_iterations):
                await self._conversation_service.set_context_injection(
                    skills=skill_text, tools=self._tool_descriptions, memory=memory_context,
                )

                store = (i == 0) and not tool_results
                try:
                    if stream:
                        raw = ""
                        async for chunk in self._conversation_service.astream_send(user_input, store_history=store):
                            raw += chunk
                    else:
                        raw = await self._conversation_service.asend(user_input, store_history=store)
                    resp = self._parser.parse(raw)
                except Exception:
                    logger.exception("Brain 第%d轮 LLM 调用失败", i + 1)
                    if pending:
                        return AgentResponse("我遇到了一些问题，让我重新想想……", pending, **await _usage(self))
                    raise

                if not resp.tool_calls:
                    return AgentResponse(resp.reply_text, pending, **await _usage(self))

                # 纯外部工具：返回给 AliyaAgent 分发
                if not any(tc.tool_name in self._internal_tools for tc in resp.tool_calls):
                    external = [tc for tc in resp.tool_calls if tc.tool_name != "reply"]
                    sig = compute_tool_signature(external)
                    if prev_sig == sig and external:
                        logger.warning("Brain 检测到循环工具调用 | signature=%s", sig[:100])
                        return AgentResponse("我需要重新考虑一下这个问题……", [], **await _usage(self))
                    prev_sig = sig
                    pending.extend(external)
                    return AgentResponse(resp.reply_text, pending, **await _usage(self))

                # 含内部工具：执行并注入对话历史，继续下一轮
                await self._execute_internal_tools(resp, pending, injected)

            logger.warning("Brain 达到最大轮数 %s | pending=%s | %dms",
                           self._max_iterations, [tc.tool_name for tc in pending],
                           (time.monotonic() - t_start) * 1000)
            return AgentResponse("让我再想想……", pending, **await _usage(self))

        finally:
            for prefix, count in injected.items():
                try:
                    await self._conversation_service.discard_messages(prefix, count)
                except Exception:
                    logger.exception("清理注入消息失败: prefix=%s", prefix)

    async def _execute_internal_tools(
        self, resp: AgentResponse, pending: list[ToolCall], injected: dict[str, int]
    ) -> None:
        """执行本轮内部工具，结果注入对话历史；非内部工具加入待分发列表。"""
        for tc in resp.tool_calls:
            if tc.tool_name in self._internal_tools:
                tool = self._internal_tools[tc.tool_name]
                err = tool.validate_args(tc.arguments)
                text = f"{tool.message_prefix}参数错误: {err}" if err else await tool.execute_and_format(tc.arguments)
                try:
                    await self._conversation_service.append_message(
                        "assistant", text, metadata={"injected": True, "prefix": tool.message_prefix},
                    )
                except Exception:
                    logger.exception("Brain 内部工具消息注入失败")
                injected[tool.message_prefix] = injected.get(tool.message_prefix, 0) + 1
            elif tc.tool_name != "reply":
                pending.append(tc)

    def _get_skill_listing(self) -> str:
        """所有已启用的技能名+描述，缓存直到 SkillLoader.reload() 被调用。"""
        if self._skill_listing is not None:
            return self._skill_listing
        skills = [s for s in self._skill_loader.load_all() if s.enabled]
        if not skills:
            self._skill_listing = ""
        else:
            self._skill_listing = "\n".join(s.listing for s in sorted(skills, key=lambda s: s.priority))
        return self._skill_listing

    @staticmethod
    def _merge_tool_results(memory_context: str, tool_results: str) -> str:
        if not tool_results:
            return memory_context
        if memory_context:
            return f"{memory_context}\n\n## Tool Execution Results\n{tool_results}"
        return f"## Tool Execution Results\n{tool_results}"

    async def clear_history(self) -> None:
        await self._conversation_service.clear_history()


async def _usage(brain: BrainEngine) -> dict:
    u = await brain._conversation_service.get_usage()
    return dict(prompt_tokens=u.prompt_tokens, completion_tokens=u.completion_tokens, total_tokens=u.total_tokens)
