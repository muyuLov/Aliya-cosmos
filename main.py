"""Aliya Agent — 启动入口

用法:
  source .venv/bin/activate    # 激活虚拟环境
  python main.py               启动 WebSocket 服务器（agent 在线程运行，WS 端口 8765）
  python main.py --chat        终端交互式聊天（无前端的直接模式）
  python main.py --chat-ws     终端交互式聊天 + WS 广播（GUI 可连接）
"""

import asyncio
import json
import sys
from contextlib import nullcontext as _nullctx
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.routing import APIWebSocketRoute

from agent.agent import AliyaAgent, agent_config_from_yaml
from agent.tools.registry import ToolRegistry
from agent.tools.reply import ReplyTool
from agent.tools.memory_query import MemoryQueryTool
from agent.ws import create_handler
from core.config import get_config_instance
from core.llm import create_from_config as create_llm
from core.logger import get_logger
from core.memory import get_memory_manager
from core.tts import create_from_config as create_tts

logger = get_logger(__name__)


class ConsoleOutput:
    """终端输出适配器，将 agent 通知渲染到控制台"""

    async def send_json(self, data: dict) -> None:
        msg_type = data.get("type", "")
        if msg_type in ("brain_complete", "brain_refine", "reply"):
            text = data.get("reply") or data.get("text") or ""
            print(f"\nAliya: {text}")
        elif msg_type == "brain_start":
            print("\r思考中...", end="", flush=True)
        elif msg_type == "brain_progress":
            print(f"\r思考中（{data.get('message', '')}）", end="", flush=True)
        elif msg_type == "tool_start":
            print(f"\n  [工具] {data.get('tool')} 执行中...")
        elif msg_type == "tool_complete":
            tool = data.get("tool")
            if data.get("status") == "success":
                print(f"  [工具] {tool} 完成")
            else:
                print(f"  [工具] {tool} 失败: {data.get('error', '')}")
        elif msg_type == "brain_error":
            print(f"\n[错误] {data.get('message', '')}")
        elif msg_type == "tool_summary":
            fail = data.get("fail", 0)
            if fail:
                print(f"  [工具] 完成: {data.get('success', 0)} 成功, {fail} 失败")

    __call__ = send_json


class WSBroadcast:
    """管理 WebSocket 客户端列表，广播 agent 通知。"""

    def __init__(self) -> None:
        self._clients: set[Any] = set()
        self._agent: Any = None

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def set_agent(self, agent: Any) -> None:
        """注入 agent 引用，用于响应查询类消息"""
        self._agent = agent

    def register(self, websocket: Any) -> None:
        """注册一个新的 WS 客户端"""
        self._clients.add(websocket)
        logger.info("WS 客户端已注册 | 当前连接数=%d", len(self._clients))

    def unregister(self, websocket: Any) -> None:
        """移除断开的 WS 客户端"""
        self._clients.discard(websocket)
        logger.info("WS 客户端已注销 | 剩余连接数=%d", len(self._clients))

    async def send_json(self, data: dict) -> None:
        """广播消息给所有已连接的 WS 客户端"""
        if not self._clients:
            return
        dead: set[Any] = set()
        for ws in self._clients:
            try:
                await ws.send_json(data)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self._clients.discard(ws)


class MultiOutput:
    """将消息同时发送给多个输出目标。"""

    def __init__(self, *targets: Any) -> None:
        self._targets = targets

    async def send_json(self, data: dict) -> None:
        for t in self._targets:
            try:
                await t.send_json(data)
            except Exception:
                pass

    __call__ = send_json


def _init_components(config_path: str = "data/config/main.yml"):
    registry = ToolRegistry()
    registry.register(ReplyTool())
    registry.register(MemoryQueryTool())

    conversation_service = create_llm(config_path=config_path)

    tts_service: Any | None = None
    audio_player: Any | None = None
    try:
        tts_service, audio_player = create_tts(config_path=config_path)
    except Exception as e:
        logger.warning("TTS 初始化失败（跳过）: %s", e)

    return registry, tts_service, audio_player, conversation_service


def _get_memory_manager():
    mm = get_memory_manager()
    return mm if mm.enabled else None


async def _run_ws_broadcast_server(broadcast: WSBroadcast, conversation_service: Any) -> None:
    """后台运行 WS 广播服务器。

    提供一个法 WebSocket 端点 /agent/ws，接收方 WS 客户端并广播 agent 通知，
    同时响应基本的查询类消息（get_feeling_scores / get_token_usage / ping）。
    """
    from fastapi import WebSocket, WebSocketDisconnect

    config = get_config_instance("data/config/main.yml")
    ws_host = config.get("cosmos.service.agent.ws_server.host", "127.0.0.1")
    ws_port = int(config.get("cosmos.service.agent.ws_server.port", 8765))

    app = FastAPI()

    @app.websocket("/agent/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        broadcast.register(websocket)
        try:
            async for data in websocket.iter_json():
                msg_type = data.get("type", "")
                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                elif msg_type == "get_feeling_scores":
                    agent = broadcast._agent
                    if agent:
                        scores = agent.get_feeling_scores()
                        await websocket.send_json({
                            "type": "feeling_scores",
                            "scores": scores,
                            "dominant": agent.get_emotion(),
                        })
                elif msg_type == "get_token_usage":
                    if hasattr(conversation_service, 'usage'):
                        usage = conversation_service.usage
                        await websocket.send_json({
                            "type": "token_usage",
                            "total": usage.total_tokens,
                            "input": usage.prompt_tokens,
                            "output": usage.completion_tokens,
                        })
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            broadcast.unregister(websocket)

    logger.info("WS 广播服务器启动 | ws=%s:%d", ws_host, ws_port)
    server = uvicorn.Server(uvicorn.Config(app, host=ws_host, port=ws_port, log_level="warning"))
    await server.serve()


async def chat_loop(ws_enabled: bool = False):
    """终端交互式聊天。"""
    registry, tts_service, audio_player, conversation_service = _init_components()
    memory_manager = _get_memory_manager()
    console = ConsoleOutput()

    # ── WS 广播初始化 ──
    broadcast: WSBroadcast | None = None
    ws_server_task: asyncio.Task | None = None
    if ws_enabled:
        broadcast = WSBroadcast()
        ws_server_task = asyncio.create_task(
            _run_ws_broadcast_server(broadcast, conversation_service)
        )

    async with tts_service or _nullctx(), audio_player or _nullctx():
        agent_config = agent_config_from_yaml()

        async def _console_confirm(tool_name: str, params: dict) -> bool:
            loop = asyncio.get_running_loop()
            param_preview = json.dumps(params, ensure_ascii=False)[:120]
            print(f"\n[权限确认] 工具 `{tool_name}` 请求执行")
            print(f"  参数: {param_preview}")
            reply = await loop.run_in_executor(
                None, lambda: input("是否允许执行？(y/N): ").strip().lower()
            )
            return reply == "y"

        # 组合输出：终端 + WS 广播
        targets: list[Any] = [console]
        if broadcast:
            targets.append(broadcast)
        output = MultiOutput(*targets)

        agent = AliyaAgent(
            conversation_service=conversation_service,
            tool_registry=registry,
            memory_manager=memory_manager,
            send_message=output.send_json,
            tts_service=tts_service,
            audio_player=audio_player,
            audio_relay=broadcast.send_json if broadcast else None,
            config=agent_config,
            confirm_callback=_console_confirm,
        )

        # WS 广播模式下，将 agent 引用注入广播器以便响应查询
        if broadcast:
            broadcast.set_agent(agent)

        mode_hint = " + WS 广播" if ws_enabled else ""
        print(f"Aliya 聊天模式{mode_hint}（输入 /exit 退出, /clear 清空历史）\n")
        try:
            while True:
                try:
                    user_input = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: input("你: ")
                    )
                except (EOFError, KeyboardInterrupt):
                    print()
                    break

                text = user_input.strip()
                if not text:
                    continue
                if text == "/exit":
                    break
                if text == "/clear":
                    await agent.handle_clear_history(confirm=True)
                    continue

                try:
                    await agent.handle_user_message(text)
                except Exception as e:
                    logger.error("聊天循环异常: %s", e, exc_info=True)
                    print(f"\n[错误] 处理消息时发生异常: {e}")
        finally:
            if ws_server_task and not ws_server_task.done():
                ws_server_task.cancel()
                try:
                    await ws_server_task
                except asyncio.CancelledError:
                    pass


def start_server():
    """启动 WebSocket 服务器。"""
    config = get_config_instance("data/config/main.yml")
    log_level = config.get("cosmos.logger.level", "info")
    ws_host = config.get("cosmos.service.agent.ws_server.host", "127.0.0.1")
    ws_port = int(config.get("cosmos.service.agent.ws_server.port", 8765))

    logger.info(
        "WS 服务启动 | ws=%s:%d | log=%s",
        ws_host,
        ws_port,
        log_level,
    )

    memory_manager, tts_service, audio_player = _init_server_deps()

    def _conversation_factory():
        return create_llm(config_path="data/config/main.yml")

    handler = create_handler(
        conversation_service_factory=_conversation_factory,
        tts_service=tts_service,
        memory_manager=memory_manager,
        audio_player=audio_player,
    )

    app = FastAPI()
    app.router.routes.append(APIWebSocketRoute("/agent/ws", handler))

    try:
        uvicorn.run(app, host=ws_host, port=ws_port, log_level=log_level)
    except Exception as e:
        logger.error("服务器启动失败: %s", e, exc_info=True)
        sys.exit(1)


def _init_server_deps(config_path: str = "data/config/main.yml"):
    tts_service: Any | None = None
    audio_player: Any | None = None
    try:
        tts_service, audio_player = create_tts(config_path=config_path)
    except Exception as e:
        logger.warning("TTS 初始化失败（跳过）: %s", e)

    memory_manager = get_memory_manager()
    if not memory_manager.enabled:
        memory_manager = None

    return memory_manager, tts_service, audio_player


def main():
    # 加载日志配置（必须在任何 logger 使用前调用，否则 debug 配置不生效）
    from core.logger import setup as setup_logger
    setup_logger("data/config/main.yml")

    if "--chat-ws" in sys.argv:
        asyncio.run(chat_loop(ws_enabled=True))
    elif "--chat" in sys.argv:
        asyncio.run(chat_loop())
    else:
        start_server()


if __name__ == "__main__":
    main()
