"""主动聊天模块：调度器 + 护栏 + 渠道路由

参考 Cyrene-Agent 的 scheduler + proactive 模块。
"""

from agent.proactive.scheduler import ProactiveScheduler, TriggerConfig, create_proactive_scheduler

__all__ = [
    "ProactiveScheduler",
    "TriggerConfig",
    "create_proactive_scheduler",
]
