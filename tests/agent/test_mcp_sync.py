"""测试 agent.mcp 启动同步：按配置连接服务器并注册工具，失败隔离。"""

from __future__ import annotations

from agent.mcp import load_mcp_specs, sync_mcp_servers
from agent.mcp.client import McpServerSpec, McpToolProxy
from agent.tools.registry import ToolRegistry


class _FakeConnector:
    """可注入的 connect_server 桩：按 spec.name 决定成功/失败。"""

    def __init__(self) -> None:
        self.calls: list[McpServerSpec] = []

    async def __call__(self, spec: McpServerSpec) -> list[McpToolProxy]:
        self.calls.append(spec)
        if spec.name == "broken":
            raise ConnectionError("连接失败")

        async def _invoke(args):
            return f"ok:{args.get('x', '')}"

        return [
            McpToolProxy(
                server=spec.name,
                name=f"tool_{i}",
                description="A",
                input_schema={"type": "object", "properties": {}},
                invoke=_invoke,
            )
            for i in (1, 2)
        ]


async def test_sync_mcp_servers_ok(monkeypatch):
    connector = _FakeConnector()
    monkeypatch.setattr("agent.mcp.connect_server", connector)

    reg = ToolRegistry()
    specs = [
        McpServerSpec(name="srv1", transport="stdio", command=["x"]),
        McpServerSpec(name="srv2", transport="stdio", command=["x"]),
    ]
    total, connected = await sync_mcp_servers(reg, specs)
    assert total == 4
    assert connected == ["srv1", "srv2"]
    names = {d.name for d in reg.enabled_definitions()}
    assert "mcp__srv1__tool_1" in names
    assert "mcp__srv2__tool_2" in names


async def test_sync_mcp_servers_failure_isolated(monkeypatch):
    connector = _FakeConnector()
    monkeypatch.setattr("agent.mcp.connect_server", connector)

    reg = ToolRegistry()
    specs = [
        McpServerSpec(name="broken", transport="stdio", command=["x"]),
        McpServerSpec(name="srv2", transport="stdio", command=["x"]),
    ]
    total, connected = await sync_mcp_servers(reg, specs)
    assert total == 2
    assert connected == ["srv2"]
    assert "broken" not in connected


def test_load_mcp_specs_filters_disabled(tmp_path):
    cfg_yaml = """\
cosmos:
  service:
    agent:
      mcp:
        config_path: %s
"""
    servers_json = """\
{
  "mcpServers": {
    "s1": {"command": "echo", "args": ["hi"]},
    "s2": {"type": "sse", "url": "http://localhost:9000/sse", "disabled": true}
  }
}
"""
    cfg_file = tmp_path / "mcp.yml"
    json_file = tmp_path / "MCPServers.json"
    cfg_file.write_text(cfg_yaml % str(json_file).replace("\\", "/"), encoding="utf-8")
    json_file.write_text(servers_json, encoding="utf-8")

    specs = load_mcp_specs(str(cfg_file))
    assert len(specs) == 1
    assert specs[0].name == "s1"
    assert specs[0].command == ["echo", "hi"]
    assert specs[0].transport == "stdio"


def test_load_mcp_specs_sse_inferred_from_url(tmp_path):
    """未显式声明 type 时按是否含 url 推断传输类型。"""
    cfg_yaml = """\
cosmos:
  service:
    agent:
      mcp:
        config_path: %s
"""
    servers_json = """\
{
  "mcpServers": {
    "remote": {"url": "http://localhost:9000/sse"}
  }
}
"""
    cfg_file = tmp_path / "mcp.yml"
    json_file = tmp_path / "MCPServers.json"
    cfg_file.write_text(cfg_yaml % str(json_file).replace("\\", "/"), encoding="utf-8")
    json_file.write_text(servers_json, encoding="utf-8")

    specs = load_mcp_specs(str(cfg_file))
    assert len(specs) == 1
    assert specs[0].name == "remote"
    assert specs[0].transport == "sse"
    assert specs[0].url == "http://localhost:9000/sse"


def test_load_mcp_specs_missing_file_safe(tmp_path):
    """JSON 文件不存在时安全降级为空列表。"""
    cfg_yaml = """\
cosmos:
  service:
    agent:
      mcp:
        config_path: %s
"""
    cfg_file = tmp_path / "mcp.yml"
    missing = tmp_path / "no_such.json"
    cfg_file.write_text(cfg_yaml % str(missing).replace("\\", "/"), encoding="utf-8")

    assert load_mcp_specs(str(cfg_file)) == []
