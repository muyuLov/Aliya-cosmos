"""上下文构建器：分阶段注入人设/记忆/情绪/工具调度规则

复用 core/llm 的 ConversationService.set_system_prompt 作为 system 注入宿主。
"""

from __future__ import annotations

from pathlib import Path


class ContextBuilder:
    """读取 data/prompts 下提示词并拼接两阶段 system prompt。"""

    def __init__(self, prompts_dir: str = "data/prompts") -> None:
        self._dir = Path(prompts_dir)

    def _read(self, name: str) -> str:
        return (self._dir / name).read_text(encoding="utf-8")

    def build_tool_system(self) -> str:
        """返回工具调度规则全文（tools_system.md）。

        工具列表不拼接进 system prompt——由 LLM API 的 tools schema 传递。
        """
        return self._read("tools_system.md")

    def build_soul_system(
        self,
        *,
        memory_text: str = "",
        emotion_patch: str = "",
        tool_summary: str = "",
    ) -> str:
        """按顺序拼接：人设（identity+soul+tone-rules）→ 记忆 → 情绪 → 工具结果摘要。"""
        parts = [
            self._read("identity.md"),
            self._read("soul.md"),
            self._read("tone-rules.md"),
        ]
        if memory_text:
            parts.append(f"## 相关记忆\n{memory_text}")
        if emotion_patch:
            parts.append(f"## 情绪状态\n{emotion_patch}")
        if tool_summary:
            parts.append(f"## 本轮工具结果摘要\n{tool_summary}")
        return "\n\n".join(parts)


async def inject_soul_context(
    service,
    builder: ContextBuilder,
    *,
    memory_text: str,
    emotion_patch: str,
    tool_summary: str = "",
) -> None:
    """组装灵魂阶段 system prompt 并注入 ConversationService。"""
    system = builder.build_soul_system(
        memory_text=memory_text,
        emotion_patch=emotion_patch,
        tool_summary=tool_summary,
    )
    await service.set_system_prompt(system)
