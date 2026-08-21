"""提供商注册表：支持动态注册/发现 LLM 提供商"""

from __future__ import annotations

from typing import Any

from core.llm.providers.base import LLMProvider


class ProviderRegistry:
    """提供商注册表，支持按名称注册和发现提供商类。

    使用方式::

        # 注册自定义提供商
        from core.llm.providers.registry import ProviderRegistry
        ProviderRegistry.register("my_provider", MyProviderClass)

        # 从注册表创建实例
        provider = ProviderRegistry.create("my_provider", config)

        # 列出所有已注册的提供商
        providers = ProviderRegistry.list_providers()
    """

    _providers: dict[str, type[LLMProvider]] = {}

    @classmethod
    def register(cls, name: str, provider_cls: type[LLMProvider]) -> None:
        """注册提供商类。

        Args:
            name: 提供商名称标识符。
            provider_cls: 提供商类（必须继承 LLMProvider）。

        Raises:
            TypeError: provider_cls 未继承 LLMProvider。
        """
        if not (isinstance(provider_cls, type) and issubclass(provider_cls, LLMProvider)):
            raise TypeError(f"provider_cls 必须是 LLMProvider 的子类，当前为 {provider_cls}")
        cls._providers[name] = provider_cls

    @classmethod
    def get(cls, name: str) -> type[LLMProvider] | None:
        """按名称获取提供商类。

        Args:
            name: 提供商名称标识符。

        Returns:
            提供商类，未注册时返回 None。
        """
        return cls._providers.get(name)

    @classmethod
    def list_providers(cls) -> list[str]:
        """列出所有已注册的提供商名称。

        Returns:
            已注册的提供商名称列表。
        """
        return list(cls._providers.keys())

    @classmethod
    def create(cls, name: str, config: dict[str, Any]) -> LLMProvider:
        """从注册表创建提供商实例。

        Args:
            name: 提供商名称标识符。
            config: 提供商配置字典。

        Returns:
            提供商实例。

        Raises:
            ValueError: 提供商未注册。
        """
        provider_cls = cls._providers.get(name)
        if provider_cls is None:
            available = ", ".join(cls._providers.keys()) or "（无）"
            raise ValueError(
                f"提供商 '{name}' 未注册。已注册的提供商: {available}"
            )
        return provider_cls(config)
