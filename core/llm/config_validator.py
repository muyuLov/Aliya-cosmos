"""LLM 配置验证器：检查配置的完整性和合法性"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.config.env_resolver import resolve_env_vars as _resolve_env_vars

from core.logger import get_logger

logger = get_logger(__name__)


class ConfigValidator:
    """LLM 配置验证器"""

    # 提供商必需字段
    REQUIRED_FIELDS = {
        "ollama": ["url", "model"],
        "deepseek": ["url", "model"],
        "lmstudio": ["url", "model"],
    }

    # 可选字段及其默认值
    OPTIONAL_FIELDS = {
        "timeout": 600,
        "max_retries": 3,
    }

    @classmethod
    def validate_provider_config(
        cls,
        provider_name: str,
        config: dict[str, Any],
        *,
        strict: bool = False,
    ) -> tuple[bool, list[str]]:
        """
        验证提供商配置的完整性。

        Args:
            provider_name: 提供商名称。
            config: 提供商配置字典。
            strict: 严格模式，缺少必需字段时返回 False。

        Returns:
            (是否有效, 警告/错误信息列表) 元组。
        """
        messages = []
        is_valid = True

        # 检查必需字段
        required = cls.REQUIRED_FIELDS.get(provider_name, [])
        for field in required:
            if field not in config or not config[field]:
                messages.append(f"缺少必需字段: {field}")
                is_valid = False

        # 检查可选字段的合法性
        if "timeout" in config:
            timeout = config["timeout"]
            if not isinstance(timeout, (int, float)) or timeout <= 0:
                messages.append(f"timeout 必须为正数，当前值: {timeout}")
                if strict:
                    is_valid = False

        if "max_retries" in config:
            max_retries = config["max_retries"]
            if not isinstance(max_retries, int) or max_retries < 0:
                messages.append(f"max_retries 必须为非负整数，当前值: {max_retries}")
                if strict:
                    is_valid = False

        # 特定提供商的额外检查
        if provider_name == "deepseek":
            api_key = config.get("api_key") or config.get("key", "")
            if not api_key:
                messages.append("缺少必需字段: api_key 或 key（二者提供其一即可）")
                is_valid = False
            elif not api_key.startswith("sk-"):
                messages.append("DeepSeek API 密钥格式可能不正确（通常以 sk- 开头）")

        return is_valid, messages

    @classmethod
    def validate_llm_section(
        cls,
        llm_section: dict[str, Any],
        *,
        strict: bool = False,
    ) -> tuple[bool, list[str]]:
        """
        验证 LLM 配置节点的完整性。

        Args:
            llm_section: LLM 配置节点字典。
            strict: 严格模式。

        Returns:
            (是否有效, 警告/错误信息列表) 元组。
        """
        messages = []
        is_valid = True

        # 检查实际生效的 history_max_chars（工厂读取此键）
        for key in ("history_max_chars", "max_context_tokens"):
            if key in llm_section:
                val = llm_section[key]
                if not isinstance(val, int) or val <= 0:
                    messages.append(f"{key} 必须为正整数，当前值: {val}")
                    if strict:
                        is_valid = False
                elif val < 20000:
                    messages.append(f"{key} 过小（{val}），建议至少 20000")
                elif val > 1000000:
                    messages.append(f"{key} 过大（{val}），可能导致性能问题")

        # 检查是否使用了废弃的配置
        if "max_context_length" in llm_section:
            messages.append(
                "检测到废弃的配置项 max_context_length，已无实际效果，请移除"
            )

        # 检查提供商配置
        provider_count = 0
        providers_section = llm_section.get("providers", {})

        if not isinstance(providers_section, dict):
            messages.append("providers 必须是字典类型")
            is_valid = False
        else:
            provider_name = providers_section.get("name")
            if provider_name:
                provider_count += 1
                if provider_name not in cls.REQUIRED_FIELDS:
                    messages.append(
                        f"未知的提供商名称: {provider_name}，"
                        f"支持的提供商: {list(cls.REQUIRED_FIELDS.keys())}"
                    )
                    if strict:
                        is_valid = False
                if not providers_section.get("config_path"):
                    messages.append("缺少 providers.config_path 字段，必须指定外部配置文件路径")
                    is_valid = False

        if provider_count == 0:
            messages.append("未找到任何提供商配置，请在 providers.name 中指定提供商名称")
            is_valid = False

        return is_valid, messages

    @classmethod
    def validate_and_log(
        cls,
        llm_section: dict[str, Any],
        *,
        strict: bool = False,
    ) -> bool:
        """
        验证配置并记录日志。

        Args:
            llm_section: LLM 配置节点字典。
            strict: 严格模式。

        Returns:
            配置是否有效。
        """
        is_valid, messages = cls.validate_llm_section(llm_section, strict=strict)

        # 额外验证 provider 外部配置文件的字段合法性
        providers_section = llm_section.get("providers", {})
        provider_name = providers_section.get("name")
        config_path = providers_section.get("config_path")
        if provider_name and config_path:
            try:
                all_provider_configs = json.loads(Path(config_path).read_text(encoding="utf-8"))
                raw_config = all_provider_configs.get(provider_name, {})
                provider_config = _resolve_env_vars(raw_config)
                prov_valid, prov_msgs = cls.validate_provider_config(
                    provider_name, provider_config, strict=strict
                )
                if not prov_valid:
                    is_valid = False
                messages.extend(prov_msgs)
            except (FileNotFoundError, json.JSONDecodeError) as e:
                messages.append(f"加载提供商配置文件失败: {e}")
                is_valid = False

        if messages:
            for msg in messages:
                if is_valid:
                    logger.warning("配置警告: %s", msg)
                else:
                    logger.error("配置错误: %s", msg)

        if is_valid and not messages:
            logger.debug("LLM 配置验证通过")

        return is_valid
