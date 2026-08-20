"""工具权限检查：读取 Permissions.yml，按 deny > confirm > allow > 默认 判定"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

import yaml


class Permission(Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


# Permissions.yml 中的配置值 → Permission 枚举
_LEVEL_MAP = {
    "always_allow": Permission.ALLOW,
    "confirm": Permission.CONFIRM,
    "never_allow": Permission.DENY,
}


class PermissionChecker:
    """基于配置文件与工具风险等级的工具权限检查。"""

    def __init__(self, config_path: str) -> None:
        self._config_path = Path(config_path)
        self._tools: dict[str, Permission] = {}
        self._load()

    def _load(self) -> None:
        try:
            with self._config_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except (FileNotFoundError, yaml.YAMLError):
            data = {}
        for tool_id, level in (data.get("tools") or {}).items():
            perm = _LEVEL_MAP.get(str(level).lower())
            if perm is not None:
                self._tools[tool_id] = perm

    def check(self, tool_id: str, risk: str = "safe") -> Permission:
        """检查工具权限。规则优先级：配置 deny > confirm > allow > 默认策略。

        Args:
            tool_id: 工具标识。
            risk: 工具风险等级（"safe" / "medium" / "high"），未配置时按默认策略。

        Returns:
            Permission.ALLOW / CONFIRM / DENY。
        """
        configured = self._tools.get(tool_id)
        if configured is not None:
            return configured
        # 默认策略：high → CONFIRM；medium → CONFIRM；safe → ALLOW
        if risk == "high":
            return Permission.CONFIRM
        if risk == "medium":
            return Permission.CONFIRM
        return Permission.ALLOW
