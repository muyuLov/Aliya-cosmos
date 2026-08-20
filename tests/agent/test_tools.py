"""测试工具系统：ToolDefinition/ToolContext/ToolRegistry/权限检查/内置工具"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.tools import Permission, PermissionChecker, ToolContext, ToolDefinition, ToolRegistry
from agent.tools.builtin import register_builtin_tools


class TestToolDefinition:
    def test_defaults(self):
        d = ToolDefinition(id="t1", name="tool1", description="desc", input_schema={})
        assert d.id == "t1"
        assert d.enabled is True
        assert d.risk == "safe"

    def test_risk_override(self):
        d = ToolDefinition(id="t", name="n", description="d", input_schema={}, risk="high")
        assert d.risk == "high"

    def test_frozen(self):
        d = ToolDefinition(id="t", name="n", description="d", input_schema={})
        with pytest.raises(AttributeError):
            setattr(d, "risk", "high")


class TestToolContext:
    def test_defaults(self):
        ctx = ToolContext(user_query="hi", conversation_id="c1")
        assert ctx.memory is None

    def test_with_memory(self):
        ctx = ToolContext(user_query="hi", conversation_id="c1", memory="mem")
        assert ctx.memory == "mem"


class TestToolRegistry:
    def _make(self):
        reg = ToolRegistry()
        d = ToolDefinition(id="t1", name="tool1", description="desc", input_schema={"type": "object"})
        async def exec(_ctx, args):
            return f"result:{args.get('x')}"
        reg.register(d, exec)
        return reg

    def test_build_tools_schema(self):
        reg = self._make()
        schema = reg.build_tools_schema()
        assert schema == [
            {
                "type": "function",
                "function": {
                    "name": "tool1",
                    "description": "desc",
                    "parameters": {"type": "object"},
                },
            }
        ]

    def test_enabled_definitions_filter(self):
        reg = self._make()
        disabled = ToolDefinition(id="t2", name="tool2", description="d", input_schema={}, enabled=False)
        async def noop(_ctx, _args):
            return ""
        reg.register(disabled, noop)
        assert [d.id for d in reg.enabled_definitions()] == ["t1"]

    @pytest.mark.asyncio
    async def test_execute_calls_executor(self):
        reg = self._make()
        result = await reg.execute("t1", ToolContext("hi", "c1"), {"x": 1})
        assert result == "result:1"

    @pytest.mark.asyncio
    async def test_execute_unregistered(self):
        reg = self._make()
        result = await reg.execute("missing", ToolContext("hi", "c1"), {})
        assert "工具未注册" in result


class TestPermissionChecker:
    def _write(self, tmp_path, content: str):
        p = tmp_path / "Permissions.yml"
        p.write_text(content, encoding="utf-8")
        return str(p)

    def test_allow_confirm_deny_from_config(self, tmp_path):
        path = self._write(
            tmp_path,
            "tools:\n  safe_tool: always_allow\n  warn_tool: confirm\n  bad_tool: never_allow\n",
        )
        checker = PermissionChecker(path)
        assert checker.check("safe_tool") == Permission.ALLOW
        assert checker.check("warn_tool") == Permission.CONFIRM
        assert checker.check("bad_tool") == Permission.DENY

    def test_default_by_risk(self, tmp_path):
        path = self._write(tmp_path, "tools: {}\n")
        checker = PermissionChecker(path)
        assert checker.check("unlisted", risk="safe") == Permission.ALLOW
        assert checker.check("unlisted", risk="medium") == Permission.CONFIRM
        assert checker.check("unlisted", risk="high") == Permission.CONFIRM

    def test_config_overrides_default(self, tmp_path):
        path = self._write(tmp_path, "tools:\n  risky: always_allow\n")
        checker = PermissionChecker(path)
        assert checker.check("risky", risk="high") == Permission.ALLOW

    def test_missing_file_falls_back(self, tmp_path):
        checker = PermissionChecker(str(tmp_path / "missing.yml"))
        assert checker.check("anything", risk="safe") == Permission.ALLOW


class TestBuiltinTools:
    @pytest.mark.asyncio
    async def test_get_current_time(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        ctx = ToolContext("hi", "c1")
        result = await reg.execute("get_current_time", ctx, {})
        # "YYYY-MM-DD HH:MM:SS[ 时区]"；无时区时尾部含空格
        assert result.startswith("2026")

    @pytest.mark.asyncio
    async def test_memory_query_with_mock(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        mem = MagicMock()
        mem.get_relevant_memories = AsyncMock(return_value=[("Kane", "人物", "喜欢", "太空", "概念")])
        ctx = ToolContext("hi", "c1", memory=mem)
        result = await reg.execute("memory_query", ctx, {"query": "爱好"})
        assert "Kane" in result
        mem.get_relevant_memories.assert_awaited_once_with(query="爱好", limit=3)

    @pytest.mark.asyncio
    async def test_memory_query_no_results(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        mem = MagicMock()
        mem.get_relevant_memories = AsyncMock(return_value=[])
        result = await reg.execute("memory_query", ToolContext("hi", "c1", memory=mem), {"query": "x"})
        assert "没有找到相关记忆" in result

    @pytest.mark.asyncio
    async def test_query_recent_conversation(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        mem = MagicMock()
        mem.query_memory = AsyncMock(return_value="我们上次聊了太空")
        result = await reg.execute("query_recent_conversation", ToolContext("hi", "c1", memory=mem), {"question": "最近聊啥"})
        assert result == "我们上次聊了太空"

    @pytest.mark.asyncio
    async def test_query_recent_no_answer(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        mem = MagicMock()
        mem.query_memory = AsyncMock(return_value=None)
        result = await reg.execute("query_recent_conversation", ToolContext("hi", "c1", memory=mem), {"question": "q"})
        assert result == "[无相关记忆]"

    def test_registry_has_all_builtin_tools(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        names = {d.name for d in reg.enabled_definitions()}
        assert names == {
            "get_current_time",
            "memory_query",
            "query_recent_conversation",
            "search_knowledge",
            "roll_dice",
        }
