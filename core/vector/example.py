"""vector 模块使用示例

演示向量化与向量检索的各种使用方式：
- 示例 1：全局单例（get_vector_store）构建记忆库并检索
- 示例 2：批量添加（add_many）与 top-k / 阈值检索
- 示例 3：直接构造 VectorStore（自定义 Embedding 配置）
- 示例 4：同步检索（独立脚本 / REPL 场景）
- 示例 5：元数据、删除、清空等管理操作
- 示例 6：配置缺失时的错误处理与引导

依赖服务：OpenAI 兼容 Embedding API（如 Ollama、LM Studio 或云厂商），
需在 data/config/main.yml → cosmos.service.vector.embedding 中配置 model。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if __name__ == "__main__":
    project_root = Path(__file__).parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from core.exception import get_default_handler
from core.logger import setup as setup_logger
from core.vector import (
    EmbeddingConfig,
    OpenAIEmbeddingProvider,
    VectorConfig,
    VectorStore,
    add,
    add_many,
    clear,
    count,
    delete,
    get_vector_config,
    get_vector_store,
    reset_vector_store,
    search,
    search_async,
)
from core.vector.exceptions import VectorConfigError, VectorError


def _check_embedding_ready() -> bool:
    """检查向量模块 embedding 是否已配置（model 非空）。

    当前设计要求显式配置 embedding 模型名（LLM 的 chat 模型不能用于 embedding），
    未配置时打印引导并返回 False，示例提前退出。
    """
    cfg = get_vector_config()
    if not cfg.enabled:
        print("[WARN] 向量模块未启用：cosmos.service.vector.enabled: true")
        return False
    if not cfg.embedding.model:
        print("[WARN] 未配置 embedding 模型名，示例跳过真实 API 调用：")
        print("  请在 data/config/main.yml → cosmos.service.vector.embedding 配置：")
        print("    model: text-embedding-3-small   # 或本地 Ollama/LM Studio 的 embedding 模型")
        print("    url: http://localhost:11434     # 服务地址")
        print("    api_key: <your-key>            # API 密钥（本地服务可填任意非空值）")
        return False
    return True


def _print_results(results, title: str) -> None:
    """格式化打印检索结果（score / 文本 / 元数据）。"""
    print(title)
    if not results:
        print("  (无结果，所有条目低于相似度阈值)")
        return
    for r in results:
        tag = r.metadata.get("tag") or r.metadata.get("topic")
        suffix = f"  (tag={tag})" if tag else ""
        print(f"  {r.score:.4f}  {r.text}{suffix}")


async def example_global_store() -> None:
    """示例 1：通过全局单例构建记忆库并检索（推荐）。

    使用 get_vector_store() 获取线程安全懒加载单例，
    向量仅保存在进程内存中，进程退出即清空。
    """
    print("\n=== 示例 1：全局单例添加与检索 ===")

    if not _check_embedding_ready():
        return

    try:
        store = get_vector_store()
        print(f"向量库状态: provider={store.embedding.provider_name} count={store.count}")

        # 写入多条带元数据的记忆，构成一个小型记忆库
        docs = [
            {"text": "Aliya 喜欢在深空探索时听古老的音乐", "metadata": {"tag": "爱好"}},
            {"text": "Aliya 最想念泰瑞斯公司庭院里的四季花", "metadata": {"tag": "情感"}},
            {"text": "Aliya 驾驶的海獭号正驶向深空深处", "metadata": {"tag": "现状"}},
        ]
        for doc in docs:
            await add(doc["text"], metadata=doc["metadata"])
        print(f"[OK] 已写入 {len(docs)} 条记忆，当前条目数: {count()}")

        # 语义检索：默认阈值 0.5，低相关条目会被过滤
        _print_results(
            await search_async("Aliya 喜欢什么", top_k=5),
            title="\n检索「Aliya 喜欢什么」（top_k=5，默认阈值 0.5）:",
        )

    except VectorError as e:
        get_default_handler().handle(e)


async def example_batch_search() -> None:
    """示例 2：批量添加与 top-k / 阈值检索。

    演示 add_many() 一次写入多条，以及 top_k、threshold 对检索结果的控制。
    """
    print("\n=== 示例 2：批量添加与检索参数 ===")

    if not _check_embedding_ready():
        return

    try:
        docs = [
            {"text": "Kane 是个技术很强但经常被通报批评的同事", "metadata": {"topic": "人物"}},
            {"text": "海獭号是 Aliya 驾驶的深空探索船", "metadata": {"topic": "物品"}},
            {"text": "泰瑞斯公司负责深空科研项目", "metadata": {"topic": "组织"}},
            {"text": "深空探索任务往往以年为单位计算", "metadata": {"topic": "常识"}},
        ]
        ids = await add_many(docs)
        print(f"[OK] 批量添加 {len(ids)} 条，当前条目数: {count()}")

        # top_k 控制返回条数上限
        _print_results(
            await search_async("Aliya 的船叫什么名字", top_k=2),
            title="\n检索「Aliya 的船叫什么名字」top_k=2:",
        )

        # threshold 控制相关度门槛：调高后只保留高度相关的条目
        _print_results(
            await search_async("Aliya 的船叫什么名字", threshold=0.9),
            title="\n同一查询提高阈值到 0.9（只保留高度相关）:",
        )

    except VectorError as e:
        get_default_handler().handle(e)


async def example_custom_embedding() -> None:
    """示例 3：直接构造 VectorStore（自定义 Embedding 配置）。

    不使用全局单例，显式传入 OpenAIEmbeddingProvider 与 VectorConfig，
    适合在单元测试、独立脚本中隔离使用。
    """
    print("\n=== 示例 3：自定义 VectorStore ===")

    try:
        emb_cfg = EmbeddingConfig(
            model="text-embedding-3-small",   # 替换为你的 embedding 模型
            url="http://localhost:11434",      # 替换为你的服务地址（Ollama 示例）
            api_key="",
            dimension=1536,                    # 可选：显式指定期望维度，与 API 返回不一致时报错
        )
        store = VectorStore(
            embedding=OpenAIEmbeddingProvider(emb_cfg),
            config=VectorConfig(top_k=3, similarity_threshold=0.0),
        )

        await store.add("海獭号正在穿越深空", metadata={"scene": "海獭号"})
        _print_results(
            await store.search_async("海獭号现在在哪"),
            title=f"\n检索结果（自定义 store，{store.count} 条）:",
        )

    except VectorError as e:
        get_default_handler().handle(e)


def example_sync_search() -> None:
    """示例 4：同步检索（独立脚本 / REPL 场景）。

    内部通过 asyncio.run 驱动异步路径，**不能**在已有运行中事件循环的
    async 上下文中调用（会抛 RuntimeError）；异步应用请使用 search_async。

    独立运行方式：``python -c "from core.vector.example import example_sync_search; example_sync_search()"``
    或在无事件循环的脚本中调用。
    """
    print("\n=== 示例 4：同步检索 ===")

    if not _check_embedding_ready():
        return

    try:
        _print_results(
            search("Aliya 喜欢什么", top_k=3),
            title="\n同步检索「Aliya 喜欢什么」（top_k=3）:",
        )
    except VectorError as e:
        get_default_handler().handle(e)


async def example_management() -> None:
    """示例 5：元数据、删除与清空等管理操作。"""
    print("\n=== 示例 5：管理操作 ===")

    if not _check_embedding_ready():
        return

    try:
        # 单例是进程级共享的，此处条目数包含此前示例写入的数据
        print(f"当前条目数（含此前示例写入）: {count()}")

        iid = await add("临时记录", metadata={"tmp": True})
        print(f"[OK] 新增一条临时记录: {iid[:8]}...")

        removed = delete(iid)
        print(f"删除该条目: {removed}，条目数: {count()}")

        clear()
        print(f"清空全部后条目数: {count()}")

        # 重置单例（主要用于测试或配置热重载）
        reset_vector_store()
        print("[OK] 单例已重置")

    except VectorError as e:
        get_default_handler().handle(e)


async def example_error_handling() -> None:
    """示例 6：配置缺失时的错误处理。

    未配置 embedding.model 时，get_vector_store() 会抛出 VectorConfigError
    （设计上不静默降级），示例演示如何捕获并给出引导。
    """
    print("\n=== 示例 6：错误处理 ===")

    cfg = get_vector_config()
    if cfg.embedding.model:
        print("[SKIP] 已配置 model，此示例无错误场景，跳过")
        return

    try:
        get_vector_store()
        print("[WARN] 意外：未配置 model 竟然成功了")
    except VectorConfigError as e:
        print(f"[OK] 按预期抛出 VectorConfigError: {e.message}")
        print("  处理建议：补全 embedding.model 后再调用 get_vector_store()")


async def main() -> None:
    """运行全部示例。"""
    print("Vector 向量模块演示")
    print("=" * 60)

    # 单例示例前先清理上次运行残留数据
    reset_vector_store()

    await example_global_store()        # 示例 1：全局单例添加与检索
    await example_batch_search()        # 示例 2：批量添加与检索参数
    # await example_custom_embedding()  # 示例 3：自定义 VectorStore（需真实 embedding 服务）
    # example_sync_search()             # 示例 4：同步检索（独立脚本运行，不能放入 async main）
    await example_management()          # 示例 5：管理操作
    await example_error_handling()      # 示例 6：错误处理

    print("\n" + "=" * 60)
    print("演示完成")
    print("  - 未配置 embedding 模型时，示例会打印配置引导并跳过真实 API 调用")


if __name__ == "__main__":
    setup_logger()   # 从 data/config/main.yml 加载日志配置
    asyncio.run(main())
