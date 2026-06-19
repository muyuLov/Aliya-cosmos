# config 模块

负责从 YAML 文件加载配置，提供基于点路径的字段读写和配置热切换能力。

## 核心接口

- `ConfigManager(config_path)` — 初始化并加载配置文件
- `config.get(path, default)` — 点路径读取，如 `"cosmos.logger.level"`
- `config.set(path, value)` — 点路径写入，自动创建缺失的中间层
- `config.reload()` — 重新加载文件，覆盖运行时修改
- `config.get_all_fields()` — 返回扁平化键值对字典

### 使用示例

```python
from core.config import ConfigManager

config = ConfigManager("data/config/main.yml")

# 读取
level = config.get("cosmos.logger.level")          # "info"
model = config.get("cosmos.service.llm.default.ollama.model")
missing = config.get("cosmos.not.exist", default="默认值")

# 写入
config.set("cosmos.logger.level", "debug")
config.set("cosmos.new.key", 42)  # 自动创建中间路径

# 导出所有字段
for path, value in config.get_all_fields().items():
    print(f"{path}: {value}")

# 重载文件（覆盖所有运行时修改）
config.reload()
```

## 依赖关系

- 外部库：`pyyaml`

## 配置说明

配置文件位于 `data/config/` 目录，支持任意嵌套的 YAML 结构，无需预定义模型。
