"""阶段 2：工具阶段循环（Think → Act → Observe）"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable

from agent.context import AgentContext
from agent.hooks import HookPoint, HookRegistry

if TYPE_CHECKING:
    from agent.pipeline import TurnState

logger = logging.getLogger(__name__)

# 注入到临时消息中的前缀标记，用于后续 cleanup
TOOL_RESULT_MARKER = "tool_result"


async def run_tool_loop(
    ctx: AgentContext,
    text: str,
    state: "TurnState",
    notify: Callable[[dict], Awaitable[None]] | None = None,
    hooks: HookRegistry | None = None,
) -> str:
    """执行 Think → Act → Observe 循环，返回最终回复文本（可能为空）。

    Args:
        ctx: 依赖容器
        text: 用户输入
        state: 轮次状态（pipeline 持有，本函数修改 turn / 调用标记）
        notify: 通知回调
        hooks: 钩子注册表（在 after_tool 点触发认知学习等横切能力）
    """
    result = await ctx.brain.think(text)
    final_reply = result.reply

    while result.tool_calls:
        state.has_called_tools = True
        state.turn += 1

        if state.turn > ctx.config.max_turns:
            logger.warning(
                "[Plan] 达到最大循环轮次，强制进入灵魂阶段 | turn=%d | max_turns=%d",
                state.turn,
                ctx.config.max_turns,
            )
            break

        tools_list = [c.get("name") for c in result.tool_calls]
        logger.debug("[Tool] 执行工具调用 | turn=%d | tools=%s", state.turn, tools_list)
        if notify:
            await notify(
                {
                    "type": "brain_progress",
                    "message": f"执行工具调用（第 {state.turn} 轮）",
                    "tools": tools_list,
                }
            )

        tool_results = await ctx.registry.dispatch_all(result.tool_calls, ctx)

        # 认知学习（after_tool 钩子）：需求更新、情景记忆、世界模型、自我模型
        if hooks:
            for name, tres in tool_results:
                await hooks.run(HookPoint.AFTER_TOOL, name, tres)
        elif ctx.cognition:
            # 无钩子调用方（如单测直连）的降级路径
            for name, tres in tool_results:
                detail = tres.data if tres.success else tres.error
                ctx.cognition.after_tool(name, tres.success, detail=detail)

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
            await notify(
                {
                    "type": "brain_progress",
                    "message": f"根据工具结果继续推理（第 {state.turn} 轮）",
                }
            )
        result = await ctx.brain.think_with_context()
        final_reply = result.reply

        if notify:
            await notify(
                {
                    "type": "brain_refine",
                    "reply": result.reply,
                    "thought": result.thought,
                    "turn": state.turn,
                }
            )

    return final_reply
