from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from contextvars import ContextVar
from enum import Enum, auto
from typing import Any

from agent.models import ToolProgress


# contextvar 用于安全地在并发工具调用间传递进度回调，避免实例属性竞态
_on_progress_var: ContextVar[Callable[[ToolProgress], None] | None] = ContextVar("_on_progress", default=None)


def get_progress_callback() -> Callable[[ToolProgress], None] | None:
    """获取当前工具调用的进度回调。在 ToolRegistry.dispatch 中设置。"""
    return _on_progress_var.get()


class ToolCategory(Enum):
    CORE = auto()       # 核心工具，始终展示给 LLM（默认）
    INTERNAL = auto()   # 内部工具，不展示给 LLM（如 MemoryQueryTool）
    AGENT_ONLY = auto() # 仅在 agent 子上下文中展示
    HIDDEN = auto()     # 隐藏，不注入提示


class BaseTool(ABC):
    name: str = ""
    description: str = ""
    input_schema: dict = {}  # JSON Schema 格式的参数描述
    concurrency_safe: bool = True  # True=只读可并行，False=修改操作须串行
    category: ToolCategory = ToolCategory.CORE  # 工具分类，用于上下文剪裁

    @abstractmethod
    async def run(self, arguments: dict[str, Any]) -> Any:
        raise NotImplementedError

    def format_signature(self) -> str:
        """生成包含参数描述的完整工具签名（供 LLM 上下文注入使用）。"""
        parts = [f"- **{self.name}**: {self.description}"]
        if self.input_schema:
            parts.append("  参数:")
            for pname, schema in self.input_schema.items():
                ptype = schema.get("type", "any")
                pdesc = schema.get("description", "")
                default = schema.get("default")
                suffix = f"（默认 {default}）" if default is not None else ""
                parts.append(f"    - {pname} ({ptype}): {pdesc}{suffix}")
        return "\n".join(parts)

    def validate_args(self, arguments: dict[str, Any]) -> str | None:
        """校验已传参数的类型是否符合 input_schema（不要求必填），返回错误信息或 None。"""
        if not self.input_schema:
            return None
        for name, schema in self.input_schema.items():
            if name not in arguments:
                continue
            value = arguments[name]
            expected_type: str = schema.get("type", "").lower()
            if expected_type and not self._type_matches(value, expected_type):
                return f"工具 '{self.name}' 参数 {name} 应为 {expected_type}，实际为 {type(value).__name__}"
            if expected_type == "array":
                items_type = schema.get("items", {}).get("type", "")
                if items_type:
                    for idx, item in enumerate(value):
                        if not self._type_matches(item, items_type):
                            return f"工具 '{self.name}' 参数 {name}[{idx}] 应为 {items_type}，实际为 {type(item).__name__}"
            if expected_type == "object":
                for prop_name, prop_schema in schema.get("properties", {}).items():
                    if prop_name not in value:
                        continue
                    ptype = prop_schema.get("type", "")
                    if ptype and not self._type_matches(value[prop_name], ptype):
                        return f"工具 '{self.name}' 参数 {name}.{prop_name} 应为 {ptype}，实际为 {type(value[prop_name]).__name__}"
            if "enum" in schema:
                allowed = schema["enum"]
                if value not in allowed:
                    return f"工具 '{self.name}' 参数 {name} 必须为 {allowed} 之一，实际为 {value}"
        return None

    @staticmethod
    def _type_matches(value: Any, expected_type: str) -> bool:
        """检查值的类型是否匹配期望类型。"""
        if not expected_type:
            return True
        if expected_type == "string":
            return isinstance(value, str)
        if expected_type in ("integer", "number"):
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected_type == "boolean":
            return isinstance(value, bool)
        if expected_type == "array":
            return isinstance(value, list)
        if expected_type == "object":
            return isinstance(value, dict)
        return True

    def is_concurrency_safe(self, arguments: dict[str, Any]) -> bool:
        """根据参数判断该次调用是否可以安全并发执行。

        默认委托给类属性 concurrency_safe，子类可覆写以实现基于参数的动态判断。
        """
        return self.concurrency_safe

    def validate_input(self, arguments: dict[str, Any]) -> str | None:
        """语义校验：检查参数的业务规则（如路径是否在允许范围内）。

        返回 None 表示通过，返回字符串表示拒绝原因。
        """
        return None

    def check_permissions(self, arguments: dict[str, Any]) -> str | None:
        """权限检查：判断是否允许执行此工具调用。

        返回 None 表示允许，返回字符串表示拒绝原因和错误码 PERMISSION_DENIED。
        """
        return None

    async def prompt(self, context: dict[str, Any] | None = None) -> str:
        """生成工具的系统提示文档。

        子类可覆写以提供更丰富的文档（用法示例、边界条件处理）。
        默认实现委托给 format_signature() 以保持向后兼容。
        """
        return self.format_signature()


class InternalTool(BaseTool):
    """
    内部工具基类：执行结果通过 append_message 注入对话历史，
    让 LLM 基于结果继续推理，而非走外部 dispatch。
    子类需定义 message_prefix，用于 injected 消息清理。

    注意：InternalTool 的 run() 方法返回 ToolResult，
    而 BaseTool 的子类通常返回 dict。
    """
    message_prefix: str = ""

    async def execute_and_format(self, arguments: dict[str, Any]) -> str:
        """执行工具并返回格式化文本，该文本将作为 assistant 消息注入对话。"""
        raise NotImplementedError
