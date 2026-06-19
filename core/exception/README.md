# Exception 模块

负责提供统一的结构化异常处理框架。各业务模块在自己的 `exceptions.py` 中定义继承 `StructuredException` 的专属异常类，调用方直接 `raise`/`try-except`，不依赖装饰器。

## 核心接口

### `StructuredException`

结构化异常基类，所有业务异常应继承此类。

```python
from core.exception import StructuredException

class DatabaseError(StructuredException):
    def __init__(self, details: dict | None = None, cause: Exception | None = None):
        super().__init__(code="DB_001", message="数据库操作失败", details=details, cause=cause)
```

| 字段          | 类型                  | 说明                      |
| ------------- | --------------------- | ------------------------- |
| `code`        | `str`                 | 错误码，如 `"DB_001"`     |
| `message`     | `str`                 | 人类可读描述              |
| `details`     | `dict`                | 附加上下文信息            |
| `cause`       | `Exception \| None`   | 原始异常（异常链）        |
| `timestamp`   | `datetime`            | 异常实例化时间            |

- `to_dict()` — 序列化为字典，递归处理 `cause`，包含 traceback 与 timestamp
- `with_details(**kwargs)` — 链式追加 details，返回 self，便于 raise 时补充上下文

```python
raise DatabaseError().with_details(user_id=42, table="orders", op="SELECT")
```

---

### `ExceptionHandler`

异常处理器，支持按类型注册处理策略，匹配遵循 MRO 顺序。

```python
from core.exception import ExceptionHandler

# 链式注册
handler = (
    ExceptionHandler()
    .register(DatabaseError, lambda e: alert(e.code))
    .register(ValidationError, lambda e: log(e.message))
)

# 装饰器形式注册
@handler.on(DatabaseError, suppress_default=True)
def handle_db(exc: DatabaseError) -> None:
    send_alert(exc.code)  # suppress_default=True 时不再走默认日志

# propagate 责任链：子类处理完后继续触发父类处理函数
handler.register(StructuredException, lambda e: report(e.code))
handler.register(DatabaseError, lambda e: alert(e), propagate=True)
# handle(DatabaseError) 依次执行：alert → report
```

- `register(exc_type, handler_func, *, suppress_default=False, propagate=False)` — 注册处理函数，返回 `self` 支持链式调用
- `on(exc_type, *, suppress_default=False, propagate=False)` — 装饰器形式注册，等价于 `register()`
- `unregister(exc_type)` — 移除指定类型的所有处理函数
- `clear()` — 清空所有注册函数，常用于测试环境重置
- `handle(exception)` — 分发处理；`suppress_default=False`（默认）时自定义函数与默认日志均执行，`True` 时跳过默认日志
- `__repr__` — 输出已注册类型列表，如 `ExceptionHandler(registered=[DatabaseError, ValidationError])`

---

### `catch_context` 上下文管理器

代码块边界的统一兜底捕获，**业务函数内部推荐直接 `try/except`**，此管理器用于最外层边界。

```python
from core.exception import catch_context

with catch_context(exc_types=(ValidationError,), re_raise=False):
    parse_input(raw)
```

| 参数        | 类型                          | 默认值         | 说明                         |
| ----------- | ----------------------------- | -------------- | ---------------------------- |
| `handler`   | `ExceptionHandler \| None`    | `None`         | 指定处理器                   |
| `re_raise`  | `bool`                        | `False`        | 处理后是否重新抛出           |
| `exc_types` | `tuple[type[Exception], ...]` | `(Exception,)` | 要捕获的异常类型，默认全捕获 |

---

### 全局默认处理器

```python
from core.exception import get_default_handler, set_default_handler

# 获取全局处理器并注册策略
get_default_handler().register(ValueError, lambda e: ...)

# 替换全局处理器
set_default_handler(my_handler)
```

## 使用示例

```python
# mymodule/exceptions.py
from core.exception import StructuredException

class MyModuleError(StructuredException):
    """模块基类"""

class SpecificError(MyModuleError):
    def __init__(self, reason: str, cause: Exception | None = None):
        super().__init__(code="MOD_001", message=f"操作失败: {reason}", cause=cause)

# mymodule/service.py
from mymodule.exceptions import SpecificError

def do_work() -> dict:
    try:
        ...
    except OSError as e:
        raise SpecificError("IO 错误", cause=e) from e

# 调用方
from core.exception import ExceptionHandler
from mymodule.exceptions import SpecificError

handler = ExceptionHandler()

try:
    do_work()
except SpecificError as e:
    handler.handle(e)
```

## 依赖关系

- 内部模块：`core.logger`（用于默认日志记录）
- 外部库：无（仅使用 Python 标准库）

## 配置说明

本模块无需额外配置，日志输出依赖 `core.logger` 的初始化状态。
若 `core.logger` 未初始化，将自动以默认配置（INFO 级别，控制台输出）启动。
