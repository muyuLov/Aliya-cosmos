"""主动聊天调度器 + 护栏 + 渠道路由

触发器定义"何时"、护栏定义"是否允许"、路由定义"投递到哪"。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TriggerConfig:
    """单条触发器配置。"""

    type: str  # "schedule" | "idle"
    at: str | None = None  # HH:MM，schedule 用
    message: str = ""


@dataclass
class ProactiveConfig:
    """主动聊天全局配置。"""

    enabled: bool = False
    check_interval_seconds: int = 60
    quiet_hours_start: str = "23:00"
    quiet_hours_end: str = "07:00"
    idle_timeout_minutes: int = 30
    triggers: list[TriggerConfig] = field(default_factory=list)
    max_unanswered: int = 3  # 连续未回复触发次数上限


def _parse_time(s: str) -> time:
    """解析 HH:MM 格式时间，失败时返回午夜。"""
    try:
        parts = s.strip().split(":")
        return time(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        logger.warning("时间格式无效: %s，使用默认 00:00", s)
        return time(0, 0)


def _in_quiet_hours(now: datetime, start: str, end: str) -> bool:
    """判断当前时间是否在不打搅时段内。"""
    t = now.time()
    qs = _parse_time(start)
    qe = _parse_time(end)

    if qs <= qe:
        # 正常时段：如 07:00 ~ 23:00
        return qs <= t <= qe
    else:
        # 跨午夜：如 23:00 ~ 07:00
        return t >= qs or t <= qe


class ProactiveScheduler:
    """主动聊天调度器：后台轮询 + 护栏 + 消息投递。

    仅支持投递到当前 GUI 连接（通过 sink 回调）。
    """

    def __init__(
        self,
        config: ProactiveConfig,
        sink: Callable[[str], Awaitable[None]],
    ) -> None:
        self._config = config
        self._sink = sink

        # 运行时状态
        self._enabled = config.enabled
        self._task: asyncio.Task | None = None
        self._last_user_message_time: datetime | None = None
        self._processing: bool = False
        self._triggered_count: int = 0
        self._schedule_triggered_today: dict[str, bool] = {}
        self._today: str = ""  # YYYY-MM-DD，用于日切重置

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def set_enabled(self, enabled: bool) -> None:
        """动态开关。"""
        self._enabled = enabled
        if enabled and self._task is None:
            await self.start()
        elif not enabled and self._task is not None:
            await self.stop()

    async def start(self) -> None:
        """启动后台轮询任务。"""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())
        logger.info("主动聊天调度器已启动")

    async def stop(self) -> None:
        """停止后台轮询任务。"""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("主动聊天调度器已停止")

    async def notify_user_message(self, session_id: str) -> None:
        """用户发消息时调用：刷新计时 + 重置触发计数。"""
        self._last_user_message_time = datetime.now()
        self._triggered_count = 0
        logger.debug("主动聊天：用户消息通知 session=%s", session_id)

    def set_processing(self, processing: bool) -> None:
        """设置是否正在处理对话（对话进行中不触发）。"""
        self._processing = processing

    def can_trigger(self, trigger: TriggerConfig, now: datetime | None = None) -> bool:
        """判断指定触发器是否可以触发。"""
        if not self._enabled:
            return False

        # 公共护栏
        if _in_quiet_hours(now or datetime.now(), self._config.quiet_hours_start, self._config.quiet_hours_end):
            return False
        if self._processing:
            return False
        if self._triggered_count >= self._config.max_unanswered:
            return False

        # 日切重置
        today = (now or datetime.now()).strftime("%Y-%m-%d")
        if today != self._today:
            self._today = today
            self._schedule_triggered_today.clear()

        if trigger.type == "schedule":
            return self._can_trigger_schedule(trigger, now or datetime.now())
        elif trigger.type == "idle":
            return self._can_trigger_idle(trigger, now or datetime.now())
        return False

    def _can_trigger_schedule(self, trigger: TriggerConfig, now: datetime) -> bool:
        """定时触发：分钟精度匹配且当天未触发过。"""
        if not trigger.at:
            return False
        target = _parse_time(trigger.at)
        if now.hour != target.hour or now.minute != target.minute:
            return False
        if self._schedule_triggered_today.get(trigger.at, False):
            return False
        self._schedule_triggered_today[trigger.at] = True
        return True

    def _can_trigger_idle(self, _trigger: TriggerConfig, now: datetime) -> bool:
        """静默超时触发。"""
        if self._last_user_message_time is None:
            # 从未收到用户消息：视为长时间空闲
            return True
        elapsed = now - self._last_user_message_time
        return elapsed >= timedelta(minutes=self._config.idle_timeout_minutes)

    async def _loop(self) -> None:
        """后台轮询主循环。"""
        while True:
            try:
                await asyncio.sleep(self._config.check_interval_seconds)
            except asyncio.CancelledError:
                return

            now = datetime.now()
            for trigger in self._config.triggers:
                if self.can_trigger(trigger, now):
                    logger.info("主动聊天触发 | type=%s | message=%s", trigger.type, trigger.message[:50])
                    try:
                        await self._sink(trigger.message)
                        self._triggered_count += 1
                    except Exception:
                        logger.exception("主动聊天消息投递失败")

    def reset_daily(self) -> None:
        """手动重置日切状态（用于测试）。"""
        self._schedule_triggered_today.clear()
        self._triggered_count = 0


def create_proactive_scheduler(
    config: dict[str, Any],
    sink: Callable[[str], Awaitable[None]],
) -> ProactiveScheduler:
    """工厂函数：从配置字典创建 ProactiveScheduler。"""
    proactive_cfg = config.get("proactive", {})

    triggers = []
    for t in proactive_cfg.get("triggers", []):
        triggers.append(TriggerConfig(
            type=t.get("type", "schedule"),
            at=t.get("at"),
            message=t.get("message", ""),
        ))

    cfg = ProactiveConfig(
        enabled=proactive_cfg.get("enabled", False),
        check_interval_seconds=proactive_cfg.get("check_interval_seconds", 60),
        quiet_hours_start=proactive_cfg.get("quiet_hours", {}).get("start", "23:00"),
        quiet_hours_end=proactive_cfg.get("quiet_hours", {}).get("end", "07:00"),
        idle_timeout_minutes=proactive_cfg.get("idle_timeout_minutes", 30),
        triggers=triggers,
    )

    return ProactiveScheduler(cfg, sink)
