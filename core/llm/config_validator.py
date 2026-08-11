"""LLM 配置校验器：校验 OpenAI 兼容接口的通用配置字段"""

from __future__ import annotations

from typing import Any

from core.logger import get_logger

logger = get_logger(__name__)

# 通用必需字段：所有 OpenAI 兼容服务都需要
_REQUIRED_FIELDS = frozenset({"url", "model"})

# 可选字段及其默认值（与 LLMProvider.__init__ 保持一致）
_OPTIONAL_FIELDS: dict[str, Any] = {
    "api_key": "",
    "timeout": 600,
    "max_retries": 3,
    "provider_name": "openai_compatible",
}


class ConfigValidator:
    """
    OpenAI 兼容接口配置校验器。

    Examples:
        >>> ConfigValidator.validate({"url": "http://localhost:11434", "model": "qwen2.5:14b"})
        (True, [])
        >>> ConfigValidator.validate({"url": "http://localhost:11434"})
        (False, ["缺少必需字段: model"])
    """

    @staticmethod
    def validate(config: dict[str, Any]) -> tuple[bool, list[str]]:
        """
        校验提供商配置的完整性和合法性。

        Args:
            config: 提供商配置字典。

        Returns:
            ``(is_valid, errors)`` 元组。
        """
        errors: list[str] = []

        # 校验必需字段
        missing = [f for f in _REQUIRED_FIELDS if f not in config]
        if missing:
            errors.append(f"缺少必需字段: {', '.join(missing)}")

        # 校验字段值类型
        if "url" in config and not isinstance(config["url"], str):
            errors.append(f"字段 'url' 应为字符串类型，当前为 {type(config['url']).__name__}")

        if "model" in config and not isinstance(config["model"], str):
            errors.append(f"字段 'model' 应为字符串类型，当前为 {type(config['model']).__name__}")

        # url 必须为 HTTP(S) 地址
        if isinstance(config.get("url"), str):
            url_cfg = config["url"]
            if not (url_cfg.startswith("http://") or url_cfg.startswith("https://")):
                errors.append(f"字段 'url' 必须以 http:// 或 https:// 开头: {url_cfg}")

        return (len(errors) == 0, errors)

    @staticmethod
    def validate_with_defaults(config: dict[str, Any]) -> dict[str, Any]:
        """
        校验并自动补全可选字段的默认值。

        Args:
            config: 提供商配置字典。

        Returns:
            补全默认值后的配置字典副本（不修改原字典）。
        """
        result = dict(config)
        for field, default in _OPTIONAL_FIELDS.items():
            if field not in result:
                result[field] = default
        return result

    @classmethod
    def validate_and_log(cls, llm_section: dict[str, Any], strict: bool = True) -> bool:
        """
        校验根配置节点并记录日志。

        校验流程：先解析 providers 外部配置，再对解析后的提供商配置进行字段校验。

        Args:
            llm_section: cosmos.service.llm 配置节点。
            strict: True 时遇到校验失败会抛出异常，False 时仅记录警告。

        Returns:
            校验是否通过。
        """
        from core.llm import _resolve_provider_config

        try:
            provider_config = _resolve_provider_config(llm_section)
        except Exception as e:
            logger.error("解析提供商配置失败: %s", e)
            if strict:
                raise
            return False

        # 补全默认值
        provider_config = cls.validate_with_defaults(provider_config)

        is_valid, errors = cls.validate(provider_config)
        if not is_valid:
            msg = f"提供商配置校验失败:\n" + "\n".join(f"  - {e}" for e in errors)
            if strict:
                from core.llm.exceptions import ProviderNotFoundError

                raise ProviderNotFoundError(msg)
            logger.warning(msg)
        else:
            logger.info(
                "LLM 提供商配置校验通过: url=%s, model=%s",
                provider_config.get("url"),
                provider_config.get("model"),
            )

        return is_valid
