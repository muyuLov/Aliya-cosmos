# Logger 模块

负责全局日志管理，提供结构化彩色终端输出、异步文件轮转输出，以及运行时动态级别控制。

## 核心接口

- `setup(config: dict | None) -> LogManager`  
  应用启动时调用一次，初始化全局日志管理器。

- `get_logger(name: str) -> logging.Logger`  
  获取指定名称的 Logger，未初始化时自动以默认配置启动。

- `get_manager() -> LogManager`  
  获取全局 LogManager 实例，用于动态调整级别或重载配置。

### 使用示例

```python
from core.logger import setup, get_logger, get_manager

# 启动时初始化（通常在 main.py 中）
setup({
    "level": "info",
    "debug": False,
    "console": {"enabled": True, "color": True},
    "file": {
        "enabled": True,
        "path": "data/log/app.log",
        "rotate": "timed",      # 按天轮转（默认）
        "when": "midnight",
        "backup_count": 30,
    },
})

logger = get_logger(__name__)
logger.info("服务启动")
logger.debug("调试信息", extra={"user": "admin"})

# 应用退出前调用，确保异步队列日志全部落盘
get_manager().shutdown()
```

### 与 ConfigManager 集成

```python
from core.config import get_config_instance
from core.logger import setup

cfg = get_config_instance("data/config/main.yml")

setup({
    "level": cfg.get("cosmos.logger.level", "info"),
    "debug": cfg.get("cosmos.logger.debug", False),
    "console": {"enabled": True, "color": True},
    "file": {
        "enabled": True,
        "path": cfg.get("cosmos.logger.file_url", "data/log/cosmos.log"),
        "rotate": "timed",
        "backup_count": 30,
    },
})
```

### 动态控制

```python
from core.logger import get_manager

mgr = get_manager()
mgr.set_debug_mode(True)        # 切换到 DEBUG 级别
mgr.set_global_level("warning") # 动态调整级别
mgr.reload_config({...})        # 热重载配置
mgr.shutdown()                  # 优雅关闭，等待异步队列落盘
```

## 日志输出格式

控制台（彩色）：

```
2026-04-06 12:00:00 | MainThread           | INFO    | --> 服务启动
2026-04-06 12:00:00 | MainThread           | WARNING | --> 磁盘空间不足
2026-04-06 12:00:00 | MainThread           | ERROR   | --> 连接超时
2026-04-06 12:00:00 | MainThread           | CRITICAL| --> 系统崩溃
```

各部分颜色：

| 部分       | 颜色                     |
| ---------- | ------------------------ |
| 时间戳     | 暗白                     |
| 线程名     | 青色                     |
| `\|` / `-->` | 暗色                   |
| DEBUG      | 青色 / 暗白消息          |
| INFO       | 粗体绿色 / 白色消息      |
| WARNING    | 粗体黄色 / 黄色消息      |
| ERROR      | 粗体红色 / 红色消息      |
| CRITICAL   | 粗体白字红底 / 粗体红色消息 |

文件输出（无色，格式相同）：

```
2026-04-06 12:00:00 | MainThread           | INFO    | --> 服务启动
```

JSON 模式（`structured: True`）：

```json
{"timestamp": "2026-04-06T12:00:00Z", "thread": "MainThread", "level": "INFO", "logger": "myapp", "message": "服务启动", "user_id": 42}
```

## 文件轮转模式

| `rotate` 值 | 说明                                      | 关键参数                          |
| ----------- | ----------------------------------------- | --------------------------------- |
| `"timed"`   | 按时间轮转（默认），历史文件追加日期后缀  | `when`（默认 `midnight`）、`backup_count` |
| `"sized"`   | 按文件大小轮转                            | `max_bytes`（默认 10 MB）、`backup_count` |

文件写入采用 `QueueHandler + QueueListener` 异步机制，日志调用不阻塞业务线程。  
应用退出前须调用 `shutdown()` 确保队列中的日志全部落盘。

## 依赖关系

- 内部模块：`core.config`（可选，用于从 YAML 读取配置）
- 外部库：仅使用 Python 3.12 标准库（`logging`、`json`、`pathlib`、`queue`）

## 配置说明

| 配置键（YAML 路径）         | 描述                | 默认值                |
| --------------------------- | ------------------- | --------------------- |
| `cosmos.logger.level`      | 日志级别            | `info`                |
| `cosmos.logger.debug`      | 是否开启 debug 模式 | `false`               |
| `cosmos.logger.file_url`   | 日志文件路径        | `data/log/cosmos.log` |
