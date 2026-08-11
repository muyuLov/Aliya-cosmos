"""提供商外部配置加载工具。

LLM 与 TTS 模块共享的 providers.name → providers.config_path → JSON 文件
这一配置解析路径，消除重复代码。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.config.env_resolver import resolve_env_vars


def load_provider_config(
    section: dict[str, Any],
    error_cls: type[Exception],
) -> tuple[str, dict[str, Any]]:
    """
    从 ``providers.name`` → ``providers.config_path`` → 外部 JSON 文件
    加载并解析提供商配置。

    Args:
        section: 服务配置节点（如 ``cosmos.service.llm``）。
        error_cls: 出错时抛出的异常类型；必须接受单个 ``str`` 参数。

    Returns:
        ``(provider_name, resolved_provider_config)`` 元组。

    Raises:
        error_cls: 配置不完整、文件不存在或 JSON 解析失败时抛出。
    """
    providers = section.get("providers", {})

    if not isinstance(providers, dict):
        raise error_cls("配置格式错误：providers 必须是字典类型")

    provider_name = providers.get("name")
    if not provider_name:
        raise error_cls(
            "配置中未找到 providers.name 字段，请使用格式：\n"
            '{"providers": {"name": "openai", "config_path": "data/config/LLMProviders.json"}}'
        )

    config_path = providers.get("config_path")
    if not config_path:
        raise error_cls(
            "配置中未找到 providers.config_path 字段，必须指定外部配置文件路径"
        )

    try:
        path = Path(config_path)
        with path.open("r", encoding="utf-8") as f:
            external_configs = json.load(f)

        if not isinstance(external_configs, dict):
            raise error_cls(
                f"外部配置文件格式错误：{config_path}，期望 JSON 对象"
            )

        if provider_name not in external_configs:
            available = list(external_configs.keys())
            raise error_cls(
                f"外部配置文件 {config_path} 中未找到提供商 '{provider_name}'。\n"
                f"可用的提供商：{available}"
            )

        provider_config = external_configs[provider_name]
        return provider_name, dict(resolve_env_vars(provider_config))

    except FileNotFoundError as e:
        raise error_cls(f"外部配置文件不存在：{config_path}") from e
    except json.JSONDecodeError as e:
        raise error_cls(
            f"外部配置文件 JSON 解析失败：{config_path}\n{e}"
        ) from e
