# Vector 向量模块

轻量级向量化与向量检索能力，不依赖外部向量数据库（无需 ChromaDB / FAISS），
提供文本向量化、内存向量存储与余弦相似度检索。

## 架构概览

```
__init__.py     ← 包入口，暴露便捷接口（add / search / search_async / ...）
embedding.py    ← 向量化提供者（OpenAI 兼容 Embedding API）
store.py        ← 内存向量存储与检索（余弦相似度）
config.py       ← 配置加载与校验（cosmos.service.vector）
exceptions.py   ← 异常定义（错误码 VEC_xxx）
```

**数据流**：`add(text)` → EmbeddingProvider 向量化 → 维度校验 → 存入内存；
`search(query)` → 向量化 query → 余弦相似度打分 → 阈值过滤 → top-k 排序返回。

> 注意：向量仅保存在进程内存中，进程退出即清空。如需持久化，
> 由上层负责（如定期导出或接入外部向量数据库）。

## 运行示例

```bash
python core/vector/example.py
```

示例覆盖全局单例添加/检索、批量添加与检索参数、自定义 VectorStore、
同步检索（独立脚本运行）、管理操作与错误处理。未配置 embedding 模型时
会自动打印配置引导并跳过真实 API 调用。

## 快速使用

```python
from core.vector import add, search, search_async, get_vector_store

# 异步添加
iid = await add("我喜欢喝咖啡，也爱熬夜写代码", metadata={"tag": "hobby"})

# 异步检索（推荐）
results = await search_async("我的爱好是什么", top_k=3)
for r in results:
    print(r.id, r.text, round(r.score, 4))

# 同步检索（须在无运行中事件循环的上下文使用）
results = search("我的爱好是什么")

# 获取单例（线程安全懒加载）
store = get_vector_store()
print(store.count, store.dimension)
```

## 向量化

模块固定使用 OpenAI 兼容 Embedding API（无 provider 切换）。

`model`、`url` 必须显式配置（LLM 的 chat 模型不能用于 embedding），
配置不完整时抛出 `VectorConfigError`，不会静默补齐或降级。
`api_key` 可留空——本地服务（Ollama/LM Studio 等）通常无真实密钥，
留空时使用占位符满足 OpenAI SDK 必填约束。

## 配置（data/config/main.yml）

```yaml
cosmos:
  service:
    vector:
      enabled: true
      similarity_threshold: 0.5        # 检索相似度阈值（0-1）
      top_k: 5                         # 默认返回条数
      embedding:
        model: ""                      # 必须配置 embedding 模型名
        url: ""                        # 必须配置服务地址
        api_key: ""                    # API 密钥（本地服务可留空，使用占位符）
        batch_size: 16                 # 批量向量化文本条数（1~128）
        dimension: 0                   # 期望向量维度（0=由 API 返回自动推断，非 0 时校验 API 返回是否一致）
```

`dimension` 为 0 时，向量维度由首次 API 返回自动推断；
配置为非 0 时，向量化结果维度与配置不一致会抛出 `DimensionMismatchError`（尽早暴露配置错误）。

配置变更后调用 `reload_config()` 重新加载，或依赖自动监听回调（需先调用 `init_config_listener()`）。

### 资源清理

向量库使用 OpenAI 兼容 API 客户端，应用退出或配置热重载前调用：

```python
import asyncio
from core.vector import shutdown_vector_store

asyncio.run(shutdown_vector_store())   # 关闭底层 API 客户端连接池并重置单例
```

## 异常

所有异常继承 `VectorError`（`StructuredException` 子类），错误码前缀 `VEC_`。

| 异常类                  | 错误码    | 场景                              |
| ----------------------- | --------- | --------------------------------- |
| `VectorConfigError`     | `VEC_001` | 配置校验失败                      |
| `VectorNotEnabledError` | `VEC_002` | 向量模块未启用                    |
| `EmbeddingError`        | `VEC_100` | 向量化基础异常                    |
| `EmbeddingAPIError`     | `VEC_101` | 调用外部 Embedding API 失败       |
| `DimensionMismatchError`| `VEC_102` | 向量维度不一致                    |
| `StoreError`            | `VEC_200` | 存储基础异常（如 ID 冲突、空白文本）|

## 依赖关系

- 内部：`core.config`（配置管理）、`core.logger`（日志）、`core.exception`（异常基类）
- 外部：`openai`（调用 Embedding API）
- 存储：纯内存，无第三方数据库依赖
