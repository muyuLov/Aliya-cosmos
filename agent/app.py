"""FastAPI 应用装配：WS 网关路由 + 渠道路由"""

from __future__ import annotations

from fastapi import FastAPI

from agent.channels import build_channel_routers
from agent.ws import create_ws_router


def create_app() -> FastAPI:
    """创建 Aliya Agent 服务应用。"""
    app = FastAPI(title="Aliya Agent")
    app.include_router(create_ws_router())  # 本地 GUI 仍走 WS
    for router in build_channel_routers():  # 按 cosmos.channels.* 配置挂载已启用渠道
        app.include_router(router)
    return app
