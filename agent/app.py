"""FastAPI 应用装配：渠道 webhook 路由"""

from __future__ import annotations

from fastapi import FastAPI

from agent.channels import build_channel_routers


def create_app() -> FastAPI:
    """创建 Aliya Agent 服务应用。"""
    app = FastAPI(title="Aliya Agent")
    for router in build_channel_routers():  # 按 cosmos.channels.* 配置挂载已启用渠道
        app.include_router(router)
    return app
