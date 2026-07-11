"""Agent 模块

架构：
  agent/agent.py   — AliyaAgent 主编排器（含 LLM 思考、JSON 解析）
  agent/ws.py      — WebSocket 端点处理器
  agent/tools/     — 工具系统（注册、回复、TTS）
"""

from agent.agent import AliyaAgent, BrainResult

__all__ = [
    "AliyaAgent",
    "BrainResult",
]
