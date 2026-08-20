"""Aliya Agent 服务入口：uvicorn 启动 WS 网关

运行方式：
    python -m agent.main
或
    uvicorn agent.app:create_app --factory --host 127.0.0.1 --port 8765
"""

from __future__ import annotations

import argparse

import uvicorn

from core.config import get_config_instance
from core.logger import get_logger

logger = get_logger(__name__)


def get_ws_server_config() -> tuple[str, int]:
    """从 main.yml 读取 agent WS 服务监听配置（host / port）。"""
    cfg = get_config_instance("data/config/main.yml")
    section = cfg.get("cosmos.service.agent.ws_server") or {}
    host = str(section.get("host", "127.0.0.1"))
    try:
        port = int(section.get("port", 8765))
    except (TypeError, ValueError):
        port = 8765
    return host, port


def main() -> None:
    """启动 Agent WS 服务。"""
    parser = argparse.ArgumentParser(description="Aliya Agent 服务")
    parser.add_argument("--host", default=None, help="监听地址（默认读配置）")
    parser.add_argument("--port", type=int, default=None, help="监听端口（默认读配置）")
    args = parser.parse_args()

    host, port = get_ws_server_config()
    host = args.host or host
    port = args.port or port

    logger.info("启动 Aliya Agent 服务 | host=%s port=%d", host, port)
    uvicorn.run("agent.app:create_app", factory=True, host=host, port=port)


if __name__ == "__main__":
    main()
