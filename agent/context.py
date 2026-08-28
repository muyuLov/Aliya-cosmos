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
