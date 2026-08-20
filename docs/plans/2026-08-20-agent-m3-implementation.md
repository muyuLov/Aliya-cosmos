# Aliya-cosmos Agent M3 实施计划：知识外扩

> 里程碑定位：M1=基础对话闭环（FC 两阶段循环 + WS 接口），M2=伴侣能力（情绪引擎 + 主动聊天 + 多会话）。
> **M3 = 知识外扩**：让 Agent 能接入三类外部知识/能力源——①RAG 文档库、②Skill 可插拔技能、③MCP 协议服务。
> 参考 `example/Cyrene-Agent-master` 的 `mcp-adapter.ts` / `mcp-manager.ts` / `skills/cyrene-music-companion` / `main/sync-mcp-builtin.ts` 的模式，但实现完全贴合本项目 `core/vector`、`core/memory`、`agent/tools` 的真实接口。

---

## 0. 集成点总览（本文档所有任务的前提，请勿改动）

| 组件 | 真实接口（已存在，M1/M2 实施） | M3 用法 |
|------|------|------|
| 工具注册 | `ToolRegistry.register(def, executor)` / `build_tools_schema()` / `execute(tool_id, ctx, args)`（M1 已有；**无** `register_mcp_tools`，M3 一律用 `register`） | M3 新工具（RAG/Skill/MCP 代理）全部经 `register` 注入 |
| 工具上下文 | `ToolContext(user_query, conversation_id, memory)`（真实字段，frozen 为 `ToolDefinition`） | RAG/Skill/MCP 工具经 `ctx.memory` 取 `GRAGMemoryManager`，经 `ctx.conversation_id` 取会话 ID |
| 工具执行 | `loop.py` 的 `_tool_phase` 已处理 `pending_confirmations` 与 executor 调用 | M3 新增工具无需改 loop |
| RAG 检索 | `VectorStore.search_async(query, top_k, threshold)` / `add_many(items)` / `aclose()` | 新建 `KnowledgeStore` 封装，工具层经它读写 |
| Embedding | `EmbeddingFactory.create()` / `EmbeddingProvider.embed(texts)` / `dimension` | 文档索引与查询向量化 |
| 长期记忆 | `core/memory.memory_manager.get_memory_manager()` → `GRAGMemoryManager`；`query_memory(question)` / `get_relevant_memories(...)` / `add_conversation_memory(...)` | M3 Part A RAG 检索结果可经 `query_memory` 沉淀复用（可选，不强制） |
| 上下文构建器 | `agent/context.py` 的 `ContextBuilder`（**注意：无 M2 计划中的 `AliyaContext`**）；现有 `build_tool_system()` 读 `tools_system.md` | M3 在 `ContextBuilder` **新增** `build_mcp_system()` 方法注入 MCP 服务清单 |
| 主循环 | `AgentLoop(service, registry, checker, context, *, max_tool_rounds, tool_timeout, confirm_timeout, memory)` | M3 不改签名；registry 在构造前已注入全部工具 |
| 配置 | `data/config/main.yml` 的 `cosmos.service.vector.*`（RAG 复用，默认 `storage: milvus` 持久化）、新增 `cosmos.agent.knowledge.dir`（默认 `data/knowledge`，启动索引目录）、新增 `cosmos.agent.mcp.servers`（MCP 服务器列表） | M3 不改动既有 vector 配置 |

**关键约束**：M3 所有能力均以「工具（Tool）」形式进入 FC 循环，不改动 `AgentLoop` 两阶段逻辑、不改动 WS 协议、不改动情绪引擎。这是方案 A「进程内分层 + 工具即能力」的自然延伸。

---

## Part A：RAG 文档库

> 对标 Cyrene 的 `Orchestrator` 检索增强（`retrieveContext` / `retrieveMemories`），但用本项目 `core/vector` 实现。

### A1. 知识存储封装 `agent/knowledge/store.py`

新建模块，封装 `VectorStore` + `EmbeddingFactory`，对 Agent 暴露「索引文档 / 检索片段」两个语义操作。

```python
"""RAG 知识库：封装 core/vector，提供文档索引与片段检索。"""
from __future__ import annotations

from core.vector.embedding import EmbeddingFactory
from core.vector.store import VectorStore, SearchResult, VectorConfig, get_vector_config
from core.logger import get_logger

logger = get_logger(__name__)


class KnowledgeStore:
    """单例知识库：持有 VectorStore 与 embedding 提供者。

    文档以「片段（chunk）」为单位切分后入库；检索返回 top-k 片段。
    """

    def __init__(self, config: VectorConfig | None = None) -> None:
        # 真实接口：EmbeddingFactory.create(config: VectorConfig) 必传 config；
        # config 缺 embedding.model/url 时 OpenAIEmbeddingProvider 直接抛 VectorConfigError（无静默降级）。
        cfg = config or get_vector_config()
        embedding = EmbeddingFactory.create(cfg)
        self._store = VectorStore(embedding, cfg)

    async def index_document(
        self,
        doc_id: str,
        title: str,
        chunks: list[str],
    ) -> list[str]:
        """将一篇文档的若干片段入库，返回片段条目 ID 列表。"""
        items = [
            {"text": c, "metadata": {"doc_id": doc_id, "title": title}}
            for c in chunks
        ]
        return await self._store.add_many(items)

    async def search(self, query: str, top_k: int = 5, threshold: float | None = None) -> list[SearchResult]:
        """检索与 query 最相关的片段（低于阈值被过滤）。"""
        return await self._store.search_async(query, top_k=top_k, threshold=threshold)

    async def aclose(self) -> None:
        await self._store.aclose()
```

**验证**：`pytest tests/test_knowledge_store.py`
- 单测用 `VectorConfig(storage="memory", embedding=EmbeddingConfig(model=..., url=...))` 构造（避免依赖 Milvus），`index_document` 返回非空 ID 列表；
- `search` 对索引过的片段能返回 score > threshold 的结果；
- `aclose` 不抛异常。
- **持久化说明**：`KnowledgeStore` 复用全局 `cosmos.service.vector` 配置（默认 `storage: milvus`），生产环境 RAG 知识库随 Milvus 持久化、重启不丢；测试用 `memory` 隔离。

### A2. 文档加载器 `agent/knowledge/loader.py`

新建模块，将 `data/knowledge/*.md`（或配置的目录）切分为片段（按标题/空行，chunk_size≈500 字）。

```python
"""文档加载：将 Markdown 文件切分为检索片段。"""
from __future__ import annotations

import re
from pathlib import Path

_CHUNK_SIZE = 500  # 每片段目标字符数


def split_markdown(text: str, chunk_size: int = _CHUNK_SIZE) -> list[str]:
    """按段落切分，长段落再按句界硬切，避免超出 chunk_size。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 1 <= chunk_size:
            buf = f"{buf}\n{p}".strip()
        else:
            if buf:
                chunks.append(buf)
            # 长段落按句切
            if len(p) > chunk_size:
                for s in re.split(r"(?<=[。！？])", p):
                    s = s.strip()
                    if not s:
                        continue
                    if len(buf) + len(s) + 1 <= chunk_size:
                        buf = f"{buf}\n{s}".strip()
                    else:
                        if buf:
                            chunks.append(buf)
                        buf = s
            else:
                buf = p
    if buf:
        chunks.append(buf)
    return chunks


def load_directory(directory: str | Path) -> list[tuple[str, str, list[str]]]:
    """读取目录下全部 .md，返回 [(doc_id, title, chunks)]。

    doc_id = 文件名（去扩展名）；title = 首个一级标题或文件名。
    """
    directory = Path(directory)
    docs: list[tuple[str, str, list[str]]] = []
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        title = title.group(1).strip() if title else path.stem
        docs.append((path.stem, title, split_markdown(text)))
    return docs
```

**验证**：`tests/test_knowledge_loader.py`
- `split_markdown` 对长文本产出每段 ≤ `chunk_size + 容差` 的片段；
- `load_directory` 对 `tests/fixtures/knowledge/*.md` 返回正确 (doc_id, title, chunks)。

### A3. 索引引导 `agent/knowledge/__init__.py` + 启动钩子

在 `agent/knowledge/__init__.py` 暴露 `get_knowledge_store()` 懒加载单例，并新增 `index_knowledge_directory()` 在应用启动时调用（经 `SessionManager`/`main.py` 引导，M3 仅提供函数，不强制改动 main）。

```python
"""知识库懒加载单例。"""
from __future__ import annotations

from pathlib import Path

from core.logger import get_logger
from core.vector.config import VectorConfig

from agent.knowledge.loader import load_directory
from agent.knowledge.store import KnowledgeStore

logger = get_logger(__name__)

_store: KnowledgeStore | None = None


def get_knowledge_store(config: VectorConfig | None = None) -> KnowledgeStore:
    global _store
    if _store is None:
        _store = KnowledgeStore(config)
    return _store


async def index_knowledge_directory(directory: str | Path) -> int:
    """启动时一次性索引目录下全部 .md，返回索引片段总数；目录不存在/为空时安全跳过。"""
    store = get_knowledge_store()
    docs = load_directory(directory)
    if not docs:
        return 0
    total = 0
    for doc_id, title, chunks in docs:
        await store.index_document(doc_id, title, chunks)
        total += len(chunks)
    logger.info("知识库索引完成：%d 篇文档，%d 个片段", len(docs), total)
    return total
```

**验证**：`get_knowledge_store()` 多次调用返回同一实例；`index_knowledge_directory("data/knowledge")` 不抛异常（目录为空时返回 0）。

### A4. RAG 检索工具 `agent/tools/rag.py`

仅注册一个工具 `search_knowledge`（检索）。**不暴露 `index_knowledge` 工具**——文档索引由 A3 的启动函数 `index_knowledge_directory()` 在应用启动时一次性完成，避免让模型在对话中随意重索引整库。

```python
"""RAG 工具：让 Agent 在对话中检索知识库。"""
from __future__ import annotations

from agent.knowledge import get_knowledge_store
from agent.tools.base import ToolContext, ToolDefinition

_RAG_TOOL = ToolDefinition(
    id="search_knowledge",
    name="search_knowledge",
    description="当用户问题涉及 Aliya 的背景知识、设定、过往记录或文档内容时调用，"
                "从知识库检索相关片段。参数 query 为检索关键词或问题。",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索问题或关键词"},
            "top_k": {"type": "integer", "description": "返回片段数，默认 5", "default": 5},
        },
        "required": ["query"],
    },
    enabled=True,
)


async def search_knowledge(ctx: ToolContext, args: dict) -> str:
    # ctx 真实字段：user_query / conversation_id / memory（GRAGMemoryManager | None）
    store = get_knowledge_store()
    query = args["query"]
    top_k = int(args.get("top_k", 5))
    results = await store.search(query, top_k=top_k)
    if not results:
        return "（知识库无相关片段）"
    lines = []
    for i, r in enumerate(results, 1):
        title = r.metadata.get("title", "未知文档")
        lines.append(f"[{i}] 来自《{title}》\n{r.text}")
    return "\n\n".join(lines)
```

**验证**：`tests/test_rag_tool.py`
- 预先 `index_document` 一篇测试文档；
- `search_knowledge(ctx, {"query": ...})` 返回含文档标题的文本；
- 未索引时返回「知识库无相关片段」安全文案，不崩溃。

### A5. 工具注册接入 `agent/tools/builtin/__init__.py`

在 `register_builtin_tools(registry)` 中追加 `register(search_knowledge_def, search_knowledge)`（导入自 `agent/tools/rag.py`），并导出 `search_knowledge_def`。

**验证**：`build_tools_schema()` 输出的 tools 数组包含 `search_knowledge`；M1 既有测试仍通过（无回归）。

---

## Part B：Skill 可插拔技能

> 对标 Cyrene 的 `skills/cyrene-music-companion`（每个 skill 自带 `index.ts` + `SKILL.md`，通过 `tool-catalog` 注册为 FC 工具）。
> 本项目无 TS 运行时，采用**「Python 模块即 Skill」**等价方案：每个 skill 是一个模块，导出 `definition: ToolDefinition` 与 `execute(ctx, args)`，由 `skill_loader` 自动发现并注册。

### B1. Skill 协议 `agent/skills/base.py`

```python
"""Skill 协议：一个 skill 即一个可被 FC 调用的工具。"""
from __future__ import annotations

from agent.tools.base import ToolDefinition, ToolExecutor

# 每个 skill 模块必须导出：
#   definition: ToolDefinition
#   execute: ToolExecutor
```

仅作约定文档，无运行时强制（避免过度工程）。Skill 模块放在 `agent/skills/<name>/__init__.py`。

### B2. Skill 自动发现 `agent/skills/loader.py`

```python
"""Skill 加载器：扫描 agent/skills/*/ 下合规模块，注册到 ToolRegistry。"""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from agent.tools.registry import ToolRegistry
from agent.tools.base import ToolDefinition, ToolExecutor
from core.logger import get_logger

logger = get_logger(__name__)
_SKILLS_DIR = Path(__file__).resolve().parent


def load_skills(registry: ToolRegistry) -> int:
    """扫描并注册全部 skill，返回注册数量。"""
    count = 0
    for modinfo in pkgutil.iter_modules([str(_SKILLS_DIR)]):
        name = modinfo.name
        if name in ("base", "loader", "__pycache__"):
            continue
        try:
            module = importlib.import_module(f"agent.skills.{name}")
            definition = getattr(module, "definition", None)
            execute = getattr(module, "execute", None)
            if isinstance(definition, ToolDefinition) and callable(execute):
                registry.register(definition, execute)
                count += 1
                logger.info("已加载 skill: %s", name)
            else:
                logger.warning("skill %s 缺少 definition/execute，跳过", name)
        except Exception as e:
            logger.warning("加载 skill %s 失败: %s", name, e)
    return count
```

**验证**：`tests/test_skill_loader.py`
- 在 fixtures 下放置一个最小合规 skill 模块，`load_skills(fake_registry)` 返回 ≥1 且 `build_tools_schema` 含该 skill 的 name；
- 非法 skill（缺字段）被跳过且不抛异常。

### B3. 示例 Skill `agent/skills/dice/__init__.py`

提供可运行的示例，验证链路：

```python
"""示例 Skill：掷骰子。"""
from __future__ import annotations

import random

from agent.tools.base import ToolDefinition, ToolContext


definition = ToolDefinition(
    id="roll_dice",
    name="roll_dice",
    description="掷骰子游戏：当用户想掷骰、抽奖或做随机数决定时调用。",
    input_schema={
        "type": "object",
        "properties": {
            "sides": {"type": "integer", "description": "面数，默认 6", "default": 6},
            "count": {"type": "integer", "description": "骰子数，默认 1", "default": 1},
        },
        "required": [],
    },
    enabled=True,
)


async def execute(ctx: ToolContext, args: dict) -> str:
    # ctx 真实字段：user_query / conversation_id / memory
    sides = int(args.get("sides", 6))
    count = int(args.get("count", 1))
    rolls = [random.randint(1, sides) for _ in range(max(1, count))]
    return f"掷出：{rolls}（合计 {sum(rolls)}）"
```

**验证**：`roll_dice` 经 loop 实测可调用；`execute` 返回格式正确的文本。

### B4. Skill 加载接入 `agent/tools/builtin/__init__.py` 的 `register_builtin_tools`

在 `register_builtin_tools` 末尾追加 `load_skills(registry)`（导入自 `agent/skills/loader.py`）。

**验证**：启动日志出现「已加载 skill: dice」；`tools_schema` 含 `roll_dice`。

---

## Part C：MCP 协议接入

> 对标 Cyrene 的 `mcp-manager.ts`（连接 stdio/SSE 服务器，拉取工具清单）+ `mcp-adapter.ts`（把 MCP 工具转成 FC 工具并动态注册）+ `main/sync-mcp-builtin.ts`（启动时同步内置 MCP）。
> 采用官方 `mcp` Python SDK（`FastMCP` 客户端 / `ClientSession`）。M3 实现 **stdio** 与 **SSE** 两种传输，动态注册远端工具为本地 FC 工具。

### C1. MCP 客户端封装 `agent/mcp/client.py`

```python
"""MCP 客户端：连接 stdio / SSE 服务器，拉取并缓存工具清单。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from core.logger import get_logger

logger = get_logger(__name__)

# 若环境无 mcp SDK：from mcp import ClientSession 等
# M3 以「传输类型 + 启动参数」描述一个服务器，连接后把远端工具映射为本地 ToolDefinition


@dataclass
class McpServerSpec:
    name: str
    transport: str  # "stdio" | "sse"
    command: list[str] | None = None   # stdio: [exe, arg, ...]
    url: str | None = None             # sse: http(s) endpoint
    enabled: bool = True


@dataclass
class McpToolProxy:
    server: str
    name: str
    description: str
    input_schema: dict
    # 调用时经 MCP session 执行；M3 用占位 callable 表达「代理执行」
    invoke: Any = field(default=None, repr=False)


async def connect_server(spec: McpServerSpec) -> list[McpToolProxy]:
    """连接服务器并拉取工具清单，返回 McpToolProxy 列表。

    真实实现依赖 `mcp` SDK：
      - stdio：mcp.client.stdio.stdio_client + ClientSession
      - sse：mcp.client.sse.sse_client + ClientSession
    把远端 list_tools 结果映射为 McpToolProxy（invoke 封装 session.call_tool）。
    连接或握手失败直接抛异常，由 sync_mcp_servers 捕获并隔离。
    """
    # M3 实装位置：在此组装 MCP 客户端会话并填充 proxy.invoke
    raise NotImplementedError("connect_server 需在实装时接入 mcp SDK")
```

**注意**：M3 不内联实现完整 MCP 协议握手（依赖 `mcp` SDK）。`client.py` 暴露 `connect_server(spec)` 与数据类 `McpServerSpec` / `McpToolProxy`，真实握手在 `connect_server` 内部完成。

**验证**：`tests/test_mcp_client.py`
- 用 `mcp` SDK 自带的 `FastMCP` 起一个内存测试服务器（官方 `mcp` 提供 `server` 测试工具），`connect_server` 能 `list_tools` 拿到 ≥1 个工具；
- 若 SDK 不可用，该测试 `skip`（不阻塞 CI）。

### C2. MCP 工具适配 `agent/mcp/adapter.py`

将远端 MCP 工具转为本地 `ToolDefinition` + `ToolExecutor`，经 `registry.register()` 注册（与 M3 其他工具一致，无 `register_mcp_tools`）。

```python
"""MCP 适配器：把远端工具映射为本地 FC 工具。"""
from __future__ import annotations

from agent.mcp.client import McpServerSpec, McpToolProxy
from agent.tools.base import ToolDefinition, ToolContext
from agent.tools.registry import ToolRegistry
from core.logger import get_logger

logger = get_logger(__name__)


def _make_executor(proxy: McpToolProxy):
    async def _exec(ctx: ToolContext, args: dict) -> str:
        # proxy.invoke 为 MCP session.call_tool 的封装
        if proxy.invoke is None:
            return "[MCP 未连接]"
        try:
            return await proxy.invoke(args)
        except Exception as e:
            logger.warning("MCP 工具 %s/%s 调用失败: %s", proxy.server, proxy.name, e)
            return f"[MCP 工具调用失败: {e}]"
    return _exec


def register_mcp_server(registry: ToolRegistry, proxies: list[McpToolProxy]) -> None:
    """把一批 MCP 工具代理注册进 registry。"""
    for proxy in proxies:
        if not proxy.invoke:
            continue
        definition = ToolDefinition(
            id=f"mcp__{proxy.server}__{proxy.name}",
            name=f"mcp__{proxy.server}__{proxy.name}",
            description=proxy.description,
            input_schema=proxy.input_schema,
            enabled=True,
            risk="medium",  # 远端工具不可控，标为中等风险（真实 ToolDefinition 字段）
        )
        registry.register(definition, _make_executor(proxy))
```

**验证**：`tests/test_mcp_adapter.py`
- 构造一个带 `invoke` 的 `McpToolProxy`，`register_mcp_server` 后 `build_tools_schema` 含 `mcp__<server>__<name>`；
- `execute` 经 `invoke` 返回结果；`invoke` 抛异常时返回安全失败文案。

### C3. MCP 启动同步 `agent/mcp/__init__.py` + 配置段

读取 `data/config/main.yml` 的 `cosmos.agent.mcp.servers`（列表，每项含 `name/transport/command/url/enabled`），在 `agent/ws.py` 的 `_default_session_factory` 中 `await sync_mcp_servers(registry, specs)`（位于 `register_builtin_tools` 之后）。

```python
"""MCP 启动同步：按配置连接服务器并注册工具。"""
from __future__ import annotations

from typing import Any

from agent.mcp.adapter import register_mcp_server
from agent.mcp.client import McpServerSpec, connect_server
from agent.tools.registry import ToolRegistry
from core.config import get_config_instance
from core.logger import get_logger

logger = get_logger(__name__)


def load_mcp_specs(config_path: str = "data/config/main.yml") -> list[McpServerSpec]:
    cfg = get_config_instance(config_path)
    raw = cfg.get("cosmos.agent.mcp.servers") or []
    specs: list[McpServerSpec] = []
    for item in raw:
        if not item.get("enabled", True):
            continue
        specs.append(
            McpServerSpec(
                name=item["name"],
                transport=item.get("transport", "stdio"),
                command=item.get("command"),
                url=item.get("url"),
                enabled=True,
            )
        )
    return specs


async def sync_mcp_servers(registry: ToolRegistry, specs: list[McpServerSpec]) -> tuple[int, list[str]]:
    """连接全部启用服务器，注册其工具。

    返回 (注册工具总数, 成功连接的服务器名列表)；失败服务器被隔离，不阻断其他服务器。
    """
    total = 0
    connected: list[str] = []
    for spec in specs:
        try:
            proxies = await connect_server(spec)  # 见 C1 connect_server
            register_mcp_server(registry, proxies)
            total += len(proxies)
            connected.append(spec.name)
            logger.info("MCP 服务器 %s 已注册 %d 个工具", spec.name, len(proxies))
        except Exception as e:
            logger.warning("MCP 服务器 %s 连接失败（跳过）: %s", spec.name, e)
    return total, connected
```

**验证**：`tests/test_mcp_sync.py`
- 配置段含一个内存 FastMCP 测试服务器；`sync_mcp_servers` 后 `build_tools_schema` 含其工具，返回元组 `(n, [server_name])`；
- 配置中 `enabled: false` 的服务器被跳过；连接失败服务器不抛异常、不阻塞其他服务器，且不在 `connected` 列表中。

### C4. `ContextBuilder.build_mcp_system()` 新增

`agent/context.py` 的 `ContextBuilder` 当前**只有** `build_tool_system()`（读 `tools_system.md`），**没有** `build_mcp_system`。M3 在 `ContextBuilder` 中**新增**该方法，并把已连接服务器名作为实例字段注入。

```python
# agent/context.py —— 在 ContextBuilder.__init__ 增加：
#   self.available_mcp_servers: list[str] = []
# 新增方法：
def build_mcp_system(self) -> str:
    """返回已连接 MCP 服务清单（注入人设 system，与 build_tool_system 并列）。

    无已连接服务器时返回空字符串（不污染 prompt）。
    """
    if not self.available_mcp_servers:
        return ""
    names = "、".join(self.available_mcp_servers)
    return f"## 可用外部服务（MCP）\n当前可调用以下 MCP 服务提供的工具：{names}。"
```

`sync_mcp_servers` 注册成功后，将已连接服务器名写入 `builder.available_mcp_servers`（D1 工厂中完成）。

**验证**：`build_mcp_system()` 在 MCP 同步后返回非空服务清单；未连接时返回空字符串（不破坏既有 `build_tool_system` 契约）。

---

## Part D：集成与端到端验证

### D1. 注册入口与顺序

**同步部分**（`agent/tools/builtin/__init__.py` 的 `register_builtin_tools`）：
1. 既有内置工具（`register_time_tool` / `register_memory_tools`）
2. `register(search_knowledge_def, search_knowledge)`（Part A）
3. `load_skills(registry)`（Part B）

**异步部分**（`agent/ws.py` 的 `_default_session_factory`，在 `register_builtin_tools(registry)` 之后）：
4. `await index_knowledge_directory(cfg.get("cosmos.agent.knowledge.dir", "data/knowledge"))`（Part A3，启动即索引；目录不存在/为空安全跳过，RAG 工具退化为空库降级）
5. `total, connected = await sync_mcp_servers(registry, load_mcp_specs())`（Part C）
6. 把 `connected`（成功连接的服务器名列表）写入 `ContextBuilder.available_mcp_servers`（供 C4）

> `sync_mcp_servers` 与 `index_knowledge_directory` 均为异步，必须由工厂 `await`；registry 复用于多会话，仅初始化一次。索引失败不应阻断 MCP 同步（各自 try 隔离）。

### D2. 端到端冒烟 `tests/test_m3_e2e.py`

1. 构造 `ToolRegistry`，`await` 完成 M3 全部注册；
2. `build_tools_schema()` 同时包含 `search_knowledge`、`roll_dice`、`mcp__*`（若有启用服务器）；
3. 用一篇 fixtures 文档走 `await get_knowledge_store().index_document(...)` → `await search_knowledge(ctx, {...})` → 断言返回片段；
4. `roll_dice` 经 `registry.execute` 返回合法文本；
5. 确认 `AgentLoop._tool_phase` 能正常调度上述工具（复用 M1 测试夹具）。

### D3. 文档与记忆（可选，非阻塞）

- RAG 检索结果可经 `core.memory.memory_manager.get_memory_manager()` 取 `GRAGMemoryManager`，调用 `query_memory(question)` 复用既有长期记忆。**M3 不强制实现**，仅在 `search_knowledge` 经 `ctx.memory` 留出接入点。

---

## 验证清单（M3 完成标准）

| 能力 | 验证项 |
|------|--------|
| RAG | `search_knowledge` 工具经 loop 可调，返回知识库片段；空库安全降级 |
| Skill | `load_skills` 自动发现并注册 `agent/skills/*`；示例 `roll_dice` 可运行 |
| MCP | `sync_mcp_servers` 按配置连接 stdio/SSE 服务器并注册远端工具为 FC 工具；失败隔离 |
| 集成 | `build_tools_schema` 同时含三类工具；M1/M2 既有测试无回归 |
| 契约 | WS 协议、情绪引擎、两阶段 FC 循环均**未改动** |

---

## 实施顺序建议（每步可独立验证）

```
A1 知识库封装      → 单测 test_knowledge_store
A2 文档加载器      → 单测 test_knowledge_loader
A3 懒加载单例      → 单测 get_knowledge_store
A4 RAG 检索工具    → 单测 test_rag_tool
A5 注册接入        → 跑通 build_tools_schema 含 search_knowledge
B1 Skill 协议      → 仅约定（无单测）
B2 自动发现        → 单测 test_skill_loader
B3 示例 dice       → 经 loop 实测
B4 加载接入        → 启动日志确认
C1 MCP 客户端      → 单测 test_mcp_client（SDK 缺失则 skip）
C2 适配器          → 单测 test_mcp_adapter
C3 启动同步        → 单测 test_mcp_sync
C4 context 填充    → build_mcp_system 验证
D1-D3 集成冒烟     → test_m3_e2e + 全量 pytest 无回归
```

> 范围克制说明：M3 不实现 GUI 侧知识库管理界面、不做文档增量更新 UI、不实现 MCP 鉴权中间件——这些属于后续里程碑。M3 目标是让 Agent **在对话中能检索知识、调用 Skill、使用 MCP 工具**，能力以 FC 工具形式闭环。
