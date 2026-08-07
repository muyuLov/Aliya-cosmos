"""测试 AgentContext：统一依赖容器与 ToolContext 派生"""

from __future__ import annotations

from unittest.mock import MagicMock

from agent.context import AgentContext
from agent.config import AgentConfig
from agent.tools.base import ToolContext


def test_make_tool_context_derives_all_fields():
    tts = MagicMock()
    player = MagicMock()
    mem = MagicMock()
    confirm = MagicMock()
    perm = MagicMock()

    async def notify(_data: dict) -> None:
        pass

    ctx = AgentContext(
        conv=MagicMock(),
        registry=MagicMock(),
        config=AgentConfig(),
        prompt_manager=MagicMock(),
        style_switcher=MagicMock(),
        brain=MagicMock(),
        emotion=MagicMock(),
        cognition=None,
        memory_manager=mem,
        tts_service=tts,
        audio_player=player,
        notify=notify,
        confirm_callback=confirm,
        permission_config=perm,
    )
    tc = ctx.make_tool_context()
    assert isinstance(tc, ToolContext)
    assert tc.tts_service is tts
    assert tc.audio_player is player
    assert tc.memory_manager is mem
    assert tc.confirm_callback is confirm
    assert tc.permission_config is perm
    assert tc.send_message is notify
