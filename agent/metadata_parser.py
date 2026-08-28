"""结构化输出解析器（metadata_parser）

解析主叙事 LLM 的 JSON 回复，分离 prose(script) 与 transport(reply/actions)，
防御性归一化（normalize），失败降级纯文本模式。

关键防御：
- script 截断到 max_script_characters
- alter 取整限幅 -5..5
- seen 强制 boolean；seen=false 强制 reply.mode=none
- intents 最多 8 条
- mode=delayed 且 sendAt 越界降级 mode=none
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from core.logger import get_logger

logger = get_logger(__name__)

# 默认最大 script 字符数
_DEFAULT_MAX_SCRIPT = 5000


@dataclass
class NarrativeOutput:
    """主叙事输出的解析结果。"""

    script: str = ""
    has_required_script: bool = False
    reply_mode: str = "immediate"  # immediate | delayed | none
    reply_content: str = ""
    seen: bool = True
    alter: int | None = None
    memories: list[dict[str, Any]] = field(default_factory=list)
    intents: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    state_patch: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def parse_narrative_output(
    raw_text: str,
    *,
    max_script_characters: int = _DEFAULT_MAX_SCRIPT,
) -> NarrativeOutput:
    """解析主叙事 LLM 输出。

    优先尝试 JSON 解析；失败时降级为纯文本模式。
    """
    # 尝试 JSON 解析
    parsed = _try_parse_json(raw_text)
    if parsed is None:
        return _fallback_plain_text(raw_text)

    return _normalize(parsed, max_script_characters=max_script_characters)


def _try_parse_json(text: str) -> dict[str, Any] | None:
    """尝试将文本解析为 JSON 字典。"""
    text = text.strip()
    if not text.startswith("{"):
        return None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def _fallback_plain_text(text: str) -> NarrativeOutput:
    """降级为纯文本模式。"""
    return NarrativeOutput(
        script=text,
        has_required_script=bool(text.strip()),
        reply_mode="immediate",
        reply_content=text,
        raw={"_fallback": True, "_raw": text},
    )


def _normalize(
    parsed: dict[str, Any],
    *,
    max_script_characters: int,
) -> NarrativeOutput:
    """归一化 JSON 输出，应用所有防御规则。"""

    # ── script ──
    script = parsed.get("script", "")
    if not isinstance(script, str):
        script = str(script) if script else ""
    # 截断
    if len(script) > max_script_characters:
        script = script[:max_script_characters]

    has_required_script = bool(script.strip())

    # ── reply ──
    reply = parsed.get("reply", {})
    if not isinstance(reply, dict):
        reply = {}
    reply_mode = reply.get("mode", "immediate")
    if not isinstance(reply_mode, str) or reply_mode not in (
        "immediate",
        "delayed",
        "none",
    ):
        reply_mode = "immediate"
    reply_content = reply.get("content", "")
    if not isinstance(reply_content, str):
        reply_content = str(reply_content) if reply_content else ""

    # ── seen ──
    seen_raw = parsed.get("seen", True)
    seen = bool(seen_raw) if seen_raw is not None else True

    # seen=false → reply.mode=none
    if not seen:
        reply_mode = "none"

    # ── alter ──
    alter_raw = parsed.get("alter")
    alter = _clamp_alter(alter_raw)

    # ── memories ──
    memories = parsed.get("memories", [])
    if not isinstance(memories, list):
        memories = []
    memories = [_sanitize_memory(m) for m in memories if isinstance(m, dict)]

    # ── intents ──
    intents = parsed.get("intents", [])
    if not isinstance(intents, list):
        intents = []
    intents = [_sanitize_intent(i) for i in intents if isinstance(i, dict)]
    intents = intents[:8]  # 最多 8 条

    # ── actions ──
    actions = parsed.get("actions", [])
    if not isinstance(actions, list):
        actions = []
    actions = [a for a in actions if isinstance(a, dict)]

    # ── state_patch ──
    state_patch = parsed.get("statePatch")
    if state_patch is not None and not isinstance(state_patch, dict):
        state_patch = None

    return NarrativeOutput(
        script=script,
        has_required_script=has_required_script,
        reply_mode=reply_mode,
        reply_content=reply_content,
        seen=seen,
        alter=alter,
        memories=memories,
        intents=intents,
        actions=actions,
        state_patch=state_patch,
        raw=parsed,
    )


def _clamp_alter(value: Any) -> int | None:
    """将 alter 值取整并限制在 -5..5 范围。"""
    if value is None:
        return None
    try:
        iv = int(value)
    except (TypeError, ValueError):
        return None
    return max(-5, min(5, iv))


def _sanitize_memory(m: dict) -> dict:
    """清理单条 memory。"""
    return {
        "content": str(m.get("content", "")),
        "importance": float(m.get("importance", 0.5)),
        "participantId": str(m.get("participantId", "")),
        "kind": str(m.get("kind", "fact")),
    }


def _sanitize_intent(i: dict) -> dict:
    """清理单条 intent。"""
    return {
        "type": str(i.get("type", "")),
        "summary": str(i.get("summary", "")),
        "notBefore": str(i.get("notBefore", "")),
        "participantId": str(i.get("participantId", "")),
    }
