"""测试多会话历史 Part C：session_store（元数据 + 持久化 + 排序）"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from agent.session_store import SessionMeta, SessionStore


@pytest.fixture
def tmp_store(tmp_path: Path) -> SessionStore:
    """每个测试用独立的临时目录。"""
    return SessionStore(store_dir=tmp_path)


# ── SessionMeta ──────────────────────────────────────────────


class TestSessionMeta:
    def test_defaults(self):
        m = SessionMeta()
        assert m.id  # 自动 UUID
        assert m.title == ""
        assert m.created_at > 0
        assert m.updated_at > 0
        assert m.message_count == 0
        assert m.pinned is False

    def test_title_or_default(self):
        m = SessionMeta(id="abc123")
        assert m.title_or_default == "会话 abc123"
        m.title = "我的聊天"
        assert m.title_or_default == "我的聊天"

    def test_touch(self):
        m = SessionMeta()
        old_updated = m.updated_at
        time.sleep(0.01)
        m.touch(delta_count=3)
        assert m.updated_at > old_updated
        assert m.message_count == 3


# ── SessionStore CRUD ────────────────────────────────────────


class TestSessionStoreCRUD:
    def test_create(self, tmp_store: SessionStore):
        meta = tmp_store.create(title="测试会话")
        assert meta.title == "测试会话"
        assert tmp_store.count() == 1

    def test_get(self, tmp_store: SessionStore):
        meta = tmp_store.create(title="test")
        found = tmp_store.get(meta.id)
        assert found is not None
        assert found.title == "test"

    def test_get_nonexistent(self, tmp_store: SessionStore):
        assert tmp_store.get("no-such-id") is None

    def test_update(self, tmp_store: SessionStore):
        meta = tmp_store.create(title="old")
        updated = tmp_store.update(meta.id, title="new", pinned=True)
        assert updated is not None
        assert updated.title == "new"
        assert updated.pinned is True

    def test_update_readonly_fields(self, tmp_store: SessionStore):
        meta = tmp_store.create()
        original_id = meta.id
        original_created = meta.created_at
        tmp_store.update(meta.id, id="hacked", created_at=0)
        found = tmp_store.get(original_id)
        assert found is not None
        assert found.id == original_id
        assert found.created_at == original_created

    def test_delete(self, tmp_store: SessionStore):
        meta = tmp_store.create()
        assert tmp_store.delete(meta.id) is True
        assert tmp_store.get(meta.id) is None
        assert tmp_store.count() == 0

    def test_delete_nonexistent(self, tmp_store: SessionStore):
        assert tmp_store.delete("no-such-id") is False

    def test_touch_store(self, tmp_store: SessionStore):
        meta = tmp_store.create()
        tmp_store.touch(meta.id, delta_count=5)
        found = tmp_store.get(meta.id)
        assert found is not None
        assert found.message_count == 5


# ── 排序 ────────────────────────────────────────────────────


class TestSessionStoreSort:
    def test_list_sorted_by_updated(self, tmp_store: SessionStore):
        m1 = tmp_store.create(title="first")
        time.sleep(0.01)
        m2 = tmp_store.create(title="second")
        items = tmp_store.list_all()
        assert items[0].id == m2.id  # 最新的排前面
        assert items[1].id == m1.id

    def test_list_ascending(self, tmp_store: SessionStore):
        m1 = tmp_store.create(title="a")
        _m2 = tmp_store.create(title="b")
        items = tmp_store.list_all(descending=False)
        assert items[0].id == m1.id


# ── 持久化 ──────────────────────────────────────────────────


class TestSessionStorePersistence:
    def test_persists_across_instances(self, tmp_path: Path):
        s1 = SessionStore(store_dir=tmp_path)
        meta = s1.create(title="持久化测试")
        # 新实例
        s2 = SessionStore(store_dir=tmp_path)
        assert s2.count() == 1
        found = s2.get(meta.id)
        assert found is not None
        assert found.title == "持久化测试"

    def test_corrupted_file_graceful(self, tmp_path: Path):
        index = tmp_path / "metadata.json"
        index.write_text("not json!!!", encoding="utf-8")
        s = SessionStore(store_dir=tmp_path)
        assert s.count() == 0

    def test_json_structure(self, tmp_path: Path):
        s = SessionStore(store_dir=tmp_path)
        s.create(title="结构测试")
        data = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["title"] == "结构测试"
