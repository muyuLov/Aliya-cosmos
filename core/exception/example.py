"""异常模块使用示例——结构化异常模式"""

from __future__ import annotations

from core.exception import (
    ExceptionHandler,
    StructuredException,
    catch_context,
    get_default_handler,
)

# ── 1. 在模块 exceptions.py 中定义业务异常（此处内联演示）────────────────────


class DatabaseError(StructuredException):
    """数据库操作异常"""

    def __init__(
        self,
        details: dict | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code="DB_001",
            message="数据库操作失败",
            details=details or {},
            cause=cause,
        )


class ValidationError(StructuredException):
    """输入校验异常"""

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(
            code="VAL_001",
            message=f"字段 '{field}' 校验失败：{reason}",
            details={"field": field, "reason": reason},
        )


# ── 2. 注册处理器（可选，不注册则走默认日志）────────────────────────────────

handler = ExceptionHandler()
handler.register(ValidationError, lambda e: print(f"[校验] {e.message}"))  # type: ignore[attr-defined]


@handler.on(DatabaseError, suppress_default=True)  # type: ignore[arg-type]
def handle_db(exc: DatabaseError) -> None:
    """接管数据库异常的日志输出"""
    print(f"[DB告警] {exc.code} @ {exc.timestamp.isoformat()}")


# ── 3. 业务函数：直接 try/except 结构化异常 ──────────────────────────────────


def fetch_user(user_id: int) -> dict:
    """
    查询用户，演示标准结构化异常用法。

    Raises:
        DatabaseError: 数据库操作失败时抛出。
    """
    try:
        # 模拟数据库操作失败
        raise ConnectionRefusedError("连接被拒绝")
    except ConnectionRefusedError as e:
        raise DatabaseError({"user_id": user_id}, cause=e) from e


def parse_item(item_id: int) -> dict:
    """
    解析条目，演示 with_details 链式追加上下文。

    Raises:
        ValidationError: 参数非法时抛出。
    """
    if item_id <= 0:
        raise ValidationError("item_id", "必须大于 0").with_details(received=item_id)
    return {"id": item_id}


async def async_fetch(item_id: int) -> dict:
    """
    异步查询，演示异步函数中的结构化异常。

    Raises:
        ValidationError: 参数非法时抛出。
    """
    if item_id <= 0:
        raise ValidationError("item_id", "必须大于 0")
    return {"id": item_id}


# ── 4. propagate 责任链示例 ──────────────────────────────────────────────────

chain_handler = ExceptionHandler()
chain_handler.register(StructuredException, lambda e: print(f"[通用上报] code={e.code}"))  # type: ignore[attr-defined]
chain_handler.register(
    DatabaseError,
    lambda e: print(f"[DB专属] 触发告警: {e.message}"),  # type: ignore[attr-defined]
    propagate=True,
)
# handle(DatabaseError) 依次执行：[DB专属] → [通用上报]


# ── 5. catch_context：代码块边界的统一捕获 ───────────────────────────────────


def process_batch(items: list[dict]) -> None:
    """
    批量处理，演示 catch_context 在代码块边界的用法。
    业务逻辑内部仍用 try/except，catch_context 用于最外层兜底。
    """
    with catch_context(handler=handler, re_raise=False, exc_types=(ValidationError,)):
        for item in items:
            if not item.get("id"):
                raise ValidationError("id", "不能为空")
        print("批量处理完成:", items)


# ── 6. 运行示例 ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    print("=== 结构化异常 + try/except ===")
    try:
        fetch_user(42)
    except DatabaseError as e:
        handler.handle(e)
        print("to_dict:", json.dumps(e.to_dict(), ensure_ascii=False, indent=2))

    print("\n=== with_details 链式追加上下文 ===")
    try:
        parse_item(-1)
    except ValidationError as e:
        handler.handle(e)
        print("details:", e.details)

    print("\n=== propagate 责任链 ===")
    chain_handler.handle(DatabaseError({"host": "db-01"}))

    print("\n=== catch_context 代码块边界兜底 ===")
    process_batch([{"id": "a"}, {}])

    print("\n=== ExceptionHandler.__repr__ ===")
    print(repr(handler))

    print("\n=== 全局默认处理器 ===")
    get_default_handler().handle(RuntimeError("未预期的运行时错误"))

    print("\n=== clear() 重置处理器 ===")
    handler.clear()
    print(repr(handler))
