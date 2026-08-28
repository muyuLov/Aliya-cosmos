"""CanonLayer（L0）：角色核心设定层。

只读包装 ``data/prompts/`` 下的身份/灵魂/语气设定文档，作为主叙事上下文的
人设起点。仅手动刷新，不可写、不可遗忘、不可衰减。
"""
from __future__ import annotations

from pathlib import Path

from core.memory.layers import MemoryEntry, MemoryLayer

# Canon 设定文件清单（按拼装顺序）
_CANON_FILES = ("identity.md", "soul.md", "tone-rules.md")


class CanonLayer(MemoryLayer):
    """角色核心设定层（只读）。"""

    name = "canon"

    def __init__(self, prompts_dir: str | Path | None = None) -> None:
        base = Path(prompts_dir) if prompts_dir else Path(__file__).resolve().parents[3] / "data" / "prompts"
        self._files: list[Path] = [base / f for f in _CANON_FILES]
        self._cache: str | None = None

    def _load(self) -> str:
        parts: list[str] = []
        for fp in self._files:
            if fp.exists():
                parts.append(fp.read_text(encoding="utf-8").strip())
        return "\n\n".join(parts)

    async def write(self, entry: MemoryEntry) -> None:
        # Canon 只读，写入为空操作
        return None

    async def query(self, text: str, limit: int = 5) -> list[MemoryEntry]:
        # 返回完整设定文本；text 参数仅用于接口一致性
        if self._cache is None:
            self._cache = self._load()
        return [
            MemoryEntry(
                id="canon:all",
                content=self._cache,
                source="canon",
                confidence=1.0,
                importance=1.0,
            )
        ]

    async def forget(self, entry_id: str) -> None:
        # Canon 只读，不可遗忘
        return None

    async def decay(self, factor: float = 0.95) -> None:
        # Canon 只读，不可衰减
        return None
