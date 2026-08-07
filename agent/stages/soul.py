"""阶段 3：灵魂阶段（人格上下文 + 记忆注入 + 最终回复）"""

from __future__ import annotations

import logging

from agent.context import AgentContext
from agent.prompts import ALL_STYLES, StyleName

logger = logging.getLogger(__name__)

_AUTONOMY_HEADER = "[主动建议]"
_MEMORY_HEADER = "[相关记忆]"
_SUMMARY_HEADER = "[历史对话摘要]"


async def run_soul_phase(
    ctx: AgentContext,
    style: str = "",
    user_input: str = "",
) -> str:
    """切换到灵魂阶段（恢复人格、注入记忆、设置情绪补丁），生成最终回复。

    Args:
        ctx: 依赖容器
        style: 当前表达风格名（default / lively / healing / sweet）
        user_input: 本轮用户输入（用于外部记忆管理器检索相关记忆）
    """
    style_name: StyleName = style if style in ALL_STYLES else "default"
    soul_system = ctx.prompt_manager.build_soul_system_prompt(style=style_name)
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
        except Exception as e:
            logger.debug("[SoulPhase] 主动建议获取失败（忽略）: %s", e)
    # 外部记忆管理器检索（GRAG）：用本轮用户输入召回相关记忆
    if ctx.memory_manager:
        try:
            query = (user_input or "").strip()
            if query:
                related = await ctx.memory_manager.get_relevant_memories(query, limit=3)
                if related:
                    extra_parts.append(f"{_MEMORY_HEADER}\n{str(related)}")
        except Exception as e:
            logger.debug("[SoulPhase] 外部记忆检索失败（忽略）: %s", e)

    memory_context = "\n\n".join(extra_parts) if extra_parts else ""
    await ctx.conv.set_context_injection(memory=memory_context)

    logger.debug(
        "[SoulPhase] 已完成 | style=%s | emotion=%s | has_summary=%s | has_memory=%s",
        style,
        current_emotion or "none",
        bool(ctx.brain.compressed_context),
        bool(memory_context),
    )
    return await ctx.brain.generate_soul_reply()


__all__ = ["run_soul_phase"]
