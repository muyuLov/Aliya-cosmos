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
