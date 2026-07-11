# Config 模块

从 YAML 文件加载配置，提供点路径读写、热重载、变更回调与 `${ENV_VAR}` 环境变量解析。

## 核心接口

### `get_config_instance()` — 获取单例

```python
from core.config import get_config_instance

# 首次调用：指定路径，创建单例
cfg = get_config_instance("data/config/main.yml")

# 后续调用：省略路径，返回同一实例
cfg = get_config_instance()
```

不同路径会维护各自独立的单例，同一路径始终返回同一实例。

---

### `get(path, default)` — 点路径读取

```python
# 读取嵌套配置值
level  = cfg.get("cosmos.logger.level")              # "info"
debug  = cfg.get("cosmos.logger.debug", False)       # 路径不存在时返回 default
name   = cfg.get("cosmos.service.llm.providers.name")

# 检查路径是否存在
"cosmos.logger.level" in cfg    # True
"cosmos.not.exist"    in cfg    # False
```

返回值中的 `${ENV_VAR}` 占位符会自动解析（见[环境变量](#环境变量解析)）。如需原始字符串，使用 `get_raw(path)`。

---

### `set(path, value)` — 点路径写入

```python
cfg.set("cosmos.logger.level", "debug")
cfg.set("cosmos.logger.debug", True)

# 中间层不存在时自动创建
cfg.set("cosmos.new.deep.key", "value")
```

写入后会触发已注册的变更回调。

---

### `reload()` — 热重载

```python
cfg.reload()  # 重新读取磁盘文件，覆盖所有运行时修改
```

热重载后触发 `path="__reload__"` 的全局回调，可用于通知各模块清除本地缓存。

---

### `register_callback()` — 变更回调

```python
def on_llm_change(path: str, value) -> None:
    print(f"LLM 配置变更: {path} = {value}")

# 监听指定路径前缀
cfg.register_callback("cosmos.service.llm", on_llm_change)

# 监听所有变更（path_pattern=None）
cfg.register_callback(None, lambda path, val: print(f"全局变更: {path}"))
```

`set()` 和 `reload()` 均会触发路径前缀匹配的回调，适合各模块做缓存失效。

---

### `get_all_fields()` — 扁平化导出

```python
for path, value in cfg.get_all_fields().items():
    print(f"{path}: {value}")
# cosmos.logger.level: info
# cosmos.logger.debug: false
# cosmos.service.llm.providers.name: deepseek
# ...
```

---

## 环境变量解析

`get()` 返回值时自动将 `${VAR}` 和 `${VAR:default}` 替换为对应的环境变量值。

```yaml
# data/config/LLMProviders.json / main.yml 中均可使用
api_key: ${DEEPSEEK_API_KEY}
api_url: ${ASTRA_API_URL:http://localhost:5000}  # 有默认值
```

```python
import os
os.environ["DEEPSEEK_API_KEY"] = "sk-xxxx"

cfg.get("cosmos.service.llm.api_key")  # → "sk-xxxx"
```

- 变量未设置且无默认值时抛出 `KeyError`
- 项目启动时自动从根目录 `.env` 文件加载（不覆盖已有系统环境变量，需安装 `python-dotenv`）
- 使用 `get_raw(path)` 跳过解析，获取原始占位符字符串

### 敏感字段脱敏

日志或调试输出时，使用 `mask_sensitive()` 避免打印明文密钥：

```python
from core.config import mask_sensitive

raw = {"api_key": "sk-abcdefgh1234", "level": "info"}
print(mask_sensitive(raw))
# {"api_key": "sk-a****1234", "level": "info"}
```

脱敏字段列表见 `SENSITIVE_KEYS`（包含 `api_key`、`password`、`token` 等）。

---

## 配置文件格式

标准嵌套 YAML，顶层必须为字典：

```yaml
cosmos:
  logger:
    level: info
    debug: false
  service:
    llm:
      providers:
        name: deepseek
        config_path: data/config/LLMProviders.json
```

点路径规则：层级之间用 `.` 分隔，如 `cosmos.service.llm.providers.name`。

---

## 依赖关系

- 内部：无（其他模块均依赖本模块）
- 外部：`pyyaml`（YAML 解析）、`python-dotenv`（可选，`.env` 自动加载）
