# Aliya-Cosmos HDS-Interlude 重构实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 Aliya-Cosmos 从「两阶段 FC 对话循环」重构为 HDS-Interlude 式「幕间连续生活」系统：主叙事单次写作 + 四层分层记忆 + Alter 动态情绪 + Agency 主体约束 + 场景/弧线 + 分层日志。

**Architecture:** 5 阶段渐进重构，每阶段独立可测、可回滚。阶段 0 移除 TTS → 阶段 1 基础设施（AsyncSingleton/配置缓存/会话串行队列）→ 阶段 2 分层记忆（Canon/Overlay/Continuity/Facts 全新实现，底层 SQLite+sqlite-vec 向量持久化，旧 GRAG+层次化记忆整体移除）→ 阶段 3 主叙事单次写作循环（agent 整体重写）→ 阶段 4 Alter/Agency/休息窗口 → 阶段 5 场景管理 + 日志系统整体重写。**旧系统直接删除，不保留接口兼容。**

**Tech Stack:** Python 3.12+, asyncio, PyYAML, pytest (asyncio_mode=auto), aiosqlite + sqlite-vec（分层记忆向量持久化）

**Spec:** `docs/plans/2026-08-28-hdsi-refactor-design.md`

## Global Constraints

- Python >=3.12, <3.14
- 异步优先：核心路径不得阻塞事件循环
- 降级原则：可选依赖初始化失败不阻塞主流程
- 代码注释与文档均为中文，标识符保留英文
- Black (100 列) + isort (black profile)
- pytest `asyncio_mode = "auto"`，默认 coverage，可用 `--no-cov`
- TDD：先写失败测试 → 运行确认失败 → 最小实现 → 运行确认通过 → 提交

---

# 阶段 0：移除 TTS 系统

## Task 0.1: 移除 TTS 模块与依赖

**Files:**
- Delete: `core/tts/`（整个目录：`__init__.py`/`service.py`/`factory.py`/`models.py`/`constants.py`/`exceptions.py`/`validation.py`/`text_splitter.py`/`example.py`/`README.md`/`CONFIGURATION.md`/`providers/`/`player/`）
- Modify: `pyproject.toml`（移除 `tts`/`voice-synthesis` 分类关键词、`numpy`/`edge-tts`/FFmpeg 相关依赖）
- Modify: `data/config/main.yml`（移除 `cosmos.service.tts` 整段）
- Delete: `data/config/TTSProviders.json`
- Modify: `agent/events.py`（移除 `TTS_FEATURES` 常量与 `to_protocol` 中相关映射）
- **保留** `docker/compose/compose.yml` 的 `astratts` 服务（用户明确保留 docker 配置）
- **保留** `docker/scripts/start/start_docker_services.sh` 与 `.ps1` 的 AstraTTS 段（用户明确保留 docker 配置）
- Modify: `README.md` / `CLAUDE.md`（移除 TTS 相关描述与命令）
- Run: `uv lock` 更新锁文件

**Step 1: 确认移除面**

先全局搜索确认 `core.tts` 无 core/tts 之外的被引用方（GUI 前端除外）：
Run: `rg -l "core\.tts|core\.tts\.|from core import tts" --glob '!core/tts/**' --glob '!example/**'`
Expected: 无输出（除 `agent/events.py` 中 `TTS_FEATURES` 常量引用——此为字符串常量非 import）

**Step 2: 删除模块与配置**
```bash
rm -rf core/tts
rm -f data/config/TTSProviders.json
```

**Step 3: 清理依赖与文档**
编辑 `pyproject.toml` 移除 TTS 依赖与分类；编辑 `main.yml` 移除 `cosmos.service.tts`；编辑 `agent/events.py` 移除 `TTS_FEATURES`；**不动** docker compose 与 start 脚本（用户明确保留 docker 配置）；更新 README/CLAUDE.md 移除 TTS 描述。
```bash
uv lock
```

**Step 4: 验证**
Run: `uv run python -c "import agent; print('ok')"`
Run: `uv run pytest --no-cov`
Expected: 无 `ModuleNotFoundError: core.tts`，测试全绿；docker compose/start 脚本未被改动（`git status` 仅显示 core/tts、配置、文档变更）

**Step 5: 提交**
```bash
git add -A
git commit -m "refactor(tts): 移除 TTS 语音合成系统（模块/配置/依赖，保留 docker 配置）"
```

---

# 阶段 1：基础设施层

## Task 1.1: 创建 AsyncSingleton

**Files:**
- Create: `core/infra/__init__.py`
- Create: `core/infra/singleton.py`
- Test: `tests/core/infra/test_singleton.py`

**Step 1: 写失败测试**

`tests/core/infra/__init__.py`（空）

`tests/core/infra/test_singleton.py`:
```python
import pytest
from core.infra.singleton import AsyncSingleton


@pytest.fixture(autouse=True)
def _cleanup():
    AsyncSingleton.clear()
    yield
    AsyncSingleton.clear()


async def test_get_or_create_returns_same_instance():
    calls = []
    async def factory():
        calls.append(1)
        return "instance"
    a = await AsyncSingleton.get_or_create("k", factory)
    b = await AsyncSingleton.get_or_create("k", factory)
    assert a is b
    assert len(calls) == 1


async def test_get_sync_returns_none_for_missing():
    assert AsyncSingleton.get_sync("nope") is None


async def test_clear_specific_key():
    await AsyncSingleton.get_or_create("k", lambda: _a("v"))
    AsyncSingleton.clear("k")
    assert AsyncSingleton.get_sync("k") is None


async def _a(v):
    return v
```

**Step 2: 运行确认失败**
Run: `uv run pytest tests/core/infra/test_singleton.py -v --no-cov`
Expected: FAIL (ModuleNotFoundError: core.infra.singleton)

**Step 3: 最小实现**

`core/infra/__init__.py`:
```python
"""基础设施工具模块。"""
```

`core/infra/singleton.py`:
```python
"""异步安全惰性单例注册表。"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class AsyncSingleton:
    """按 key 独立加锁的异步惰性单例注册表。"""

    _instances: dict[str, object] = {}
    _locks: dict[str, asyncio.Lock] = {}

    @classmethod
    def _normalize(cls, key: str) -> str:
        # 路径归一化，避免路径变体创建多个实例
        p = Path(key)
        try:
            return str(p.resolve())
        except OSError:
            return str(p)

    @classmethod
    async def get_or_create(cls, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        key = cls._normalize(key)
        if key in cls._instances:
            return cls._instances[key]  # type: ignore[return-value]
        lock = cls._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key not in cls._instances:
                cls._instances[key] = await factory()
        return cls._instances[key]  # type: ignore[return-value]

    @classmethod
    def get_sync(cls, key: str) -> object | None:
        return cls._instances.get(cls._normalize(key))

    @classmethod
    def clear(cls, key: str | None = None) -> None:
        if key is None:
            cls._instances.clear()
            cls._locks.clear()
            return
        k = cls._normalize(key)
        cls._instances.pop(k, None)
        cls._locks.pop(k, None)
```

**Step 4: 运行确认通过**
Run: `uv run pytest tests/core/infra/test_singleton.py -v --no-cov`
Expected: PASS

**Step 5: 提交**
```bash
git add core/infra tests/core/infra
git commit -m "feat(infra): 新增 AsyncSingleton 异步安全单例注册表"
```

---

## Task 1.2: 会话级串行队列

**Files:**
- Create: `agent/queue.py`
- Test: `tests/agent/test_queue.py`

**Step 1: 写失败测试**

```python
import asyncio
import pytest
from agent.queue import SessionQueue


async def test_enqueue_serializes_messages():
    queue = SessionQueue()
    order = []

    async def loop_fn(text, images=None):
        order.append(text)
        await asyncio.sleep(0.01)
        return text

    await queue.start(loop_fn)
    await queue.enqueue("first")
    await queue.enqueue("second")
    await asyncio.sleep(0.1)
    await queue.stop()
    assert order == ["first", "second"]
```

**Step 2: 运行确认失败**
Run: `uv run pytest tests/agent/test_queue.py -v --no-cov`
Expected: FAIL

**Step 3: 最小实现**

`agent/queue.py`:
```python
"""会话级串行队列：保证同一会话的处理严格串行。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any


@dataclass
class QueuedMessage:
    text: str
    images: list[str] | None = None
    result: Any = None


class SessionQueue:
    """每会话串行队列。"""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[QueuedMessage] = asyncio.Queue()
        self._worker: asyncio.Task | None = None

    async def enqueue(self, text: str, images: list[str] | None = None) -> Any:
        msg = QueuedMessage(text=text, images=images)
        await self._queue.put(msg)
        await self._queue.join()
        return msg.result

    async def start(
        self, loop_fn: Callable[[str, list[str] | None], Awaitable[Any]]
    ) -> None:
        self._worker = asyncio.create_task(self._process_loop(loop_fn))

    async def _process_loop(self, loop_fn) -> None:
        while True:
            msg = await self._queue.get()
            try:
                msg.result = await loop_fn(msg.text, msg.images)
            finally:
                self._queue.task_done()

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None
```

**Step 4: 运行确认通过**
Run: `uv run pytest tests/agent/test_queue.py -v --no-cov`
Expected: PASS

**Step 5: 提交**
```bash
git add agent/queue.py tests/agent/test_queue.py
git commit -m "feat(agent): 新增会话级串行队列 SessionQueue"
```

---

## Task 1.3: 配置环境变量解析 LRU 缓存

**Files:**
- Modify: `core/config/manager.py`
- Test: `tests/core/config/test_manager_cache.py`

**Step 1: 写失败测试**（先阅读 `core/config/manager.py` 了解 `_resolve_env_vars` 现状）
```python
import pytest
from core.config.manager import ConfigManager


async def test_env_resolution_cached_then_invalidated(monkeypatch):
    mgr = ConfigManager("data/config/main.yml")
    monkeypatch.setenv("CACHE_TEST", "v1")
    mgr.reload()
    assert mgr.get("cosmos.cache_test_key") == "v1"
    monkeypatch.setenv("CACHE_TEST", "v2")
    # 未 reload 前应返回缓存旧值
    assert mgr.get("cosmos.cache_test_key") == "v1"
    mgr.reload()
    # reload 后失效，返回新值
    assert mgr.get("cosmos.cache_test_key") == "v2"
```

**Step 2: 运行确认失败**
Run: `uv run pytest tests/core/config/test_manager_cache.py -v --no-cov`
Expected: FAIL（需阅读 manager.py 确认测试可落地，若接口不符则调整测试）

**Step 3: 实现**
在 `core/config/manager.py` 的 `_resolve_env_vars` 外层加 LRU 缓存（`functools.lru_cache`），`reload()` 时调用 `_env_cache.cache_clear()`。

**Step 4/5: 运行确认通过并提交**
```bash
git add core/config/manager.py tests/core/config/test_manager_cache.py
git commit -m "perf(config): 环境变量解析加 LRU 缓存，reload 时失效"
```

**阶段 1 完成检查点**：`uv run pytest tests/core/infra tests/agent/test_queue.py tests/core/config -v --no-cov` 全绿。

---

## Task 1.4: 时间处理模块（core/time.py）

**Files:**
- Create: `core/time.py`
- Test: `tests/core/test_time.py`

按 HDSI 时间机制实现（设计 3.9）：
- `local_clock_minutes(value, timezone)`：用 `datetime` 转本地时区提取 hour/minute 换算分钟。
- `resolve_timezone(candidate)`：try 试探本地时区，失败回退 `UTC`，缓存结果；默认 `Asia/Shanghai`。
- `story_local_time_context(value, timezone)`：返回 hour/period(morning 5-12 / afternoon 12-18 / evening 18-22 / night)/daylight_expectation/weekday/offset。
- `active_rest_window(rest_windows, timezone, now)`：跨午夜半开区间判断。
- `calendar_day_key(value, timezone)`：返回 `YYYY-MM-DD`（Overlay 证据天数/分组键）。

写失败测试（时段分界、跨午夜休息窗口、时区回退、day_key）、实现、验证、提交 `feat(core): 时间处理模块（双时钟/时段/休息窗口）`。

---

# 阶段 2：分层记忆

## Task 2.1: 记忆层协议（MemoryEntry + MemoryLayer）

**Files:**
- Create: `core/memory/layers/__init__.py`
- Test: `tests/memory/test_layers_protocol.py`

**Step 1: 写失败测试**
```python
from core.memory.layers import MemoryEntry, MemoryLayer


def test_memory_entry_defaults():
    e = MemoryEntry(id="1", content="内容", source="conv", confidence=0.5, importance=0.5)
    assert e.metadata == {}


def test_layer_is_abstract():
    try:
        MemoryLayer()
    except TypeError:
        return
    raise AssertionError("MemoryLayer 应为抽象类")
```

**Step 2: 运行确认失败** → **Step 3: 实现**

`core/memory/layers/__init__.py`:
```python
"""分层记忆协议：统一 MemoryEntry 结构与 MemoryLayer 层接口。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MemoryEntry:
    id: str
    content: str
    source: str
    confidence: float
    importance: float
    metadata: dict[str, Any] = field(default_factory=dict)


class MemoryLayer(ABC):
    name: str = "layer"

    @abstractmethod
    async def write(self, entry: MemoryEntry) -> None: ...

    @abstractmethod
    async def query(self, text: str, limit: int = 5) -> list[MemoryEntry]: ...

    @abstractmethod
    async def forget(self, entry_id: str) -> None: ...

    @abstractmethod
    async def decay(self, factor: float = 0.95) -> None: ...
```

**Step 4/5: 通过并提交**
```bash
git add core/memory/layers tests/memory/test_layers_protocol.py
git commit -m "feat(memory): 定义分层记忆协议 MemoryEntry + MemoryLayer"
```

---

## Task 2.2: SQLite + sqlite-vec 向量持久化存储

**Files:**
- Modify: `pyproject.toml`（新增 `aiosqlite`、`sqlite-vec` 依赖）
- Create: `core/memory/storage/__init__.py`
- Create: `core/memory/storage/vector_store.py`（SQLite+sqlite-vec 持久化向量库）
- Test: `tests/memory/test_vector_store_sqlite.py`

**Step 1: 写失败测试**
```python
import aiosqlite
import pytest
from core.memory.storage.vector_store import SQLiteVectorStore


async def test_add_and_knn_search(tmp_path):
    db_path = tmp_path / "mem.db"
    store = SQLiteVectorStore(str(db_path), dimension=4)
    await store.add("第一条", [0.1, 0.2, 0.3, 0.4], metadata={"layer": "fact"})
    await store.add("第二条", [0.9, 0.8, 0.7, 0.6], metadata={"layer": "fact"})
    hits = await store.search([0.1, 0.2, 0.3, 0.5], top_k=2)
    assert hits[0].text == "第一条"
    assert hits[0].metadata["layer"] == "fact"
    await store.aclose()


async def test_persistence_across_reopen(tmp_path):
    db_path = tmp_path / "mem.db"
    store = SQLiteVectorStore(str(db_path), dimension=4)
    await store.add("持久化文本", [0.5, 0.5, 0.5, 0.5], metadata={})
    await store.aclose()
    store2 = SQLiteVectorStore(str(db_path), dimension=4)
    hits = await store2.search([0.5, 0.5, 0.5, 0.5], top_k=5)
    assert any(h.text == "持久化文本" for h in hits)
    await store2.aclose()
```

**Step 2: 运行确认失败**
Run: `uv run pytest tests/memory/test_vector_store_sqlite.py -v --no-cov`
Expected: FAIL（module not found）

**Step 3: 最小实现**
- 先 `uv add aiosqlite sqlite-vec` 安装依赖。
- `SQLiteVectorStore`：`aiosqlite.connect` 后用底层 sqlite3 连接 `enable_load_extension(True)` + `sqlite_vec.load(raw)`；创建 `vec0` 虚拟表（`CREATE VIRTUAL TABLE IF NOT EXISTS ... USING vec0(embedding float[N])`）；普通元数据表存 `id/text/source/confidence/importance/metadata`，`rowid` 与 vec0 对齐；插入用 `serialize_float32(vec)`；kNN 用 `WHERE embedding MATCH ? ORDER BY distance LIMIT k` 后 join 元数据回填文本。
- 降级：sqlite-vec 加载失败时回退纯内存 numpy 余弦（复用 `core/vector/store.py` 的 `VectorStore`）。
- 注意：`dimension` 固定为配置值（如 4096）；embedding 归一化以保证 distance 语义。

**Step 4: 运行确认通过**
Run: `uv run pytest tests/memory/test_vector_store_sqlite.py -v --no-cov`
Expected: PASS（若环境不支持 sqlite-vec 扩展加载，测试应断言降级路径可用）

**Step 5: 提交**
```bash
git add pyproject.toml core/memory/storage tests/memory/test_vector_store_sqlite.py
git commit -m "feat(memory): SQLite+sqlite-vec 持久化向量存储"
```

---

## Task 2.3: CanonLayer（L0）

**Files:**
- Create: `core/memory/layers/canon.py`
- Test: `tests/memory/test_canon_layer.py`

CanonLayer 只读包装 `data/prompts/identity.md`+`soul.md`+`tone-rules.md`，提供 `query()` 返回当前设定文本。写失败测试（读取 prompts 目录断言含设定文本）、实现、验证、提交（`feat(memory): 实现 CanonLayer L0 角色核心设定层`）。

---

## Task 2.4: OverlayLayer（L1）+ StatePatch 证据链

**Files:**
- Create: `core/memory/layers/overlay.py`
- Test: `tests/memory/test_overlay_layer.py`

**Step 1: 写失败测试**（覆盖证据链应用门槛）
```python
from core.memory.layers.overlay import OverlayLayer, StatePatch


async def test_patch_applied_when_evidence_sufficient():
    layer = OverlayLayer(confidence_threshold=0.82, min_turns=3, min_days=2, cooldown_hours=72)
    # 3 个不同回合、跨 2 天、置信度 0.9
    patch = StatePatch(
        id="p1", target="character", proposed_value="新性格",
        evidence="证据", confidence=0.9, impact="minor",
        source_entry_ids=["e1"], status="proposed",
        created_at="2026-08-26T00:00:00",
        applied_at=None,
    )
    for eid, day in [("e1","2026-08-26"),("e2","2026-08-27"),("e3","2026-08-28")]:
        layer.record_evidence("p1", source_entry_id=eid, day=day)
    applied = await layer.try_apply(patch)
    assert applied is True


async def test_patch_rejected_when_insufficient():
    layer = OverlayLayer(confidence_threshold=0.82, min_turns=3, min_days=2, cooldown_hours=72)
    patch = StatePatch(id="p1", target="character", proposed_value="x", evidence="e",
                       confidence=0.5, impact="major", source_entry_ids=["e1"],
                       status="proposed", created_at="2026-08-28T00:00:00", applied_at=None)
    applied = await layer.try_apply(patch)
    assert applied is False
```

**Step 2-5: 实现并提交**（`StatePatch` dataclass + 证据去重 + 门槛校验 + 冷却），`feat(memory): 实现 OverlayLayer L1 证据链演化层`。

---

## Task 2.5: ContinuityLayer（L2）

**Files:**
- Create: `core/memory/layers/continuity.py`
- Test: `tests/memory/test_continuity_layer.py`

实现场景摘要 + 连续性快照的读写，`write()`/`query()` 存近期摘要，`forget()`/`decay()`。提交 `feat(memory): 实现 ContinuityLayer L2 场景摘要连续性层`。

---

## Task 2.6: FactLayer（L3）全新实现

**Files:**
- Create: `core/memory/layers/fact_layer.py`
- Test: `tests/memory/test_fact_layer.py`

全新实现长期事实/承诺/事件层（向量 + 本地存储），**不复用旧 GRAG 代码**。加权排序（重要性 0.5 / 置信度 0.35 / 时效 0.15）。提交 `feat(memory): 全新实现 FactLayer L3 长期事实层`。

---

## Task 2.7: 统一门面 + 旧记忆系统整体移除

重写 `core/memory/memory_manager.py` 为分层门面（持有全新四层）。删除旧记忆系统：`core/memory/` 下 `graph.py`/`extractor.py`/`rag_query.py`/`task_manager.py`/`hierarchical.py`/`config.py`/`_providers.py`/`_retry.py`/`_utils.py`/`exceptions.py` 等旧实现，`get_memory_manager()` 返回全新分层门面。运行 `uv run pytest tests/memory -v --no-cov` 全绿后提交 `refactor(memory): 四层分层统一入口，移除旧记忆系统`。

**阶段 2 完成检查点**：分层记忆全绿，旧记忆系统（GRAG+层次化）已移除。

---

# 阶段 3：主叙事单次写作循环（agent 模块重新设计）

## Task 3.0: 主剧本与参与者（story 包）+ 剧本持久化

**Files:**
- Create: `agent/story/__init__.py`
- Create: `agent/story/canonical.py`（Canonical Story 主剧本状态）
- Create: `agent/story/participant.py`（参与者资料/初始关系/演化状态）
- Create: `agent/story/entry.py`（ScriptEntry 剧本条目）
- Create: `agent/story/script_store.py`（剧本持久化，SQLite）
- Create: `agent/story/write_queue.py`（全局写队列，串行 + 退避重试）
- Test: `tests/agent/test_story.py`, `tests/agent/test_write_queue.py`

按 HDSI 主剧本模式设计：一个 Canonical Story + 独立参与者状态。剧本条目（用户事件/AI 回复/受限行动/系统事件）持久化到 SQLite，支持补写剧本与场景进度追踪。SQLite 表（设计 3.8）：`story(setting/state/cursorAt/status)`、`participant(profile/relationship/state/status)`、`script_entry(kind/actor/content/occurredAt)`、`intent`、`scene`、`arc`。写队列串行化 create/set/delete，transient 错退避重试最多 7 次（100..5000ms + jitter）。提交 `feat(agent): 新增主剧本与参与者模块 story 包`。

---

## Task 3.0b: 故事级串行队列 + 多参与者边界

**Files:**
- Create: `agent/story/serial.py`（故事级串行 `serial<T>(id, task)`）
- Modify: `agent/story/participant.py`（participant payload 字段）
- Test: `tests/agent/test_serial.py`, `tests/agent/test_participant_boundary.py`

按设计 3.10：
- `serial(story_id, task)`：取旧 promise `.catch(()=>undefined).then(task)`（失败不堵队列）+ 队头身份校验。
- participant payload：基线 `id` + `displayName/profile/relationship/relationshipOverlay/lastUserMessageAt/lastCharacterMessageAt/unreadMessageCount/pendingReplyCount`。
- 多参与者隔离：`shareParticipantDetails`（默认 false）控制别参与者原文是否传给模型；关闭时 compaction 把别参与者 content 替换为占位符。
- 提交 `feat(agent): 故事级串行队列与多参与者私聊边界`。

---

## Task 3.1: 结构化输出解析器（metadata_parser）

**Files:**
- Create: `agent/metadata_parser.py`
- Test: `tests/agent/test_metadata_parser.py`

解析完整回复，**分离 prose(script) 与 transport(interaction/groupReply/crossConversationActions)**，防御性归一化（normalize，设计 3.2）。关键防御：script 截断 `maxScriptCharacters`；alter 取整限幅 -5..5；seen 强制 boolean；`seen=false` 强制 reply.mode=none；`mode=delayed` 且 sendAt 越界降级 mode=none；memories/intents/crossConversationActions 的 participantId 白名单过滤；intents 最多 8 条过滤 follow-up-commitment。失败降级纯文本。提交 `feat(agent): 新增结构化元数据解析器 metadata_parser`。

## Task 3.2: 事件模型与协议整体重构（events）

**Files:**
- Rewrite: `agent/events.py`
- Test: `tests/agent/test_events.py`

**接口层重构**：`AgentEvent` + `ProtocolEvent` 双层协议整体重新设计，不复用旧协议。
- 新事件类型承载主叙事副产物：`TurnMetadata`（emotion_delta/memory_candidates/state_patches/follow_up_intents）、`AlterTriggered`、`ProactiveContact`、`SceneClosed`、`AgencyDecision`。
- 移除旧两阶段残留事件：`StepStarted`/`StepFinished`/`ToolCallStart`/`ToolCallResult`/`ToolCallEnd`（工具退化为受限行动，不再作为独立阶段事件）。
- `to_protocol` 映射全面重写；`EventSink` 接口重新设计适配新事件流。
- 提交 `refactor(agent): 事件模型与协议整体重构`。

## Task 3.3: 主叙事器（narrator）

**Files:**
- Create: `agent/narrator.py`
- Test: `tests/agent/test_narrator.py`

封装主叙事 LLM 调用（OpenAI-compatible），**整个上下文作为单条 JSON 消息**（system 固定合约 + user 纯 JSON 结构化上下文），`response_format: {type:'json_object'}`，一次调用产出 script + 行为决策 + 结构化副产物。**prose(script) 与 transport 分离**，`<sep/>` 只在 transport 拆气泡（首段立即投递 + split-message intent 模拟打字）。超时/重试/降级纯文本；**空 script 语义失败 → narrative-retry 持久化重试，绝不推进 cursorAt**；可见回复缺失时 `outputRecovery:true` 重写一次。提交 `feat(agent): 实现主叙事单次写作器 narrator`。

---

## Task 3.3b: 消息合并与过期请求取消（入口层）

**Files:**
- Create: `agent/story/merger.py`
- Test: `tests/agent/test_merger.py`

按设计 3.11：同一关系分支连续消息 `mergeWindowMs`（默认 2 秒）内合并；`should_supersede_narrative_request`（首条回复提交前新输入接管本轮并重写；首条提交后截断未发送 `<sep/>` 后续气泡并作为未完成意图）；过期模型结果不落库。提交 `feat(agent): 消息短时合并与过期请求取消`。

## Task 3.4: 重写主循环（loop）

**Files:**
- Rewrite: `agent/loop.py`
- Test: `tests/agent/test_loop.py`

**四阶段状态机**：补写剧本 → 处理当前事件与行为决策 → 按模式投递（immediate/delayed/silent）→ 副作用（写剧本/场景/分层记忆/StatePatch/Alter 累计）。会话串行队列取事件 → 构建上下文 → 主叙事一次调用 → 解析 → 保存 → 投递 → 受限行动（先权限检查再 Agency 门控）。删除 `_tool_phase`/`_soul_phase`。提交 `refactor(agent): 主叙事单次写作循环替代两阶段 FC`。

## Task 3.5: 上下文构建器（context）

**Files:**
- Rewrite: `agent/context.py`
- Test: `tests/agent/test_context.py`

组装优先级：Canon → recentScript → continuitySnapshot → 分层记忆召回 → Alter 氛围 → Agency 容量 → 结构化输出指令 → 时间端点（UTC + nowLocal + 时段），字符预算约束。提交 `refactor(agent): 重写上下文构建器支撑主叙事`。

## Task 3.6: 会话装配 + WS/GUI 协议 + 渠道接口重构

**Files:**
- Rewrite: `agent/session.py`, `agent/ws.py`
- Rewrite: `agent/channels/__init__.py`, `agent/channels/feishu_*.py`, `agent/channels/wechat_*.py`
- Modify: `agent/app.py`

**接口层重构**：
- `agent/ws.py`：新线上协议（WS/GUI）承载主叙事事件（turn_metadata / alter_triggered / proactive_contact / scene_closed / agency_decision），客户端消息类型（user_message / stop / confirm_response / get_state / list_sessions 等）重新定义。
- `agent/session.py`：`build_agent_session` 装配分层记忆门面 + narrator + Agency/Alter；会话生命周期适配新串行队列。
- `agent/channels/`：EventSink 渠道接口与 feishu/wechat 渠道重新设计，适配新事件流与主叙事生命周期，替代旧 sink 广播模型。
- `agent/app.py`：路由装配适配新协议。
- 提交 `refactor(agent): 会话装配、WS/GUI 协议与渠道接口整体重构`。

**阶段 3 完成检查点**：`uv run pytest tests/agent -v --no-cov` 全绿，主叙事循环可用。

---

# 阶段 4：Alter 动态情绪 + Agency 主体约束 + 休息窗口

## Task 4.1: Alter System（alter.py）

**Files:**
- Create: `agent/emotion/alter.py`（AlterState 状态机 + 动态阈值 + 权重生命周期 + 侧端分析）
- Rewrite: `agent/emotion/__init__.py`
- Delete: `agent/emotion/observer.py`, `agent/emotion/smoother.py`, `agent/emotion/tone_injector.py`, `agent/emotion/emotion_state.py`（旧固定情绪引擎）
- Test: `tests/agent/test_alter.py`

实现累计 alter、动态阈值（`threshold = base × (1 - density × factor)`）、权重生命周期（同向增强/反向衰减/过低清除）、后台侧端分析（不阻塞可见回复）。提交 `feat(emotion): Alter 动态氛围阈值系统替代固定情绪引擎`。

## Task 4.2: Agency Window（agency.py）

**Files:**
- Create: `agent/proactive/agency.py`
- Test: `tests/agent/test_agency.py`

三因素门控（activityLoad/privacy/deviceAccess）+ 容量矩阵 + 联系候选验证 + recheck-later（复用 intent 表创建 proactive-check）。提交 `feat(proactive): Agency Window 三因素主体约束`。

## Task 4.3: 休息窗口 + 后台调度器重写（rest_windows.py + scheduler.py）

**Files:**
- Create: `agent/proactive/rest_windows.py`
- Rewrite: `agent/proactive/scheduler.py`（三来源调度：自动推进/到期 intent/proactive-check，全部经 queue.py 串行进入主叙事；替代旧 schedule/idle 触发式逻辑）
- Test: `tests/agent/test_rest_windows.py`, `tests/agent/test_scheduler.py`

休息窗口约束自动推进间隔；新 scheduler 消费三类来源经串行队列进入主叙事，Agency 在投递前裁决联系候选。提交 `feat(proactive): 休息窗口 + 重写后台调度器（移除旧 schedule/idle 触发式）`。

## Task 4.4: 循环集成
将 Alter/Agency/休息窗口注入主叙事上下文与受限行动门控；`loop.py` 在投递受限行动前调用 Agency 裁决。提交 `feat(agent): 主循环集成 Alter/Agency/休息窗口`。

**阶段 4 完成检查点**：情绪/主体约束全绿，旧引擎/调度器移除。

---

# 阶段 5：场景/弧线管理 + 日志系统整体重写

## Task 5.1: 场景/弧线管理（scenes.py）
`core/memory/scenes.py`：SceneEntry/Scene/Arc，阈值触发关闭 + LLM 压缩。提交 `feat(memory): 场景/弧线管理与自动压缩`。

## Task 5.2: 分层日志核心（layered.py）
`core/logger/layered.py`：阶段感知 + **LogAction 16 枚举**（receive/send/processing/complete/trigger/emotion/memory/advance/agency/group/error/retry/warning/waiting/system）动作检测 + 明暗 256 色主题 + KAOMOJI/SYMBOLS 双模式 + 三档密度（summary<standard<diagnostic 与 level 独立叠加）+ tree 字段布局（detectLogAction/extractFields）。提交 `feat(logger): 分层日志核心 LayeredLogFormatter`。

## Task 5.3: 日志管理器重写 + 失明模式（manager/formatter）
重写 `core/logger/formatter.py` 与 `core/logger/manager.py` 承载分层输出 + **失明模式（blindMode）**：静默拦截命令、error/warn 置盲标志并丢弃（隐藏错误/剧本预览）、健康心跳仅输出 `[失明模式] 运行状态=正常|需关注` 无内容细节、`healthReportMinutes` 默认 10（钳制 1-1440）。保留 `setup/get_logger` 入口。提交 `refactor(logger): 日志系统整体重写为分层模式 + 失明模式`。

## Task 5.4: 配置与文档
更新 `data/config/main.yml` 增加 layered/blind_mode/agent 结构化输出/Alter/Agency/rest_windows/memory layers/scenes 配置；更新 CLAUDE.md 架构总览。提交 `docs(config): 新增分层日志与主叙事相关配置`。

**阶段 5 完成检查点**：`uv run pytest --no-cov` 全绿，`uv run black . && isort .` 通过。

---

## 测试策略

- **pytest markers**：按 `pyproject.toml` 现有 markers（unit/integration/slow/memory/llm）扩展 `story`/`alter`/`agency`/`scene`/`logger`；sqlite-vec 相关用 `integration` marker（依赖扩展加载）。
- **每阶段 TDD**：每个 Task 遵循"失败测试 → 实现 → 通过 → 提交"；阶段完成检查点运行该阶段测试目录。
- **降级测试**：sqlite-vec 加载失败降级内存路径、Alter/Agency 禁用路径、JSON 解析失败降级纯文本路径、场景压缩 LLM 失败简单摘要路径——均需覆盖。
- **存储测试**：SQLite 用 `tmp_path` 临时库，测试持久化跨重开（`SQLiteVectorStore` 已有此测试）。
- **时间测试**：固定时区与时刻注入，覆盖时段分界、跨午夜休息窗口。
- **多参与者测试**：`shareParticipantDetails` 开关下隔离行为断言。
- **运行**：`uv run pytest -m "not integration"`（日常）、`uv run pytest`（含集成）。

## 最终验收

1. `uv run pytest --no-cov` 全绿
2. `uv run mypy agent core` 通过
3. `uv run python main.py` 可启动主叙事循环（WS 新协议）
4. 旧系统已从代码库移除：两阶段循环（_tool_phase/_soul_phase）、旧 GRAG+层次化记忆、旧固定情绪引擎、旧 schedule/idle 调度器、core/tts 模块
5. docker compose/start 脚本保留（AstraTTS 配置未动）
6. 所有接口重构落地：WS/GUI 新协议、事件模型新协议、渠道接口重构
7. `docs/plans/2026-08-28-hdsi-refactor-design.md` 中所有阶段落地

## 参考资源

- 设计文档：`docs/plans/2026-08-28-hdsi-refactor-design.md`
- HDSI 参考实现：`example/HDS-Interlude/`（README.md、docs/ARCHITECTURE.md、ALTER_SYSTEM.md、AGENCY_WINDOW.md）
- sqlite-vec Python 用法：`pip install sqlite-vec`，`sqlite_vec.load(db)`，`CREATE VIRTUAL TABLE ... USING vec0(embedding float[N])`，`WHERE embedding MATCH ? ORDER BY distance LIMIT k`
