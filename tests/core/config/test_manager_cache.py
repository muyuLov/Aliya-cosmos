from core.config.manager import ConfigManager


async def test_env_resolution_cached_then_invalidated(monkeypatch):
    mgr = ConfigManager("data/config/main.yml")
    # 用实际含环境变量占位符的配置路径，默认端口 8765
    path = "cosmos.service.agent.ws_server.port"
    monkeypatch.setenv("WS_PORT", "9001")
    assert mgr.get(path) == "9001"
    monkeypatch.setenv("WS_PORT", "9002")
    # 未 reload 前应返回缓存旧值
    assert mgr.get(path) == "9001"
    mgr.reload()
    # reload 后失效，返回新值
    assert mgr.get(path) == "9002"
