"""PermissionConfigManager — 从 YAML 加载权限配置，提供按工具名查询权限的全局方法"""

from __future__ import annotations

from typing import Any

from core.config import get_config_instance
from core.logger import get_logger
from agent.tools.base import ToolPermission

logger = get_logger(__name__)


class PermissionConfigManager:
    """权限配置管理器

    从 YAML 文件加载工具权限映射，供 ``ToolBase.check_permissions`` 查询。
    未配置的工具返回 ``None``，由工具自身使用代码默认权限。

    Usage::

        config = PermissionConfigManager()
        perm = config.get_permission("memory_query")
        if perm == ToolPermission.NEVER_ALLOW:
            ...
    """

    def __init__(self, config_path: str = "data/config/Permissions.yml") -> None:
        self._config = get_config_instance(config_path)
        logger.debug("权限配置已加载 | path=%s", config_path)

    # ── 公共查询接口 ─────────────────────────────────────────────

    def get_permission(self, tool_name: str) -> ToolPermission | None:
        """查询某工具的全局权限等级。

        Returns:
            ToolPermission 实例，或 None（表示未配置，应使用工具默认权限）。
        """
        raw = self._get_raw(tool_name)
        if raw is None:
            logger.debug("权限未配置 | tool=%s → 使用默认", tool_name)
            return None
        try:
            return ToolPermission(raw)
        except ValueError:
            logger.warning("权限配置值无效 | tool=%s | value=%s", tool_name, raw)
            return None

    def is_tool_enabled(self, tool_name: str) -> bool:
        """检查工具是否启用。未配置时默认启用。"""
        perm = self.get_permission(tool_name)
        if perm is None:
            return True
        return perm != ToolPermission.NEVER_ALLOW

    def get_effective_permission(
        self,
        tool_name: str,
        default_permission: ToolPermission = ToolPermission.ALWAYS_ALLOW,
    ) -> ToolPermission:
        """获取有效权限：配置优先，配置为空则回退到 ``default_permission``。"""
        effective = self.get_permission(tool_name)
        if effective is not None:
            return effective
        return default_permission

    # ── 内部方法 ─────────────────────────────────────────────────

    def _get_raw(self, tool_name: str) -> str | None:
        """读取原始字符串值。"""
        path = f"tools.{tool_name}"
        raw = self._config.get(path)
        if raw is not None and not isinstance(raw, str):
            logger.warning("权限配置类型异常 | path=%s | type=%s", path, type(raw).__name__)
            return None
        return raw  # type: ignore[return-value]
