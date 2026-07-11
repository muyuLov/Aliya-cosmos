# Logger 模块

统一的日志管理系统，支持控制台彩色输出与异步文件轮转，提供模块级 Logger 获取和运行时动态配置。

## 核心接口

### `get_logger(name)` — 获取 Logger

```python
from core.logger import get_logger

logger = get_logger(__name__)

logger.debug("五元组提取开始")
logger.info("成功存储 %d 个五元组", 5)
logger.warning("Neo4j 连接冷却中，剩余 %.0fs", 30.0)
logger.error("LLM 请求失败: %s", error, exc_info=True)
```

若全局管理器尚未初始化，自动以默认配置（INFO 级别，仅控制台）启动。通常不需要手动调用 `setup()`。

---

### `setup(config)` — 初始化日志管理器

应在应用启动时调用一次，支持三种方式：

```python
from core.logger import setup

# 方式 1：自动从 data/config/main.yml 的 cosmos.logger 段加载（推荐）
setup()

# 方式 2：指定配置文件路径
setup("data/config/main.yml")

# 方式 3：直接传入配置字典
setup({
    "level": "debug",
    "debug": True,
    "console": {"enabled": True, "color": True},
    "file": {"enabled": False},
})
```

---

### `get_manager()` — 获取 LogManager

```python
from core.logger import get_manager

mgr = get_manager()

# 运行时动态调整级别
mgr.set_global_level("warning")

# 快速切换 debug 模式（同时控制 httpx/httpcore 等三方库日志）
mgr.set_debug_mode(True)

# 热重载配置
mgr.reload_config(new_config_dict)

# 应用退出前优雅关闭，确保队列中的日志全部落盘
mgr.shutdown()
```

---

## 输出格式

### 默认（彩色结构化）

```
2026-07-11 14:32:01 | MainThread           | INFO    | --> 成功存储 5 个五元组到 Neo4j
2026-07-11 14:32:02 | MainThread           | WARNING | --> Neo4j 连接失败，进入冷却期
2026-07-11 14:32:03 | MainThread           | ERROR   | --> LLM 请求失败: timeout
```

各字段固定宽度对齐：`时间戳 | 线程名(20字符) | 级别(8字符) | --> 消息`

### JSON 结构化（`structured: true`）

```json
{"timestamp": "2026-07-11T14:32:01Z", "thread": "MainThread", "level": "INFO", "logger": "core.memory.graph", "message": "成功存储 5 个五元组到 Neo4j"}
```

适合日志采集平台（ELK、Loki 等）。通过 `extra=` 传入的自定义字段会自动附加到 JSON 输出。

---

## 配置说明

配置路径：`cosmos.logger`（`data/config/main.yml`）

```yaml
cosmos:
  logger:
    level: info          # 日志级别：debug / info / warning / error / critical
    debug: false         # debug 模式：true 时强制 DEBUG 级别，并开放三方库日志
    structured: false    # false = 彩色结构化；true = JSON

    console:
      enabled: true      # 是否输出到控制台
      color: true        # 是否启用 ANSI 彩色

    file:
      enabled: true                  # 是否输出到文件
      path: data/logs/cosmos.log     # 日志文件路径（父目录自动创建）
      rotate: session                # 轮转策略（见下表）
      when: midnight                 # timed 模式轮转周期
      backup_count: 30               # 保留历史文件数
      max_bytes: 10485760            # sized 模式单文件上限（10MB）
      buffer_size: 100               # 批量写入缓冲区大小（条数）
      flush_interval: 5.0            # 自动刷新间隔（秒）
      max_queue_size: 10000          # 异步队列容量上限
```

### 文件轮转策略

| `rotate` 值 | 说明 | 文件命名示例 |
|-------------|------|-------------|
| `session`（默认）| 每次启动创建新文件，文件名附带时间戳 | `cosmos_20260711_143201_123456.log` |
| `timed` | 按时间轮转，由 `when` 控制周期 | `cosmos.log.2026-07-10` |
| `sized` | 按文件大小轮转，由 `max_bytes` 控制 | `cosmos.log.1` |

---

## 异步文件写入

文件输出采用三层异步架构，日志调用不阻塞业务线程：

```
业务代码 → MonitoredQueueHandler（有界队列）
               ↓ 后台 QueueListener 消费
          BufferedFileHandler（内存缓冲批量写入）
               ↓
          底层文件 Handler（RotatingFileHandler / TimedRotatingFileHandler）
```

- **MonitoredQueueHandler**：队列容量由 `max_queue_size` 控制，使用率超过 80% 时输出告警，队列满时丢弃最旧记录
- **BufferedFileHandler**：积累到 `buffer_size` 条或超过 `flush_interval` 秒后批量写入，写入失败自动降级到 stderr
- 应用退出前调用 `mgr.shutdown()` 确保队列中的日志全部落盘

---

## 依赖关系

- 内部：`core.config`（从 YAML 加载配置）
- 外部：仅 Python 标准库（`logging`、`queue`、`threading`）
