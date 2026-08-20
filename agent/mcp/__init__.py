"""MCP 启动同步：按配置连接服务器并注册工具。

服务器列表存于 JSON 文件（data/config/MCPServers.json，路径由
main.yml 的 cosmos.service.agent.mcp.config_path 指定），采用标准
mcpServers 对象格式（Claude Code 风格）：key 为服务器名，value 含
command/args/type/url/disabled/env/autoApprove 等字段。
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.mcp.adapter import register_mcp_server
from agent.mcp.client import McpServerSpec, connect_server
from agent.tools.registry import ToolRegistry
from core.config import get_config_instance
from core.logger import get_logger

logger = get_logger(__name__)


def load_mcp_specs(config_path: str = "data/config/main.yml") -> list[McpServerSpec]:
    """从 main.yml 指定的 JSON 文件读取 MCP 服务器配置（mcpServers 对象格式）。

    字段映射：
    - 传输类型：``type``（"stdio"/"sse"）；缺省按是否提供 ``url`` 推断（有 url→sse，否则 stdio）。
    - stdio 命令：``command``（字符串）+ ``args``（列表）拼接为 command 列表。
    - 开关：``disabled: true`` 的服务器被过滤，不参与启动连接。
    - ``env`` / ``autoApprove`` 当前不支持，忽略。

    文件不存在 / JSON 解析失败 / 格式错误时安全降级为空列表（不抛异常）。
    """
    cfg = get_config_instance(config_path)
    json_path = cfg.get("cosmos.service.agent.mcp.config_path")
    if not json_path:
        logger.warning("MCP 配置缺少 config_path，跳过")
        return []
    path = Path(json_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("MCP 配置文件不存在，跳过: %s", path)
        return []
    except json.JSONDecodeError as exc:
        logger.warning("MCP 配置文件解析失败，跳过: %s (%s)", path, exc)
        return []

    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        logger.warning("MCP 配置文件格式错误（期望含 mcpServers 对象）: %s", path)
        return []

    specs: list[McpServerSpec] = []
    for name, conf in servers.items():
        if not isinstance(conf, dict) or not name:
            logger.warning("MCP 配置项格式错误，跳过: %s", conf)
            continue
        if conf.get("disabled"):
            continue

        transport = conf.get("type") or ("sse" if conf.get("url") else "stdio")
        command = None
        if transport == "stdio" and conf.get("command"):
            command = [conf["command"]] + list(conf.get("args") or [])
        specs.append(
            McpServerSpec(
                name=name,
                transport=transport,
                command=command,
                url=conf.get("url"),
            )
        )
    return specs


async def sync_mcp_servers(
    registry: ToolRegistry, specs: list[McpServerSpec]
) -> tuple[int, list[str]]:
    """连接全部启用服务器，注册其工具。

    返回 (注册工具总数, 成功连接的服务器名列表)；失败服务器被隔离，不阻断其他服务器。
    """
    total = 0
    connected: list[str] = []
    for spec in specs:
        try:
            proxies = await connect_server(spec)
            register_mcp_server(registry, proxies)
            total += len(proxies)
            connected.append(spec.name)
            logger.info("MCP 服务器 %s 已注册 %d 个工具", spec.name, len(proxies))
        except Exception as e:
            logger.warning("MCP 服务器 %s 连接失败（跳过）: %s", spec.name, e)
    return total, connected
