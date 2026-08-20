"""测试 agent.skills.loader：自动发现、跳过非法 skill、dice 示例执行。"""

from __future__ import annotations

import types

from agent.skills import loader
from agent.skills.dice import definition as dice_definition
from agent.skills.dice import execute as dice_execute
from agent.tools.base import ToolContext
from agent.tools.registry import ToolRegistry


def test_load_skills_registers_dice():
    reg = ToolRegistry()
    count = loader.load_skills(reg)
    assert count >= 1
    names = {d.name for d in reg.enabled_definitions()}
    assert "roll_dice" in names


def test_illegal_skill_skipped(monkeypatch, tmp_path):
    """缺 definition/execute 的 skill 被跳过，不抛异常。"""
    (tmp_path / "bad_skill").mkdir()
    bad_mod = types.ModuleType("agent.skills.bad_skill")
    setattr(bad_mod, "VALUE", 42)  # 无 definition / execute

    real_import = loader.importlib.import_module

    def fake_import(name):
        if name == "agent.skills.bad_skill":
            return bad_mod
        return real_import(name)

    monkeypatch.setattr(loader, "_SKILLS_DIR", tmp_path)
    monkeypatch.setattr(loader.importlib, "import_module", fake_import)

    reg = ToolRegistry()
    count = loader.load_skills(reg)
    assert count == 0
    assert reg.enabled_definitions() == []


async def test_dice_definition_shape():
    assert dice_definition.id == "roll_dice"
    assert dice_definition.name == "roll_dice"
    assert dice_definition.enabled is True


async def test_dice_execute_returns_text():
    result = await dice_execute(ToolContext("掷骰子", "c1"), {"sides": 6, "count": 2})
    assert result.startswith("掷出：")
    assert "合计" in result
