"""环境变量解析工具：支持 ${ENV_VAR} 和 ${ENV_VAR:default} 占位符语法"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

# 自动加载项目根目录下的 .env 文件（不覆盖已有的系统环境变量）
try:
    from dotenv import load_dotenv as _load_dotenv
    _env_file = Path(__file__).parent.parent.parent / ".env"
    _load_dotenv(_env_file, override=False)
except ImportError:
    pass  # python-dotenv 未安装时静默跳过

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")

SENSITIVE_KEYS: frozenset[str] = frozenset({
    "api_key", "key", "password", "secret", "token",
    "access_key", "secret_key", "auth_token",
})


def resolve_env_var_string(value: str) -> str:
    """解析字符串中的 ``${ENV_VAR}`` 或 ``${ENV_VAR:default}`` 占位符。

    未设置的环境变量且无默认值时保留原占位符字符串，使调用方
    的 ``config.get(..., default)`` 退路生效，而非崩溃。
    """
    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        default = match.group(2)
        result = os.environ.get(var_name)
        if result is not None:
            return result
        if default is not None:
            return default
        # 环境变量未设且无默认值：返回空字符串，避免将占位符原文传播给下游
        import logging
        logging.getLogger(__name__).warning(
            "环境变量 %s 未设置且无默认值，已替换为空字符串", var_name
        )
        return ""
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
