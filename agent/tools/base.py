"""工具基础类型定义

参考 Claude Code 的流式工具执行模式，引入：
- ``is_concurrency_safe``：标记只读/写入工具，支持分区并行
- ``check_permissions``：执行前置权限验证钩子，支持配置驱动
- ``ToolBase``：工具基类，提供默认的配置感知权限校验
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Protocol


class ToolPermission(Enum):
    """工具权限等级"""
    ALWAYS_ALLOW = "always_allow"   # 始终允许（如回复、记忆查询）
    CONFIRM = "confirm"             # 需要用户确认
    NEVER_ALLOW = "never_allow"     # 始终禁止


@dataclass
class ToolResult:
    """工具执行结果"""
    success: bool
    data: Any = None
    error: str | None = None
    # 工具执行耗时（秒），由调度器填充
    duration: float = 0.0


@dataclass
class ToolContext:
    tts_service: Any | None = None
    audio_player: Any | None = None
    memory_manager: Any | None = None
    send_message: Callable[[dict], Awaitable[None]] | None = None
    # 音频转发通道（仅 WebSocket 模式可用）；控制台模式为 None，避免向终端推送音频
    audio_relay: Callable[[dict], Awaitable[None]] | None = None
    # 权限配置（由 Agent 注入，用于配置驱动的权限校验）
    permission_config: Any | None = None
    # 用户确认回调：工具名 + 参数 → 用户是否允许执行（由运行时环境提供实现）
    confirm_callback: Callable[[str, dict], Awaitable[bool]] | None = None


class BaseTool(Protocol):
    """工具协议

    所有工具须实现 execute 方法；可选覆盖以下属性：
    - ``is_concurrency_safe`` (默认 False)：只读工具可安全并发执行
    - ``permission`` (默认 ALWAYS_ALLOW)：权限控制等级
    """

    name: str
    description: str
    input_schema: dict
    is_concurrency_safe: bool = False
    permission: ToolPermission = ToolPermission.ALWAYS_ALLOW

    async def execute(self, params: dict, context: ToolContext) -> ToolResult: ...

    async def check_permissions(
        self, params: dict, context: ToolContext,
    ) -> tuple[bool, str | None]:
        """执行前的权限校验。

        Returns:
            (True, None) 表示允许；
            (False, reason) 表示拒绝，reason 为拒绝原因。
        """
        return True, None


class ToolBase:
    """工具基类（供具体工具继承）

    提供默认的 ``check_permissions`` 实现，优先使用配置驱动权限，
    未配置时回退到工具自身的 ``permission`` 属性。

    子类需覆盖：
    - ``name``、``description``、``input_schema``
    - ``execute`` 方法
    """

    name: str = ""
    description: str = ""
    input_schema: dict = {}
    is_concurrency_safe: bool = False
    permission: ToolPermission = ToolPermission.ALWAYS_ALLOW

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        raise NotImplementedError(f"工具 `{self.name}` 未实现 execute 方法")

    async def check_permissions(
        self, params: dict, context: ToolContext,
    ) -> tuple[bool, str | None]:
        """配置驱动的权限校验。

        优先级：PermissionConfig 配置 > 工具自身 ``permission`` 属性。
        """
        if context.permission_config:
            effective = context.permission_config.get_permission(self.name)
        else:
            effective = None

        if effective is None:
            effective = self.permission

        if effective == ToolPermission.NEVER_ALLOW:
            return False, f"工具 `{self.name}` 已被禁用"
        if effective == ToolPermission.CONFIRM:
            if context.confirm_callback:
                confirmed = await context.confirm_callback(self.name, params)
                if confirmed:
                    return True, None
                return False, f"用户拒绝了工具 `{self.name}` 的执行"
            return False, f"工具 `{self.name}` 需要用户确认，但当前运行环境不支持交互确认"

        return True, None
