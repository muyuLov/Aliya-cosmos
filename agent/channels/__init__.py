"""渠道接入层：配置读取与渠道路由装配。

导出已启用渠道的 FastAPI 路由；渠道凭据/开关一律走配置
（cosmos.service.agent.channels.*），不硬编码、不额外写入密钥文件（对标 D7 凭据安全）。
"""

from __future__ import annotations

from fastapi import APIRouter

from agent.channels.feishu_client import FeishuClient
from agent.channels.feishu_router import create_feishu_router
from agent.channels.wechat_client import WeChatClient
from agent.channels.wechat_router import create_wechat_router
from core.config import get_config_instance

__all__ = [
    "FeishuClient",
    "WeChatClient",
    "create_feishu_router",
    "create_wechat_router",
    "load_channel_configs",
    "build_channel_routers",
]


def load_channel_configs() -> dict:
    """读取 cosmos.service.agent.channels.* 配置段。"""
    cfg = get_config_instance("data/config/main.yml")
    return cfg.get("cosmos.service.agent.channels") or {}


def build_channel_routers() -> list[APIRouter]:
    """按配置构造已启用渠道的路由（feishu/wechat）。

    渠道未启用或凭据缺失时安全跳过（不构造客户端、不报错）。
    """
    channels = load_channel_configs()
    routers: list[APIRouter] = []

    feishu = channels.get("feishu") or {}
    if feishu.get("enabled") and feishu.get("app_id") and feishu.get("app_secret"):
        client = FeishuClient(feishu["app_id"], feishu["app_secret"])
        routers.append(create_feishu_router(client, confirm=feishu.get("confirm_via_feishu", True)))

    wechat = channels.get("wechat") or {}
    if wechat.get("enabled") and wechat.get("corp_id") and wechat.get("secret"):
        client = WeChatClient(
            wechat["corp_id"],
            wechat["secret"],
            str(wechat.get("agent_id") or ""),
        )
        routers.append(create_wechat_router(client, confirm=wechat.get("confirm_via_wechat", True)))

    return routers
