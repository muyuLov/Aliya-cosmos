"""上下文构建器（NarrativeContextBuilder）：组装主叙事 JSON 上下文

组装优先级：Canon → recentScript → continuitySnapshot →
分层记忆召回 → Alter 氛围 → Agency 容量 → 结构化输出指令 → 时间端点。
字符预算约束。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.time import story_local_time_context, resolve_timezone

# 默认最大上下文字符数
_DEFAULT_MAX_CONTEXT_CHARS = 8000


class NarrativeContextBuilder:
    """组装主叙事 JSON 上下文。"""

    def __init__(
        self,
        prompts_dir: str = "data/prompts",
        max_context_chars: int = _DEFAULT_MAX_CONTEXT_CHARS,
    ) -> None:
        self._dir = Path(prompts_dir)
        self.max_context_chars = max_context_chars
        self.available_mcp_servers: list[str] = []

    def _read(self, name: str) -> str:
        try:
            return (self._dir / name).read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    async def build_context(
        self,
        user_input: str,
        story_id: str,
        participant_id: str,
        *,
        memories: list[dict[str, Any]] | None = None,
        alter: dict[str, Any] | None = None,
        recent_script: list[str] | None = None,
        agency: dict[str, Any] | None = None,
        timezone_str: str | None = None,
    ) -> dict[str, Any]:
        """组装完整的主叙事 JSON 上下文。"""
        tz = resolve_timezone(timezone_str)
        now = datetime.now(timezone.utc)
        time_ctx = story_local_time_context(now, tz)

        ctx: dict[str, Any] = {
            # 用户输入
            "userInput": user_input,

            # 故事标识
            "storyId": story_id,
            "participantId": participant_id,

            # Canon 层（人设）
            "persona": self._read("identity.md"),
            "soul": self._read("soul.md"),
            "toneRules": self._read("tone-rules.md"),

            # 近期剧本
            "recentScript": recent_script or [],

            # 记忆召回
            "memories": memories or [],

            # Alter 氛围
            "alter": alter or {"direction": "", "intensity": 0.0},

            # Agency 容量
            "agency": agency or {"allowed": True, "load": "normal"},

            # 结构化输出指令
            "outputFormat": {
                "type": "json_object",
                "fields": ["script", "reply", "memories", "intents", "actions"],
            },

            # 时间端点
            "time": {
                "utc": now.isoformat(),
                "nowLocal": int(time_ctx.get("hour", 0)) * 60 + int(time_ctx.get("minute", 0)),
                "period": str(time_ctx.get("period", "unknown")),
                "daylightExpectation": time_ctx.get("daylight_expectation", ""),
                "weekday": str(time_ctx.get("weekday", "")),
            },
        }

        return ctx

    def build_system_prompt(self) -> str:
        """构建主叙事 system prompt。"""
        parts = [
            self._read("identity.md"),
            self._read("soul.md"),
            self._read("tone-rules.md"),
        ]
        # 移除空字符串
        parts = [p for p in parts if p]
        return "\n\n".join(parts)

    # 向后兼容：旧 ContextBuilder 接口

    def build_tool_system(self) -> str:
        return self._read("tools_system.md")

    def build_mcp_system(self) -> str:
        if not self.available_mcp_servers:
            return ""
        names = "、".join(self.available_mcp_servers)
        return f"## 可用外部服务（MCP）\n当前可调用以下 MCP 服务提供的工具：{names}。"

    def build_soul_system(
        self,
        *,
        memory_text: str = "",
        emotion_patch: str = "",
        tool_summary: str = "",
    ) -> str:
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


# 向后兼容别名
ContextBuilder = NarrativeContextBuilder


async def inject_soul_context(
    service,
    builder: NarrativeContextBuilder,
    *,
    memory_text: str,
    emotion_patch: str,
    tool_summary: str = "",
) -> None:
    """组装灵魂阶段 system prompt 并注入 ConversationService（向后兼容）。"""
    system = builder.build_soul_system(
        memory_text=memory_text,
        emotion_patch=emotion_patch,
        tool_summary=tool_summary,
    )
    await service.set_system_prompt(system)
