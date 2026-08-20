"""测试 D1/D2：渠道配置读取、路由装配与应用挂载。"""

from __future__ import annotations

from agent.app import create_app
from agent.channels import build_channel_routers, load_channel_configs


def _config(enabled_feishu: bool = False, enabled_wechat: bool = False, **feishu_extra) -> dict:
    return {
        "feishu": {
            "enabled": enabled_feishu,
            "app_id": "app_1",
            "app_secret": "secret_1",
            "confirm_via_feishu": True,
            **feishu_extra,
        },
        "wechat": {
            "enabled": enabled_wechat,
            "corp_id": "corp_1",
            "secret": "secret_2",
            "agent_id": "1000002",
            "confirm_via_wechat": True,
        },
    }


def test_load_channel_configs_has_feishu_and_wechat():
    cfg = load_channel_configs()
    assert "feishu" in cfg
    assert "wechat" in cfg


def test_disabled_channels_no_routers(mocker):
    mocker.patch("agent.channels.load_channel_configs", return_value=_config())
    assert build_channel_routers() == []


def test_enabled_feishu_builds_router(mocker):
    mocker.patch("agent.channels.load_channel_configs", return_value=_config(enabled_feishu=True))
    routers = build_channel_routers()
    assert len(routers) == 1


def test_enabled_both_builds_routers(mocker):
    mocker.patch(
        "agent.channels.load_channel_configs",
        return_value=_config(enabled_feishu=True, enabled_wechat=True),
    )
    assert len(build_channel_routers()) == 2


def test_enabled_without_credentials_skipped(mocker):
    mocker.patch(
        "agent.channels.load_channel_configs",
        return_value=_config(enabled_feishu=True, app_id="", app_secret=""),
    )
    assert build_channel_routers() == []


def test_create_app_default_only_ws(mocker):
    mocker.patch("agent.channels.load_channel_configs", return_value=_config())
    app = create_app()
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/agent/ws" in paths
    assert "/channels/feishu" not in paths
    assert "/channels/wechat" not in paths


def test_create_app_mounts_enabled_channels(mocker):
    mocker.patch(
        "agent.channels.load_channel_configs",
        return_value=_config(enabled_feishu=True, enabled_wechat=True),
    )
    app = create_app()
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/agent/ws" in paths  # WS 网关保留
    assert "/channels/feishu" in paths
    assert "/channels/feishu/card" in paths
    assert "/channels/wechat" in paths
    assert "/channels/wechat/card" in paths
