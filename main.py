"""Aliya Agent — 启动入口

用法:
  source .venv/bin/activate    # 激活虚拟环境
  python main.py               启动 WebSocket 服务器（agent 在线程运行，WS 端口 8765）
  python main.py --chat        终端交互式聊天（无前端的直接模式）
"""

import asyncio
import os
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.routing import APIWebSocketRoute


def _check_venv() -> None:
    """检查虚拟环境是否已激活，未激活则尝试自动激活或提示。"""
    in_venv = sys.prefix != sys.base_prefix or os.environ.get("VIRTUAL_ENV")
    if in_venv:
        return

    venv_path = Path(__file__).parent / ".venv"
    if venv_path.is_dir():
        venv_python = venv_path / "bin" / "python"
        if venv_python.exists():
            os.execv(str(venv_python), [str(venv_python)] + sys.argv)

    raise SystemExit(
        "错误：未检测到虚拟环境。请先运行：\n"
        "  source .venv/bin/activate\n"
        "或使用虚拟环境的 Python 启动：\n"
        f"  {venv_path}/bin/python main.py"
    )


_check_venv()

from agent.agent import AliyaAgent
from agent.brain import BrainEngine
from agent.tools import ToolLoader
from agent.tools.advanced import MemoryQueryTool
from agent.tools.base import ToolCategory
from agent.ws_server import handle_connection
from core.config import get_config_instance
from core.logger import get_logger
from core.tts import create_from_config
from memory import get_memory_manager

logger = get_logger(__name__)


class ConsoleOutput:
    """终端输出适配器。作为同步可调用对象供 AliyaAgent 使用。"""

    def send_json(self, data: dict) -> None:
        msg_type = data.get("type")
        if msg_type == "brain_complete" or msg_type == "brain_refine" or msg_type == "reply":
            text = data.get("reply") or data.get("text") or ""
        elif msg_type == "tool_start":
            print(f"  [工具] {data.get('tool')} 执行中...")
            return
        elif msg_type == "tool_complete":
            status = data.get("status")
            tool = data.get("tool")
            if status == "success":
                print(f"  [工具] {tool} 完成")
            else:
                err = data.get("error", "")
                print(f"  [工具] {tool} 失败: {err}")
            return
        elif msg_type == "brain_error":
            print(f"\n[错误] {data.get('message', '')}")
            return
        elif msg_type == "tool_summary":
            ok = data.get("success", 0)
            fail = data.get("fail", 0)
            if fail:
                print(f"  [工具] 完成: {ok} 成功, {fail} 失败")
            return
        elif msg_type == "confirm_required":
            print(f"\n[确认] {data.get('message', '')}")
            return
        else:
            return
        print(f"\nAliya: {text}")

    __call__ = send_json


def _init_components(config_path: str = "data/config/main.yml"):
    """初始化并返回 ToolRegistry、BrainEngine、memory_manager。"""
    config = get_config_instance(config_path)
    timeout_seconds = float(config.get("cosmos.service.agent.tools.timeout_seconds", 30.0))
    top_k = int(config.get("cosmos.service.agent.grag.top_k", 5))
    tts_service, audio_player = create_from_config(config_path)
    memory_manager = get_memory_manager()

    registry = ToolLoader.build_default_registry(
        timeout_seconds=timeout_seconds,
        injections={
            "tts_service": tts_service,
            "audio_player": audio_player,
            "memory_manager": memory_manager,
        },
    )
    visible_categories = {ToolCategory.CORE}
    brain = BrainEngine.from_config(
        config_path=config_path,
        tool_descriptions=registry.format_descriptions(category_filter=visible_categories),
        memory_manager=memory_manager,
        max_iterations=int(config.get("cosmos.service.agent.brain.max_iterations", 5)),
        internal_tools={
            "memory_query": MemoryQueryTool(memory_manager),
        } if memory_manager is not None else {},
        visible_categories=visible_categories,
    )
    return registry, brain, memory_manager, tts_service, audio_player, top_k


async def chat_loop():
    """终端交互式聊天。"""
    registry, brain, memory_manager, tts_service, audio_player, top_k = _init_components()
    console = ConsoleOutput()

    async with tts_service, audio_player, brain:
        agent = AliyaAgent(
            brain=brain,
            tool_registry=registry,
            memory_manager=memory_manager,
            output=console,
            top_k=top_k,
        )
        print("Aliya 聊天模式（输入 /exit 退出, /clear 清空历史）\n")
        try:
            while True:
                try:
                    user_input = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: input("你: ")
                    )
                except (EOFError, KeyboardInterrupt, asyncio.CancelledError):
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

                await agent.handle_user_message(text)
                if agent._current_turn_task and not agent._current_turn_task.done():
                    try:
                        await agent._current_turn_task
                    except asyncio.CancelledError:
                        pass
        except asyncio.CancelledError:
            pass
        finally:
            agent.cancel_background_tasks()


def start_server():
    """启动 WebSocket 服务器。"""
    config = get_config_instance("data/config/main.yml")
    log_level = config.get("cosmos.logger.level", "info")
    max_iterations = config.get("cosmos.service.agent.brain.max_iterations", 5)
    top_k = config.get("cosmos.service.agent.grag.top_k", 5)
    timeout = config.get("cosmos.service.agent.tools.timeout_seconds", 30.0)
    ws_host = config.get("cosmos.service.agent.ws_server.host", "127.0.0.1")
    ws_port = int(config.get("cosmos.service.agent.ws_server.port", 8765))

    logger.info(
        "Agent启动 | ws=%s:%d | max_iter=%d | top_k=%d | timeout=%.1fs | log=%s",
        ws_host,
        ws_port,
        max_iterations,
        top_k,
        timeout,
        log_level,
    )

    app = FastAPI()
    app.router.routes.append(APIWebSocketRoute("/agent/ws", handle_connection))

    try:
        uvicorn.run(app, host=ws_host, port=ws_port, log_level=log_level)
    except Exception as e:
        logger.error("服务器启动失败: %s", e, exc_info=True)
        sys.exit(1)


def main():
    if "--chat" in sys.argv:
        asyncio.run(chat_loop())
    else:
        start_server()


if __name__ == "__main__":
    main()
