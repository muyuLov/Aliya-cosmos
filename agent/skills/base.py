"""Skill 协议：一个 skill 即一个可被 FC 调用的工具。"""

from __future__ import annotations

from agent.tools.base import ToolDefinition, ToolExecutor

# 每个 skill 模块必须导出：
#   definition: ToolDefinition
#   execute: ToolExecutor

__all__ = ["ToolDefinition", "ToolExecutor"]
