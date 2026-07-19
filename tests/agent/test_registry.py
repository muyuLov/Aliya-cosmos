"""测试 ToolRegistry 分区执行逻辑与工具基础类型"""

from __future__ import annotations

import pytest

from agent.tools.base import ToolContext, ToolPermission, ToolResult
from agent.tools.registry import ToolRegistry, partition_tool_calls


class TestToolResult:
    def test_default_duration(self):
        r = ToolResult(success=True)
        assert r.duration == 0.0

    def test_with_duration(self):
        r = ToolResult(success=True, duration=1.5)
        assert r.duration == 1.5

    def test_success_result(self):
        r = ToolResult(success=True, data={"key": "value"})
        assert r.data["key"] == "value"

    def test_error_result(self):
        r = ToolResult(success=False, error="出错了")
        assert r.error == "出错了"


class TestToolPermission:
    def test_enum_values(self):
        assert ToolPermission.ALWAYS_ALLOW.value == "always_allow"
        assert ToolPermission.CONFIRM.value == "confirm"
        assert ToolPermission.NEVER_ALLOW.value == "never_allow"


class TestPartitionToolCalls:
    """测试 partition_tool_calls 分区逻辑"""

    def test_empty_input(self):
        result = partition_tool_calls([], {})
        assert result == []

    def test_all_safe_tools_in_one_batch(self):
        """所有工具都是只读安全的，应合并在一个批次中"""
        class SafeTool:
            name = "safe1"
            is_concurrency_safe = True

        class SafeTool2:
            name = "safe2"
            is_concurrency_safe = True

        tools = {"safe1": SafeTool(), "safe2": SafeTool2()}
        calls = [
            {"name": "safe1", "params": {}},
            {"name": "safe2", "params": {}},
        ]
        batches = partition_tool_calls(calls, tools)
        assert len(batches) == 1
        assert len(batches[0]) == 2

    def test_all_unsafe_each_own_batch(self):
        """不安全工具各自独占一批"""
        class UnsafeTool:
            name = "unsafe1"
            is_concurrency_safe = False

        class UnsafeTool2:
            name = "unsafe2"
            is_concurrency_safe = False

        tools = {"unsafe1": UnsafeTool(), "unsafe2": UnsafeTool2()}
        calls = [
            {"name": "unsafe1", "params": {}},
            {"name": "unsafe2", "params": {}},
        ]
        batches = partition_tool_calls(calls, tools)
        assert len(batches) == 2
        assert len(batches[0]) == 1
        assert len(batches[1]) == 1

    def test_mixed_safe_unsafe(self):
        """安全工具合并为一批，不安全工具各自独立"""
        class SafeTool:
            name = "read"
            is_concurrency_safe = True

        class UnsafeTool:
            name = "write"
            is_concurrency_safe = False

        tools = {"read": SafeTool(), "write": UnsafeTool()}
        calls = [
            {"name": "read", "params": {}},
            {"name": "write", "params": {}},
            {"name": "read", "params": {"extra": True}},
        ]
        batches = partition_tool_calls(calls, tools)
        # 预期：batch0 = [read (第一个)], batch1 = [write], batch2 = [read (第二个)]
        assert len(batches) == 3
        assert len(batches[0]) == 1
        assert batches[0][0].name == "read"
        assert len(batches[1]) == 1
        assert batches[1][0].name == "write"
        assert len(batches[2]) == 1
        assert batches[2][0].name == "read"

    def test_unknown_tool_falls_back_unsafe(self):
        """未注册工具默认为不安全"""
        tools = {}
        calls = [{"name": "unknown", "params": {}}]
        batches = partition_tool_calls(calls, tools)
        assert len(batches) == 1
        assert batches[0][0].name == "unknown"

    def test_safe_after_unsafe(self):
        """不安全工具之后的安全工具应进入新一批"""
        class SafeTool:
            name = "read"
            is_concurrency_safe = True

        class UnsafeTool:
            name = "write"
            is_concurrency_safe = False

        tools = {"read": SafeTool(), "write": UnsafeTool()}
        calls = [
            {"name": "write", "params": {}},
            {"name": "read", "params": {}},
        ]
        batches = partition_tool_calls(calls, tools)
        assert len(batches) == 2
        assert len(batches[0]) == 1
        assert batches[0][0].name == "write"
        assert len(batches[1]) == 1
        assert batches[1][0].name == "read"


class TestToolRegistry:
    """测试 ToolRegistry 基本功能"""

    def test_register_and_get(self):
        class MockTool:
            name = "test_tool"
            description = "测试工具"
            input_schema = {"type": "object", "properties": {}}
            is_concurrency_safe = True
            permission = ToolPermission.ALWAYS_ALLOW

            async def execute(self, params, ctx):
                return ToolResult(success=True)

            async def check_permissions(self, params, ctx):
                return True, None

        registry = ToolRegistry()
        registry.register(MockTool())
        assert registry.get("test_tool") is not None
        assert registry.get("nonexistent") is None

    def test_format_descriptions_with_safe_tool(self):
        class MockTool:
            name = "safe_read"
            description = "安全只读工具"
            input_schema = {"type": "object", "properties": {}}
            is_concurrency_safe = True
            permission = ToolPermission.ALWAYS_ALLOW

            async def execute(self, params, ctx):
                return ToolResult(success=True)

            async def check_permissions(self, params, ctx):
                return True, None

        registry = ToolRegistry()
        registry.register(MockTool())
        desc = registry.format_descriptions()
        assert "safe_read" in desc
        assert "🟢" in desc
        assert "只读" in desc

    def test_format_descriptions_with_unsafe_tool(self):
        class MockTool:
            name = "write_data"
            description = "写入工具"
            input_schema = {"type": "object", "properties": {}}
            is_concurrency_safe = False
            permission = ToolPermission.CONFIRM

            async def execute(self, params, ctx):
                return ToolResult(success=True)

            async def check_permissions(self, params, ctx):
                return True, None

        registry = ToolRegistry()
        registry.register(MockTool())
        desc = registry.format_descriptions()
        assert "write_data" in desc
        assert "🔴" in desc
        assert "写入" in desc

    def test_format_tool_summary_with_duration(self):
        r1 = ToolResult(success=True, data="完成", duration=1.5)
        summary = ToolRegistry().format_tool_summary([("tool_a", r1)])
        assert "1.5s" in summary

    def test_registry_lists_tools(self):
        class MockTool:
            name = "t1"
            is_concurrency_safe = True
            permission = ToolPermission.ALWAYS_ALLOW
            description = ""
            input_schema = {"type": "object", "properties": {}}

            async def execute(self, params, ctx):
                return ToolResult(success=True)

            async def check_permissions(self, params, ctx):
                return True, None

        registry = ToolRegistry()
        registry.register(MockTool())
        tools = registry.list()
        assert len(tools) == 1
        assert tools[0].name == "t1"
