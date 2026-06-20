"""工具加载器和注册表构建。"""

from __future__ import annotations

from typing import Any

from agent.tools.base import BaseTool
from agent.tools.registry import ToolRegistry
from core.logger import get_logger

logger = get_logger(__name__)


class ToolLoader:
    """
    直接代码注册工具到 ToolRegistry。

    使用方式：
        loader = ToolLoader()
        loader.register_tool(ReplyTool())
        loader.register_tool(TTSTool(tts_service=..., audio_player=...))
        loader.register_tool(DateTimeTool())
        registry = loader.build_registry(timeout_seconds=30.0)
    """

    def __init__(self) -> None:
        self._tools: list[BaseTool] = []
        self._registered_names: set[str] = set()

    def register_tool(self, tool: BaseTool) -> None:
        """注册一个工具实例，如果名称已存在则跳过。"""
        if tool.name in self._registered_names:
            logger.warning("工具名称冲突，跳过：%s (%s)", tool.name, type(tool).__name__)
            return
        self._tools.append(tool)
        self._registered_names.add(tool.name)
        logger.debug("工具已加入注册队列：%s (%s)", tool.name, type(tool).__name__)

    def build_registry(
        self,
        timeout_seconds: float = 30.0,
    ) -> ToolRegistry:
        """
        构建并返回填充好的 ToolRegistry。

        Args:
            timeout_seconds: 工具执行超时秒数，传给 ToolRegistry。
        """
        registry = ToolRegistry(timeout_seconds=timeout_seconds)

        for tool in self._tools:
            registry.register(tool)
            logger.debug("工具已注册到注册表：%s (%s)", tool.name, type(tool).__name__)

        tools = registry.list_tools()
        logger.info(
            "工具注册完成：count=%d | names=%s | timeout=%.1fs",
            len(tools),
            [t.name for t in tools],
            timeout_seconds,
        )
        return registry

    @classmethod
    def build_default_registry(
        cls,
        timeout_seconds: float = 30.0,
        injections: dict[str, Any] | None = None,
    ) -> ToolRegistry:
        """
        构建默认工具注册表。

        Args:
            timeout_seconds: 工具执行超时秒数。
            injections: 运行时依赖字典，可包含 tts_service、audio_player、memory_manager 等。
        """
        from agent.tools.builtin import ReplyTool, SkillTool, TTSTool
        from agent.tools.utility import DateTimeTool

        # 按需启用的高级工具（取消注释即可）
        # from agent.tools.advanced import CodeExecutionTool, WebSearchTool
        # from agent.tools.utility import FileTool

        injections = injections or {}
        loader = cls()

        # 注册默认启用的工具
        loader.register_tool(ReplyTool(output_channel=injections.get("output_channel")))
        loader.register_tool(
            TTSTool(
                tts_service=injections.get("tts_service"),
                audio_player=injections.get("audio_player"),
            )
        )
        loader.register_tool(DateTimeTool())
        loader.register_tool(SkillTool())

        # 注册内部工具（结果注入对话，供 LLM 继续推理）
        memory_manager = injections.get("memory_manager")
        if memory_manager is not None:
            from agent.tools.advanced import MemoryQueryTool
            loader.register_tool(MemoryQueryTool(memory_manager))

        # 注册默认禁用的工具（按需启用）
        # loader.register_tool(FileTool(
        #     allowed_paths=["."],
        #     enable_cache=True,
        #     cache_ttl=60.0,
        # ))
        # loader.register_tool(WebSearchTool(search_api_url=""))

        return loader.build_registry(timeout_seconds=timeout_seconds)
