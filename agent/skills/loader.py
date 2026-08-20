"""Skill 加载器：扫描 agent/skills/*/ 下合规模块，注册到 ToolRegistry。"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import cast

from agent.tools.base import ToolDefinition, ToolExecutor
from agent.tools.registry import ToolRegistry
from core.logger import get_logger

logger = get_logger(__name__)
_SKILLS_DIR = Path(__file__).resolve().parent


def load_skills(registry: ToolRegistry) -> int:
    """扫描并注册全部 skill，返回注册数量。"""
    count = 0
    for modinfo in pkgutil.iter_modules([str(_SKILLS_DIR)]):
        name = modinfo.name
        if name in ("base", "loader", "__pycache__"):
            continue
        try:
            module = importlib.import_module(f"agent.skills.{name}")
            definition = getattr(module, "definition", None)
            execute = getattr(module, "execute", None)
            if isinstance(definition, ToolDefinition) and callable(execute):
                registry.register(definition, cast(ToolExecutor, execute))
                count += 1
                logger.info("已加载 skill: %s", name)
            else:
                logger.warning("skill %s 缺少 definition/execute，跳过", name)
        except Exception as e:
            logger.warning("加载 skill %s 失败: %s", name, e)
    return count
