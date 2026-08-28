"""验证向后兼容代码已移除

测试旧事件类型、旧方法、旧参数、旧别名已从代码库中移除。
"""

import pytest


def test_old_event_types_removed():
    """旧事件类型应已从 agent.events 模块移除"""
    import agent.events as mod

    assert not hasattr(mod, "StepStarted")
    assert not hasattr(mod, "StepFinished")
    assert not hasattr(mod, "ToolCallStart")
    assert not hasattr(mod, "ToolCallResult")
    assert not hasattr(mod, "ToolCallEnd")


def test_old_context_methods_removed():
    """旧 ContextBuilder 方法应已从 NarrativeContextBuilder 移除"""
    from agent.context import NarrativeContextBuilder

    builder = NarrativeContextBuilder()
    assert not hasattr(builder, "build_tool_system")
    assert not hasattr(builder, "build_mcp_system")
    assert not hasattr(builder, "build_soul_system")


def test_context_builder_alias_removed():
    """ContextBuilder 别名应已从 agent.context 模块移除"""
    import agent.context as mod

    assert not hasattr(mod, "ContextBuilder")


def test_inject_soul_context_removed():
    """inject_soul_context 函数应已从 agent.context 模块移除"""
    import agent.context as mod

    assert not hasattr(mod, "inject_soul_context")


def test_agent_loop_old_params_removed():
    """AgentLoop 构造函数应不再接受旧参数"""
    from agent.loop import AgentLoop
    from agent.context import NarrativeContextBuilder

    # 应能正常创建，不传旧参数
    loop = AgentLoop(context=NarrativeContextBuilder())
    assert loop is not None

    # 传入旧参数应引发 TypeError
    with pytest.raises(TypeError):
        AgentLoop(
            context=NarrativeContextBuilder(),
            service=None,
            registry=None,
            checker=None,
            emotion_engine=None,
        )


def test_logger_setup_no_dict_param():
    """logger.setup() 应不再接受配置字典参数（类型注解为 str | None）"""
    import inspect
    from core.logger import setup

    sig = inspect.signature(setup)
    param = sig.parameters["config"]

    # 类型注解应只允许 str | None，不再允许 dict
    annotation_str = str(param.annotation)
    assert "dict" not in annotation_str
    assert "str | None" in annotation_str or "Optional[str]" in annotation_str
