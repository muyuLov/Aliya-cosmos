"""剧本持久化（ScriptStore）

基于 SQLite 存储故事/参与者/剧本条目，支持游标推进与条目追加。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from agent.story.canonical import CanonicalStory
from agent.story.entry import ScriptEntry
from agent.story.participant import Participant

_DDL = """
CREATE TABLE IF NOT EXISTS story (
    story_id TEXT PRIMARY KEY,
    setting TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT '{}',
    cursor_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS participant (
    story_id TEXT NOT NULL,
    person_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    profile TEXT NOT NULL DEFAULT '',
    relationship TEXT NOT NULL DEFAULT '',
    relationship_overlay TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT '{}',
    last_user_message_at TEXT,
    last_character_message_at TEXT,
    unread_message_count INTEGER NOT NULL DEFAULT 0,
    pending_reply_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (story_id, person_id)
);

CREATE TABLE IF NOT EXISTS script_entry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    actor TEXT NOT NULL,
    content TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
"""


class ScriptStore:
    """SQLite 剧本持久化。"""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """打开数据库并创建表。"""
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.executescript(_DDL)
        await self._db.commit()

    async def close(self) -> None:
        """关闭数据库连接。"""
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ── Story ──────────────────────────────────────────────

    async def create_story(
        self, story_id: str, *, setting: str = ""
    ) -> None:
        """创建新故事。"""
        assert self._db is not None
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT OR IGNORE INTO story
               (story_id, setting, cursor_at, created_at, updated_at)
               VALUES (?, ?, NULL, ?, ?)""",
            (story_id, setting, now, now),
        )
        await self._db.commit()

    async def get_story(self, story_id: str) -> Optional[CanonicalStory]:
        """按 ID 检索故事。"""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT story_id, setting, state, cursor_at, status, created_at, updated_at "
            "FROM story WHERE story_id = ?",
            (story_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        cursor_at = (
            datetime.fromisoformat(row[3]) if row[3] else None
        )
        return CanonicalStory(
            story_id=row[0],
            setting=row[1],
            state=json.loads(row[2]),
            cursor_at=cursor_at,
            status=row[4],
            created_at=datetime.fromisoformat(row[5]),
            updated_at=datetime.fromisoformat(row[6]),
        )

    # ── Entry ──────────────────────────────────────────────

    async def append_entry(self, entry: ScriptEntry) -> int:
        """追加剧本条目并推进游标，返回 entry row id。"""
        assert self._db is not None
        now_iso = entry.occurred_at.isoformat()
        cursor = await self._db.execute(
            """INSERT INTO script_entry
               (story_id, participant_id, kind, actor, content, occurred_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                entry.story_id,
                entry.participant_id,
                entry.kind,
                entry.actor,
                entry.content,
                now_iso,
            ),
        )
        row_id = int(cursor.lastrowid or 0)
        # 推进游标
        await self._db.execute(
            "UPDATE story SET cursor_at = ?, updated_at = ? WHERE story_id = ?",
            (now_iso, now_iso, entry.story_id),
        )
        await self._db.commit()
        return row_id

    async def get_recent_entries(
        self, story_id: str, *, limit: int = 20
    ) -> list[ScriptEntry]:
        """检索最近 N 条剧本条目（时间正序）。"""
        assert self._db is not None
        cursor = await self._db.execute(
            """SELECT story_id, participant_id, kind, actor, content, occurred_at
               FROM script_entry
               WHERE story_id = ?
               ORDER BY id DESC LIMIT ?""",
            (story_id, limit),
        )
        rows = await cursor.fetchall()
        entries = [
            ScriptEntry(
                story_id=r[0],
                participant_id=r[1],
                kind=r[2],
                actor=r[3],
                content=r[4],
                occurred_at=datetime.fromisoformat(r[5]),
            )
            for r in rows
        ]
        entries.reverse()  # 时间正序
        return entries

    # ── Participant ────────────────────────────────────────

    async def upsert_participant(self, p: Participant) -> None:
        """插入或更新参与者。"""
        assert self._db is not None
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT INTO participant
               (story_id, person_id, display_name, profile, relationship,
                relationship_overlay, state, last_user_message_at,
                last_character_message_at, unread_message_count,
                pending_reply_count, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (story_id, person_id) DO UPDATE SET
                display_name=excluded.display_name,
                profile=excluded.profile,
                relationship=excluded.relationship,
                relationship_overlay=excluded.relationship_overlay,
                state=excluded.state,
                last_user_message_at=excluded.last_user_message_at,
                last_character_message_at=excluded.last_character_message_at,
                unread_message_count=excluded.unread_message_count,
                pending_reply_count=excluded.pending_reply_count,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (
                p.story_id,
                p.person_id,
                p.display_name,
                p.profile,
                p.relationship,
                json.dumps(p.relationship_overlay),
                json.dumps(p.state),
                p.last_user_message_at.isoformat() if p.last_user_message_at else None,
                p.last_character_message_at.isoformat() if p.last_character_message_at else None,
                p.unread_message_count,
                p.pending_reply_count,
                p.status,
                now,
                now,
            ),
        )
        await self._db.commit()

    async def get_participants(self, story_id: str) -> list[Participant]:
        """检索故事中所有参与者。"""
        assert self._db is not None
        cursor = await self._db.execute(
            """SELECT story_id, person_id, display_name, profile, relationship,
                      relationship_overlay, state, last_user_message_at,
                      last_character_message_at, unread_message_count,
                      pending_reply_count, status, created_at, updated_at
               FROM participant WHERE story_id = ?""",
            (story_id,),
        )
        rows = await cursor.fetchall()
        return [
            Participant(
                story_id=r[0],
                person_id=r[1],
                display_name=r[2],
                profile=r[3],
                relationship=r[4],
                relationship_overlay=json.loads(r[5]),
                state=json.loads(r[6]),
                last_user_message_at=(
                    datetime.fromisoformat(r[7]) if r[7] else None
                ),
                last_character_message_at=(
                    datetime.fromisoformat(r[8]) if r[8] else None
                ),
                unread_message_count=r[9],
                pending_reply_count=r[10],
                status=r[11],
                created_at=datetime.fromisoformat(r[12]),
                updated_at=datetime.fromisoformat(r[13]),
            )
            for r in rows
        ]
