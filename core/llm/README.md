# LLM 模块

提供统一的 LLM 服务框架，支持多提供商切换与智能上下文管理。

## 核心特性

- **智能上下文管理**：基于消息数量控制，自动清理超长历史
- **多提供商支持**：统一接口，支持 DeepSeek、Ollama、LM Studio
- **缓存机制**：内存缓存，支持TTL过期和LRU淘汰
- **异步支持**：流式生成，异步调用，线程安全

## 快速开始

### 基础用法

```python
from core.llm import create_from_config

# 从配置文件自动创建服务
service = create_from_config(
    config_path="data/config/main.yml",
    system_prompt="你是一个有用的助手"
)

# 发送消息
reply = service.send("你好！")
print(reply)
```

### 多提供商配置

```python
from core.llm import create_service

# DeepSeek 云端模型
service = create_service(
    provider_name="deepseek",
    provider_config={
        "api_key": "your_api_key",
        "model": "deepseek-v4-flash"
    }
)

# Ollama 本地模型
service = create_service(
    provider_name="ollama", 
    provider_config={
        "url": "http://127.0.0.1:11434",
        "model": "qwen2.5:7b"
    }
)
```

### 异步流式对话

```python
import asyncio

async def chat_stream():
    service = create_from_config()
    
    # 流式生成
    async for token in service.astream_send("讲个故事"):
        print(token, end="", flush=True)
    
    # 异步发送
    reply = await service.asend("总结一下", max_retries=3)
    print(reply)

asyncio.run(chat_stream())
```

## 核心接口

- `create_from_config(config_path, ...)` → `ConversationService` - 从配置文件自动创建服务
- `create_from_config(config_path, ...)` → `ConversationService` - 从配置文件创建服务（推荐）
- `create_service(provider_name, provider_config, ...)` → `ConversationService` - 手动创建服务
- `service.send(user_input)` → `str` - 同步发送消息（无事件循环时使用）
- `service.asend(user_input, store_history=True)` → `str` - 异步发送消息
- `service.astream_send(user_input, store_history=True)` → `AsyncGenerator[str, None]` - 异步流式发送
- `service.get_history()` → `list[Message]` - 获取消息历史
- `service.clear_history()` → `None` - 清空历史，保留系统提示词

## 配置说明

在 `data/config/main.yml` 的 `cosmos.service.llm` 节点下配置：

```yaml
cosmos:
  service:
    llm:
      providers:
        name: lmstudio                              # 使用的提供商名称
        config_path: data/config/LLMProviders.json  # 提供商详细配置文件路径
      max_tokens: 4000                              # 触发历史清理的消息数阈值
      system_prompt_file: agent/prompts/aliya_system_prompt.md
```

提供商详细配置在 `data/config/LLMProviders.json` 中定义：

```json
{
  "lmstudio": {
    "url": "http://127.0.0.1:1234",
    "api_key": "lm-studio",
    "model": "your_model_name",
    "timeout": 600
  },
  "deepseek": {
    "url": "https://api.deepseek.com",
    "api_key": "sk-xxxxxxxxxxxxxxxx",
    "model": "deepseek-chat",
    "timeout": 600
  },
  "ollama": {
    "url": "http://127.0.0.1:11434",
    "model": "qwen2.5:7b",
    "timeout": 600
  }
}
```

### 补丁机制

支持临时注入情感和上下文补丁：

```python
# 一次性补丁（发送后自动清除）
service.set_emotion_patch("用户情绪：开心")
service.set_context_injection(memory="用户偏好：简洁回答")
reply = service.send("你好")  # 补丁在此次对话后清除
```

## 依赖与异常

### 依赖关系

**内部模块**：`core.config`、`core.exception`、`core.logger`

**外部库**：
- 必需：`pydantic>=2.0.0`、`openai>=1.0.0`、`httpx>=0.20.0`

### 异常处理

```python
from core.llm.exceptions import LLMRequestError, ProviderNotFoundError

try:
    service = create_from_config()
    reply = service.send("你好")
except LLMRequestError as e:
    print(f"请求失败: {e.message}")
except ProviderNotFoundError as e:
    print(f"提供商未找到: {e.message}")
```

## 常见问题

1. **内存泄漏** - 定期调用 `cache.evict_expired()` 清理过期缓存
2. **并发问题** - 使用 `create_from_config()` 内置线程安全机制
