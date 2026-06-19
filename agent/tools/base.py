from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    name: str = ""
    description: str = ""
    input_schema: dict = {}  # JSON Schema 格式的参数描述，如 {"query": {"type": "string", "description": "..."}}

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
            if expected_type == "string" and not isinstance(value, str):
                return f"工具 '{self.name}' 参数 {name} 应为 {expected_type}，实际为 {type(value).__name__}"
            if expected_type in ("integer", "number"):
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    return f"工具 '{self.name}' 参数 {name} 应为 {expected_type}，实际为 {type(value).__name__}"
            if expected_type == "boolean" and not isinstance(value, bool):
                return f"工具 '{self.name}' 参数 {name} 应为 {expected_type}，实际为 {type(value).__name__}"
            if expected_type == "array":
                if not isinstance(value, list):
                    return f"工具 '{self.name}' 参数 {name} 应为 array，实际为 {type(value).__name__}"
                items_schema = schema.get("items", {})
                items_type = items_schema.get("type", "")
                if items_type:
                    for idx, item in enumerate(value):
                        err = self._check_type(item, items_type)
                        if err:
                            return f"工具 '{self.name}' 参数 {name}[{idx}] 应为 {items_type}，实际为 {type(item).__name__}"
            if expected_type == "object":
                if not isinstance(value, dict):
                    return f"工具 '{self.name}' 参数 {name} 应为 object，实际为 {type(value).__name__}"
                properties = schema.get("properties", {})
                for prop_name, prop_schema in properties.items():
                    if prop_name in value:
                        err = self._check_type(value[prop_name], prop_schema.get("type", ""))
                        if err:
                            return f"工具 '{self.name}' 参数 {name}.{prop_name} 类型错误：{err}"
            if "enum" in schema:
                allowed = schema["enum"]
                if value not in allowed:
                    return f"工具 '{self.name}' 参数 {name} 必须为 {allowed} 之一，实际为 {value}"
        return None

    def _check_type(self, value: Any, expected_type: str) -> str | None:
        """检查单个值的类型是否符合预期，返回错误信息或 None。"""
        if expected_type == "string" and not isinstance(value, str):
            return f"应为 string，实际为 {type(value).__name__}"
        if expected_type in ("integer", "number"):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return f"应为 {expected_type}，实际为 {type(value).__name__}"
        if expected_type == "boolean" and not isinstance(value, bool):
            return f"应为 boolean，实际为 {type(value).__name__}"
        if expected_type == "array" and not isinstance(value, list):
            return f"应为 array，实际为 {type(value).__name__}"
        if expected_type == "object" and not isinstance(value, dict):
            return f"应为 object，实际为 {type(value).__name__}"
        return None


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
