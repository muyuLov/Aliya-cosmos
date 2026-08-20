"""FastAPI 应用装配：WS 网关路由"""

from __future__ import annotations

from fastapi import FastAPI

from agent.ws import create_ws_router


def create_app() -> FastAPI:
    """创建 Aliya Agent 服务应用。"""
    app = FastAPI(title="Aliya Agent")
    app.include_router(create_ws_router())
    return app
