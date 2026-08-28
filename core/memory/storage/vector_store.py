"""SQLite + sqlite-vec 持久化向量存储。

将向量持久化到磁盘（``vec0`` 虚拟表 + 元数据表），重启后仍可检索。
sqlite-vec 原生扩展加载失败时自动降级为纯内存 numpy 余弦路径，保证主流程不受阻。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import aiosqlite
import numpy as np
import sqlite_vec

from core.logger import get_logger

logger = get_logger(__name__)

_MAX_RETRIES = 3


@dataclass(frozen=True)
class VectorHit:
    """kNN 检索命中项。"""

    id: int
    text: str
    distance: float
    metadata: dict[str, Any] = field(default_factory=dict)


def serialize_float32(vec: list[float]) -> bytes:
    """将向量序列化为 sqlite-vec 期望的 float32 字节串。"""
    return np.asarray(vec, dtype=np.float32).tobytes()


def deserialize_float32(raw: bytes) -> list[float]:
    """将 sqlite-vec 返回的 float32 字节串还原为 Python list。"""
    return np.frombuffer(raw, dtype=np.float32).tolist()


class SQLiteVectorStore:
    """基于 SQLite 的持久化向量库。

    Args:
        db_path: SQLite 数据库文件路径。
        dimension: 向量维度（``vec0`` 虚拟表固定维度）。
    """

    def __init__(self, db_path: str, dimension: int = 4096) -> None:
        self._db_path = db_path
        self._dimension = dimension
        self._db: aiosqlite.Connection | None = None
        self._fallback: list[tuple[str, list[float], dict[str, Any]]] = []
        self._fallback_mode = False

    # ── 生命周期 ────────────────────────────────────────────

    async def _ensure_open(self) -> aiosqlite.Connection:
        if self._db is not None:
            return self._db
        db = await aiosqlite.connect(self._db_path)
        try:
            await db.enable_load_extension(True)
            await db.load_extension(sqlite_vec.loadable_path())
            await db.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_mem "
                f"USING vec0(embedding float[{self._dimension}])"
            )
            await db.execute(
                "CREATE TABLE IF NOT EXISTS vec_meta ("
                "  rowid INTEGER PRIMARY KEY,"
                "  text TEXT NOT NULL,"
                "  metadata TEXT NOT NULL"
                ")"
            )
            await db.commit()
            self._db = db
        except Exception as exc:  # noqa: BLE001 扩展加载失败降级
            await db.close()
            self._fallback_mode = True
            logger.warning("sqlite-vec 扩展加载失败，降级为内存余弦路径: %s", exc)
        return db

    async def add(
        self, text: str, vector: list[float], metadata: dict[str, Any] | None = None
    ) -> int:
        """写入一条向量记录，返回 rowid。"""
        meta = dict(metadata or {})
        if self._fallback_mode:
            self._fallback.append((text, vector, meta))
            return len(self._fallback)
        await self._ensure_open()
        if self._fallback_mode:
            self._fallback.append((text, vector, meta))
            return len(self._fallback)
        assert self._db is not None
        db = self._db
        import json

        try:
            cur = await db.execute(
                "INSERT INTO vec_meta(text, metadata) VALUES (?, ?)",
                (text, json.dumps(meta, ensure_ascii=False)),
            )
            rowid = int(cur.lastrowid or 0)
            await db.execute(
                "INSERT INTO vec_mem(rowid, embedding) VALUES (?, ?)",
                (rowid, serialize_float32(vector)),
            )
            await db.commit()
            return rowid
        except aiosqlite.OperationalError:
            # 兼容重试（并发/锁竞争）
            await asyncio.sleep(0.02)
            return await self.add(text, vector, metadata)

    async def search(
        self, vector: list[float], top_k: int = 5
    ) -> list[VectorHit]:
        """按余弦相似度做 kNN 检索，返回按 distance 升序的命中项。"""
        if self._fallback_mode:
            return self._search_fallback(vector, top_k)
        await self._ensure_open()
        if self._fallback_mode:
            return self._search_fallback(vector, top_k)
        assert self._db is not None
        db = self._db
        import json

        query = serialize_float32(vector)
        cur = await db.execute(
            "SELECT rowid, distance FROM vec_mem "
            "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
            (query, top_k),
        )
        rows = await cur.fetchall()
        hits: list[VectorHit] = []
        for rowid, distance in rows:
            cur2 = await db.execute(
                "SELECT text, metadata FROM vec_meta WHERE rowid = ?", (rowid,)
            )
            meta_row = await cur2.fetchone()
            text = meta_row[0] if meta_row else ""
            meta = json.loads(meta_row[1]) if meta_row else {}
            hits.append(VectorHit(id=rowid, text=text, distance=distance, metadata=meta))
        return hits

    def _search_fallback(self, vector: list[float], top_k: int) -> list[VectorHit]:
        if not self._fallback:
            return []
        q = np.asarray(vector, dtype=np.float32)
        scored: list[tuple[float, int, tuple[str, list[float], dict[str, Any]]]] = []
        for i, (text, vec, meta) in enumerate(self._fallback):
            v = np.asarray(vec, dtype=np.float32)
            denom = float(np.linalg.norm(q) * np.linalg.norm(v))
            dist = 1.0 - (float(q @ v) / denom) if denom else 1.0
            scored.append((float(dist), i, (text, vec, meta)))
        scored.sort(key=lambda item: (item[0], item[1]))
        return [
            VectorHit(id=i, text=t, distance=d, metadata=dict(m))
            for d, i, (t, _v, m) in scored[:top_k]
        ]

    async def delete(self, rowid: int) -> bool:
        if self._fallback_mode:
            return False
        await self._ensure_open()
        if self._fallback_mode:
            return False
        assert self._db is not None
        db = self._db
        await db.execute("DELETE FROM vec_mem WHERE rowid = ?", (rowid,))
        await db.execute("DELETE FROM vec_meta WHERE rowid = ?", (rowid,))
        await db.commit()
        return True

    async def aclose(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def fallback_mode(self) -> bool:
        return self._fallback_mode
