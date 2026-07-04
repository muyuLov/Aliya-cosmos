"""环境变量解析工具：支持 ${ENV_VAR} 和 ${ENV_VAR:default} 占位符语法"""

from __future__ import annotations

import os
import re
from typing import Any

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")

SENSITIVE_KEYS: frozenset[str] = frozenset({
    "api_key", "key", "password", "secret", "token",
    "access_key", "secret_key", "auth_token",
})


def resolve_env_var_string(value: str) -> str:
    """解析字符串中的 ``${ENV_VAR}`` 或 ``${ENV_VAR:default}`` 占位符。"""
    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        default = match.group(2)
        result = os.environ.get(var_name)
        if result is not None:
            return result
        if default is not None:
            return default
        raise KeyError(
            f"环境变量 {var_name} 未设置，且未提供默认值"
        )
    return _ENV_VAR_PATTERN.sub(_replace, value)


def resolve_env_vars(value: Any) -> Any:
    """递归解析任意嵌套结构中的环境变量占位符。"""
    if isinstance(value, str):
        return resolve_env_var_string(value)
    if isinstance(value, dict):
        return {k: resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_env_vars(item) for item in value]
    return value


def mask_sensitive(data: dict[str, Any]) -> dict[str, Any]:
    """对字典中的敏感字段值进行脱敏处理（仅用于日志/调试输出）。"""
    masked: dict[str, Any] = {}
    for key, value in data.items():
        if key.lower() in SENSITIVE_KEYS and isinstance(value, str) and len(value) > 8:
            masked[key] = value[:4] + "****" + value[-4:]
        elif isinstance(value, dict):
            masked[key] = mask_sensitive(value)
        else:
            masked[key] = value
    return masked
