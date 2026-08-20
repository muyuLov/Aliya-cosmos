"""会话元数据模型与 JSON 持久化

存储在 data/sessions/ 目录下：
  - metadata.json  — 所有会话元数据索引
  - {id}/           — 各会话的 LLM 历史文件（由 core/memory 管理）
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 默认存储路径
_DEFAULT_STORE_DIR = Path("data/sessions")


@dataclass
class SessionMeta:
    """单个会话的元数据。"""

    id: str = ""
    title: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    message_count: int = 0
    pinned: bool = False

    def __post_init__(self) -> None:
        if not self.id:
            self.id = str(uuid.uuid4())
        now = time.time()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    @property
    def title_or_default(self) -> str:
        """返回标题，空则返回截短的 ID。"""
        return self.title or f"会话 {self.id[:8]}"

    def touch(self, delta_count: int = 0) -> None:
        """刷新 updated_at + 增加 message_count。"""
        self.updated_at = time.time()
        self.message_count += delta_count


class SessionStore:
    """会话元数据持久化管理器。

    JSON 文件存储，支持增删改查 + 排序。
    """

    def __init__(self, store_dir: Path | str | None = None) -> None:
        self._dir = Path(store_dir) if store_dir else _DEFAULT_STORE_DIR
        self._index_path = self._dir / "metadata.json"
        self._sessions: dict[str, SessionMeta] = {}
        self._load()

    # ── 读写底层 ──────────────────────────────────────────

    def _load(self) -> None:
        """从磁盘加载元数据索引。"""
        if not self._index_path.exists():
            self._sessions = {}
            return

        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("会话索引加载失败: %s", exc)
            self._sessions = {}
            return

        self._sessions = {}
        for raw in data.get("sessions", []):
            meta = SessionMeta(**raw)
            self._sessions[meta.id] = meta

    def _save(self) -> None:
        """将元数据索引写入磁盘。"""
        self._dir.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "sessions": [asdict(m) for m in self._sessions.values()],
        }
        try:
            self._index_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error("会话索引保存失败: %s", exc)

    # ── CRUD ──────────────────────────────────────────────

    def create(self, title: str = "", **kwargs: Any) -> SessionMeta:
        """创建新会话元数据并持久化。"""
        meta = SessionMeta(title=title, **kwargs)
        self._sessions[meta.id] = meta
        self._save()
        logger.debug("创建会话 | id=%s | title=%s", meta.id, meta.title)
        return meta

    def get(self, session_id: str) -> SessionMeta | None:
        """按 ID 获取会话元数据。"""
        return self._sessions.get(session_id)

    def update(self, session_id: str, **kwargs: Any) -> SessionMeta | None:
        """更新会话元数据字段。"""
        meta = self._sessions.get(session_id)
        if meta is None:
            return None

        for key, val in kwargs.items():
            if hasattr(meta, key) and key not in ("id", "created_at"):
                setattr(meta, key, val)

        meta.updated_at = time.time()
        self._save()
        return meta

    def delete(self, session_id: str) -> bool:
        """删除会话元数据。"""
        if session_id not in self._sessions:
            return False
        del self._sessions[session_id]
        self._save()
        logger.debug("删除会话 | id=%s", session_id)
        return True

    def touch(self, session_id: str, delta_count: int = 1) -> None:
        """刷新会话更新时间 + 消息计数。"""
        meta = self._sessions.get(session_id)
        if meta is not None:
            meta.touch(delta_count)
            self._save()

    # ── 查询 ──────────────────────────────────────────────

    def list_all(self, sort_by: str = "updated_at", descending: bool = True) -> list[SessionMeta]:
        """列出所有会话，支持排序。"""
        items = list(self._sessions.values())
        items.sort(key=lambda m: getattr(m, sort_by, 0), reverse=descending)
        return items

    def count(self) -> int:
        return len(self._sessions)
