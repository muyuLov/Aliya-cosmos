# LLM 模块

多提供商对话服务，管理单个会话的消息历史、系统提示词注入与上下文缓存，内置指数退避重试与流式输出。

## 架构概览

```
__init__.py           ← 公共接口，工厂函数
service.py            ← ConversationService：消息历史管理、重试、流式
models.py             ← Message / ConversationContext / ChatRequest / ChatResponse / TokenUsage
cache.py              ← ContextCache：内存 LRU + TTL 会话缓存
cache_backend.py      ← CacheBackend / MemoryBackend
config_validator.py   ← ConfigValidator：配置字段合法性校验
exceptions.py         ← 结构化异常（LLM_001~LLM_003）

providers/
  base.py             ← LLMProvider 抽象基类
  openai_compatible.py ← OpenAICompatibleProvider（通用 OpenAI 兼容接口）
```

**调用链**：`ConversationService.asend()` → `_prepare_request()`（拼接 system + 历史） → `LLMProvider.async_chat_completion()` → `_commit_response()`（写回历史 + 更新缓存）。

---

## 核心接口

### `create_from_config()` — 从配置文件创建（推荐）

```python
from core.llm import create_from_config

# 自动读取 data/config/main.yml 的 cosmos.service.llm 段
async with create_from_config() as service:
    reply = await service.asend("你好，我是 COSMOS")
```

也可以在应用启动时传入系统提示词文件：

```python
service = create_from_config(
    system_prompt_file="agent/prompts/aliya_system_prompt.md",
)
```

---

### `ConversationService`

#### `asend()` — 异步对话（推荐）

```python
reply = await service.asend("Aliya，你现在感觉怎么样？")
```

- 自动维护消息历史（user/assistant 交替）
- 失败时指数退避重试，默认最多 3 次（间隔 1s / 2s / 4s）
- 重试时自动回滚失败的 user 消息，保持历史结构一致

#### `astream_send()` — 异步流式输出

```python
async for token in service.astream_send("讲讲深空探索的危险"):
    print(token, end="", flush=True)
```

流式内容缓冲至完整后才写入历史，重试不会向调用方 yield 半截内容。

#### `send()` — 同步调用（脚本/REPL）

```python
reply = service.send("你好")   # 内部驱动 asyncio.run()
```

不能在已有运行中事件循环的上下文中调用，async 函数内请用 `await asend()`。

---

### 上下文注入

每轮对话前调用，将工具描述、记忆上下文等追加到 system prompt 末尾，本轮结束后自动清除：

```python
await service.set_context_injection(
    skills="...",   # Skills 行为指令
    tools="...",    # 可用工具描述
    memory="...",   # GRAG 检索到的记忆上下文
)
reply = await service.asend(user_input)
# 注入内容在 asend() 返回后自动清除，不残留到下一轮
```

情绪补丁（由情感引擎调用）：

```python
await service.set_emotion_patch("## 当前情绪\n你现在很平静。")
```

---

### 历史管理

```python
history = await service.get_history()     # 获取消息历史副本
await service.clear_history()             # 清空历史，保留 system prompt
await service.append_message("user", "手动注入一条消息")

# 删除尾部带标记的临时注入消息（brain 推理循环用）
await service.discard_messages(content_marker="memory_result", max_count=1)
```

历史总字符数超过 `history_max_chars` 时，自动从头部移除最旧消息（至少保留 1 条）。

---

### Token 用量统计

```python
usage = await service.get_usage()
print(usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)
# DeepSeek 专有字段
print(usage.prompt_cache_hit_tokens, usage.reasoning_tokens)

await service.reset_usage()   # 重置累计统计
```

---

## 提供商

所有 OpenAI 兼容接口统一使用 `OpenAICompatibleProvider`，不再需要为每个服务实现独立的提供商类。

配置存放在 `data/config/LLMProviders.json`，通过 `providers.config_path` 引用，支持 `${ENV_VAR}` 环境变量语法。

标准配置字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `url` | string | **必须**。API 基础地址（如 `https://api.deepseek.com`） |
| `model` | string | **必须**。模型名称 |
| `api_key` | string | API 密钥（本地服务可用空字符串或占位符） |
| `timeout` | int | 请求超时秒数，默认 600 |
| `max_retries` | int | 最大重试次数，默认 3 |
| `http2` | bool | 是否启用 HTTP/2，默认 true。LM Studio 须设为 false |
| `provider_name` | string | 可选，用于日志标识，从配置文件 key 自动设置 |

```json
{
  "deepseek": {
    "url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "api_key": "${DEEPSEEK_API_KEY}",
    "timeout": 600
  },
  "ollama": {
    "url": "http://localhost:11434",
    "model": "qwen2.5:14b"
  },
  "lmstudio": {
    "url": "http://localhost:1234",
    "model": "deepseek-r1-distill-qwen-7b",
    "http2": false
  }
}
```

添加新提供商只需在 `LLMProviders.json` 中新增一个条目即可，无需修改代码。

---

## 配置

配置路径：`cosmos.service.llm`（`data/config/main.yml`）

```yaml
cosmos:
  service:
    llm:
      providers:
        name: deepseek                              # 当前使用的提供商
        config_path: data/config/LLMProviders.json # 提供商详细配置文件
      history_max_chars: 90000    # 历史总字符数阈值，超限时移除最旧消息
      system_prompt_file: agent/prompts/aliya_system_prompt.md  # 可选
```

`create_from_config()` 加载时会自动调用 `ConfigValidator` 对配置进行非严格校验（只记录警告，不中断）。

---

## 上下文缓存

`ContextCache` 以内存 LRU + TTL 方式存储 `ConversationContext`，默认参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `ttl` | 86400 秒（24h） | 条目存活时间，0 表示永不过期 |
| `max_size` | 500 | 最大条目数，超限时淘汰最久未使用的 |

同一个 `conversation_id` 的上下文在多次 `asend()` 间自动持久化，重启程序后丢失（内存存储）。

---

## 异常

所有异常继承 `LLMError`（`StructuredException` 子类），错误码前缀 `LLM_`。

| 异常 | 错误码 | 场景 |
|------|--------|------|
| `ProviderNotFoundError` | `LLM_001` | 提供商名称未注册，或配置文件缺失/格式错误 |
| `LLMRequestError` | `LLM_002` | API 请求失败（网络、超时、鉴权、限流） |
| `ContextCacheError` | `LLM_003` | 缓存读写发生意外错误 |

---

## 依赖关系

- 内部：`core.logger`、`core.config`、`core.exception`
- 外部：`openai`（所有基于 OpenAI 兼容接口的提供商均依赖此 SDK）、`pydantic`
