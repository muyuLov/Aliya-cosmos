"""
Aliya Agent GUI Backend Launcher
启动 Agent WebSocket 服务器供 GUI 前端连接
"""
import asyncio
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

import uvicorn
from core.config import get_config_instance
from core.logger import get_logger
from agent.ws_server import handle_connection
from fastapi import FastAPI
from fastapi.routing import APIWebSocketRoute

logger = get_logger(__name__)


def main():
    """启动 Agent WebSocket 服务器"""
    config = get_config_instance("data/config/main.yml")

    log_level = config.get("cosmos.logger.level", "info")
    max_iterations = config.get("cosmos.service.agent.brain.max_iterations", 5)
    top_k = config.get("cosmos.service.agent.grag.top_k", 5)
    timeout = config.get("cosmos.service.agent.tools.timeout_seconds", 30.0)

    logger.info("Agent启动 | ws=127.0.0.1:8765 | max_iter=%d | top_k=%d | timeout=%.1fs | log=%s",
                max_iterations, top_k, timeout, log_level)

    app = FastAPI()
    app.router.routes.append(APIWebSocketRoute("/agent/ws", handle_connection))

    try:
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=8765,
            log_level=log_level,
        )
    except Exception as e:
        logger.error("WebSocket 服务器启动失败: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
