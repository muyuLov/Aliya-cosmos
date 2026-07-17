"""AliyaAgent — 顶层 Agent 编排器

推理流程：
  brain_start + 进度推送
  → 查询记忆，注入工具描述 + 记忆 + 格式指令
  → LLM 首轮思考（JSON: reply + tool_calls）
  → 无工具：直接回复
  → 有工具：dispatch → 结果以临时消息注入（带 metadata 标记）→ refine
  → cleanup 临时消息，历史不残留
  → 记忆保存（含时间链和会话标识）
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from core.llm import ConversationService
from core.logger import get_logger
from agent.tools.base import ToolContext
from agent.tools.registry import ToolRegistry

logger = get_logger(__name__)

_PROGRESS_INTERVAL = 2.0
_MAX_REFINE = 3


@dataclass
class BrainResult:
    """LLM 思考结果"""

    reply: str
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = "stop"


# 注入到 system prompt 末尾，约束 LLM 输出格式
_TOOL_FORMAT_INSTRUCTION = """
你每次必须输出纯 JSON（不要用 markdown 代码块包裹），格式如下：
{
  "reply": "你对用户说的话",
  "tool_calls": [
    {"name": "工具名", "params": {"参数名": "参数值"}}
  ]
}
要求：
- "reply" 字段必填，是你的回复文本
- "tool_calls" 是可选的，不调用工具时省略
- 可以同时调用多个工具，它们会并行执行
- 工具执行结果会在下一轮反馈给你，你可以基于结果优化回复
"""

_TOOL_RESULT_MARKER = "tool_result"


def parse_llm_response(raw: str) -> BrainResult:
    """解析 LLM 输出的 JSON 字符串，含 fallback（代码块提取 → 正则兜底）"""
    raw = raw.strip()

    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return BrainResult(
                reply=_safe_str(data, "reply", ""),
                tool_calls=data.get("tool_calls", []),
                finish_reason="stop",
            )
    except json.JSONDecodeError:
        pass

    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict):
                return BrainResult(
                    reply=_safe_str(data, "reply", ""),
                    tool_calls=data.get("tool_calls", []),
                )
        except json.JSONDecodeError:
            pass

    reply_match = re.search(r'"reply"\s*:\s*"([^"]+)"', raw)
    reply = reply_match.group(1) if reply_match else raw
    tool_calls: list[dict] = []
    tc_match = re.search(r'"tool_calls"\s*:\s*(\[.*?\])', raw, re.DOTALL)
    if tc_match:
        try:
            parsed = json.loads(tc_match.group(1))
            if isinstance(parsed, list):
                tool_calls = parsed
        except json.JSONDecodeError:
            pass

    logger.debug("LLM 输出解析 fallback | reply_len=%d | tool_calls=%d", len(reply), len(tool_calls))
    return BrainResult(reply=reply, tool_calls=tool_calls, finish_reason="stop")


def _safe_str(data: dict, key: str, default: str) -> str:
    """从 dict 安全提取字符串字段"""
    v = data.get(key, default)
    return str(v) if v is not None else default


class AliyaAgent:
    """Agent 主编排器——直接管理 ConversationService"""

    def __init__(
        self,
        conversation_service: ConversationService,
        tool_registry: ToolRegistry,
        memory_manager: Any | None = None,
        send_message: Callable[[dict], Awaitable[None]] | None = None,
        tts_service: Any | None = None,
        audio_player: Any | None = None,
        audio_relay: Callable[[dict], Awaitable[None]] | None = None,
        max_refine: int = _MAX_REFINE,
    ) -> None:
        self._conv = conversation_service
        self._registry = tool_registry
        self._memory_manager = memory_manager
        self._send_message = send_message
        self._tts_service = tts_service
        self._audio_player = audio_player
        self._audio_relay = audio_relay
        self._max_refine = max_refine

        self._progress_task: asyncio.Task | None = None

    async def handle_user_message(self, text: str) -> None:
        await self._notify({"type": "brain_start"})
        self._progress_task = asyncio.create_task(self._push_progress())

        final_reply = ""

        try:
            tools_desc = self._registry.format_descriptions()

            instructions = tools_desc
            if instructions:
                instructions += "\n\n" + _TOOL_FORMAT_INSTRUCTION

            await self._conv.set_context_injection(tools=instructions)

            reply_text = await self._conv.asend(text)
            result = parse_llm_response(reply_text)

            final_reply = result.reply
            await self._notify({
                "type": "brain_complete",
                "reply": result.reply,
                **({"has_tool_calls": True} if result.tool_calls else {}),
            })

            if result.tool_calls:
                ctx = ToolContext(
                    tts_service=self._tts_service,
                    audio_player=self._audio_player,
                    memory_manager=self._memory_manager,
                    send_message=self._send_message,
                )

                for refine_round in range(self._max_refine):
                    tool_results = await self._registry.dispatch_all(result.tool_calls, ctx)
                    if not tool_results:
                        break

                    summary = self._registry.format_tool_summary(tool_results)
                    await self._conv.append_message(
                        "assistant", f"[工具执行结果]\n{summary}",
                        metadata={"injected": True, "prefix": _TOOL_RESULT_MARKER},
                    )

                    await self._notify({
                        "type": "brain_progress",
                        "message": f"根据工具结果优化回复（第 {refine_round + 1} 轮）",
                    })

                    result = await self._think_with_context(
                        "请根据以上工具执行结果生成最终回复。"
                    )

                    final_reply = result.reply
                    await self._notify({
                        "type": "brain_refine",
                        "reply": result.reply,
                    })

                    if not result.tool_calls:
                        break

                    logger.debug(
                        "refine 第 %d 轮仍有 %d 个工具调用",
                        refine_round + 1, len(result.tool_calls),
                    )

        except asyncio.CancelledError:
            logger.info("agent 处理被取消")
            raise
        except Exception as e:
            logger.error("agent 处理异常: %s", e, exc_info=True)
            await self._notify({"type": "brain_error", "message": str(e)})
        finally:
            if final_reply:
                await self._save_memory(text, final_reply)

            # 自动语音播放最终回复（失败不影响对话主流程）
            if final_reply and self._tts_service:
                await self._speak(final_reply)

            try:
                await self._conv.discard_messages(_TOOL_RESULT_MARKER, _MAX_REFINE + 1)
            except Exception:
                pass

            if self._progress_task and not self._progress_task.done():
                self._progress_task.cancel()
            self._progress_task = None

    async def _speak(self, text: str) -> None:
        """自动语音播放最终回复；任何异常都被吞掉，避免影响对话主流程。"""
        try:
            from agent.tools.tts_speak import speak_text

            ctx = ToolContext(
                tts_service=self._tts_service,
                audio_player=self._audio_player,
                send_message=self._send_message,
                audio_relay=self._audio_relay,
            )
            await speak_text(text, ctx)
        except Exception as e:
            _logger.warning("TTS 自动播放失败（已忽略）: %s", e)

    async def _think_with_context(self, text: str) -> BrainResult:
        """利用本轮已注入的 context injection 进行 LLM 调用"""
        reply_text = await self._conv.asend(text, store_history=True)
        return parse_llm_response(reply_text)

    async def handle_clear_history(self, confirm: bool = False) -> None:
        if confirm:
            reply = input("确认清空历史？(y/n): ").strip().lower()
            if reply != "y":
                return
        await self._conv.clear_history()
        logger.info("对话历史已清空")

    async def _save_memory(self, user_input: str, ai_reply: str) -> None:
        if not self._memory_manager or not hasattr(self._memory_manager, "add_conversation_memory"):
            return
        try:
            day_date = time.strftime("%Y-%m-%d")
            session_id = self._conv.conversation_id[:12]
            await self._memory_manager.add_conversation_memory(
                user_input, ai_reply,
                session_id=session_id,
                day_date=day_date,
                timeline="aliya|user",
            )
        except Exception as e:
            logger.warning("记忆保存失败: %s", e)

    async def _push_progress(self) -> None:
        while True:
            await asyncio.sleep(_PROGRESS_INTERVAL)
            await self._notify({"type": "brain_progress", "message": "思考中"})

    async def _notify(self, data: dict) -> None:
        if self._send_message:
            await self._send_message(data)
