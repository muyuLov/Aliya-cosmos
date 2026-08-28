"""异常模块使用示例——基于 Aliya-cosmos 项目的真实业务场景"""

from __future__ import annotations

from core.exception import (
    ExceptionHandler,
    StructuredException,
    catch_context,
    get_default_handler,
)

# ── 1. 直接使用项目中已定义的业务异常 ────────────────────────────────────────
#
#   各模块已在自己的 exceptions.py 中定义好，无需重复定义：
#     core/llm/exceptions.py   → LLMError / LLMRequestError / ProviderNotFoundError ...

from core.llm.exceptions import LLMRequestError, ProviderNotFoundError

# ── 2. 自定义异常（演示用途）──────────────────────────────────────────────────

class ServiceConnectionError(StructuredException):
    """服务连接失败（示例异常，替代已移除的 GRAG 异常）"""

    def __init__(self, message: str, *, code: str = "SERVICE_CONN_ERR") -> None:
        super().__init__(message, code=code)


class ServiceConfigError(StructuredException):
    """服务配置错误（示例异常，替代已移除的 GRAG 异常）"""

    def __init__(self, message: str, *, code: str = "SERVICE_CFG_ERR") -> None:
        super().__init__(message, code=code)


# ── 3. 注册处理器 ─────────────────────────────────────────────────────────────

handler = ExceptionHandler()

# LLM 提供商未找到：打印简短提示即可，不需要默认日志
handler.register(
    ProviderNotFoundError,
    lambda e: print(f"[LLM] 未找到提供商: {e.details.get('provider')}"),
    suppress_default=True,
)

# LLM 请求失败：自定义告警 + 保留默认日志（suppress_default=False）
handler.register(
    LLMRequestError,
    lambda e: print(f"[LLM告警] {e.code} provider={e.details.get('provider')} → {e.details.get('reason')}"),
)

# 服务连接失败：接管日志，附带时间戳
@handler.on(ServiceConnectionError, suppress_default=True)
def handle_conn(exc: ServiceConnectionError) -> None:
    print(f"[服务] 连接失败 {exc.code} @ {exc.timestamp.isoformat()} — {exc.message}")


# ── 4. 业务函数：try/except 结构化异常 ───────────────────────────────────────


def call_llm_provider(provider_name: str) -> str:
    """
    调用 LLM 提供商，演示 ProviderNotFoundError 的标准抛出方式。

    Raises:
        ProviderNotFoundError: 提供商名称未在 registry 中注册。
    """
    registered = {"ollama", "lmstudio", "deepseek"}
    if provider_name not in registered:
        raise ProviderNotFoundError(provider_name)
    return f"[{provider_name}] 响应内容..."


def send_llm_request(provider: str, prompt: str) -> str:
    """
    发送 LLM 请求，演示 LLMRequestError 包装底层网络异常。

    Raises:
        LLMRequestError: 底层请求失败时抛出，原始异常保存在 cause。
    """
    try:
        # 模拟网络超时
        raise TimeoutError("连接超时 (30s)")
    except TimeoutError as e:
        raise LLMRequestError(provider, reason="请求超时", cause=e) from e


def connect_service(uri: str) -> None:
    """
    连接服务，演示 ServiceConnectionError 包装底层连接错误。

    Raises:
        ServiceConnectionError: 数据库不可达时抛出。
    """
    try:
        raise ConnectionRefusedError(f"无法连接到 {uri}")
    except ConnectionRefusedError as e:
        raise ServiceConnectionError(
            message=f"服务不可达: {uri}"
        ) from e


def validate_config(setting: str | None) -> None:
    """
    校验配置，演示 with_details 链式追加上下文。

    Raises:
        ServiceConfigError: 配置未设置时抛出。
    """
    if not setting or not setting.strip():
        raise ServiceConfigError(
            "必须配置服务参数"
        ).with_details(config_key="cosmos.service.config.setting")


# ── 5. propagate 责任链：ServiceConnectionError 子类先处理，再上报到基类 ────

chain_handler = ExceptionHandler()

# 父类：通用上报
chain_handler.register(
    ServiceConnectionError,
    lambda e: print(f"[服务通用上报] code={e.code} message={e.message}"),
)

# 子类：专属处理 + propagate=True 继续触发父类
chain_handler.register(
    ServiceConnectionError,
    lambda e: print(f"[服务专属] 触发重连逻辑: {e.message}"),
    propagate=True,
)


# ── 6. catch_context：对话轮次批量处理的兜底捕获 ─────────────────────────────


def process_conversation_batch(turns: list[dict]) -> None:
    """
    批量处理对话轮次，演示 catch_context 在代码块边界的用法。
    业务逻辑内部仍用 try/except，catch_context 用于最外层兜底。

    每条 turn 需包含 user / ai 字段，缺失时抛出 ServiceConfigError。
    """
    with catch_context(handler=handler, re_raise=False, exc_types=(ServiceConfigError,)):
        for i, turn in enumerate(turns):
            if not turn.get("user") or not turn.get("ai"):
                raise ServiceConfigError(
                    f"第 {i} 条对话缺少必要字段"
                ).with_details(turn_index=i, turn=turn)
        print(f"批量处理完成，共 {len(turns)} 条对话")


# ── 7. 运行示例 ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    print("=== LLM 提供商未找到（suppress_default=True）===")
    try:
        call_llm_provider("openai")
    except ProviderNotFoundError as e:
        handler.handle(e)

    print("\n=== LLM 请求失败 + 异常链（cause 保留）===")
    try:
        send_llm_request("ollama", "你好")
    except LLMRequestError as e:
        handler.handle(e)
        print("cause:", type(e.cause).__name__, "→", e.cause)

    print("\n=== 服务连接失败 + to_dict 序列化 ===")
    try:
        connect_service("bolt://localhost:7687")
    except ServiceConnectionError as e:
        handler.handle(e)
        print("to_dict:", json.dumps(e.to_dict(), ensure_ascii=False, indent=2))

    print("\n=== 配置校验 + with_details ===")
    try:
        validate_config(None)
    except ServiceConfigError as e:
        print(f"[配置错误] {e.code}: {e.message}")
        print("details:", e.details)

    print("\n=== catch_context 批量对话兜底 ===")
    process_conversation_batch([
        {"user": "你好", "ai": "你好！"},
        {"user": "今天天气如何", "ai": ""},   # ai 字段为空，触发 ServiceConfigError
    ])

    print("\n=== ExceptionHandler.__repr__ ===")
    print(repr(handler))

    print("\n=== 全局默认处理器兜底未知异常 ===")
    get_default_handler().handle(RuntimeError("未预期的运行时错误"))

    print("\n=== clear() 重置处理器 ===")
    handler.clear()
    print(repr(handler))
