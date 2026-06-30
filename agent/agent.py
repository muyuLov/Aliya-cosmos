from __future__ import annotations

import asyncio
import datetime
import time
from enum import Enum
from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from agent.brain import BrainEngine
from agent.cache import MemoryRetrievalCache, PerformanceMetrics, format_memory_list
from agent.models import AgentResponse, ToolCall, ToolProgress, ToolResult, compute_tool_signature
from core.logger import get_logger

if TYPE_CHECKING:
    from memory.memory_manager import GRAGMemoryManager

logger = get_logger(__name__)


class AliyaAgent:
    def __init__(
        self,
        brain: BrainEngine,
        tool_registry: Any,
        memory_manager: GRAGMemoryManager,
        output: Callable[[dict], None] | None = None,
        top_k: int = 5,
    ) -> None:
        self._brain = brain
        self._tool_registry = tool_registry
        self._memory_manager = memory_manager
        self._output = output or (lambda _: None)
        self._top_k = top_k
        self._current_turn_task: asyncio.Task | None = None
        self._memory_cache = MemoryRetrievalCache(max_size=50, ttl=300.0)
        self._metrics = PerformanceMetrics()
        self._background_tasks: set[asyncio.Task] = set()
        self._memory_semaphore = asyncio.Semaphore(5)

    async def __aenter__(self) -> "AliyaAgent":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """取消当前轮任务、所有后台任务，但不重复释放 brain（由外部 async with brain 负责）。"""
        if self._current_turn_task and not self._current_turn_task.done():
            self._current_turn_task.cancel()
        self.cancel_background_tasks()
        logger.debug("AliyaAgent 已释放资源")

    async def handle_user_message(self, user_input: str) -> None:
        # 新消息到来时抢占当前正在运行的轮次
        if self._current_turn_task and not self._current_turn_task.done():
            self._current_turn_task.cancel()
            try:
                await self._current_turn_task
            except asyncio.CancelledError:
                pass
        self._current_turn_task = asyncio.create_task(self._process_turn(user_input))

    async def handle_stop(self) -> None:
        if self._current_turn_task and not self._current_turn_task.done():
            self._current_turn_task.cancel()
            try:
                await self._current_turn_task
            except asyncio.CancelledError:
                pass
            self._current_turn_task = None
            await self._send_error("USER_CANCELLED", "user_cancelled", "用户已中断")

    async def handle_clear_history(self, confirm: bool = False) -> None:
        if not confirm:
            self._output(
                {
                    "type": "confirm_required",
                    "action": "clear_history",
                    "message": "确定要清空所有对话历史吗？",
                }
            )
            return
        await self._brain.clear_history()
        self._output({"type": "history_cleared", "message": "对话历史已清空"})

    async def handle_ping(self) -> None:
        self._output({"type": "pong"})

    async def handle_get_stats(self) -> None:
        self._output(
            {"type": "performance_stats", "metrics": self._metrics.to_dict()}
        )

    @staticmethod
    def _safe_data(data: Any, _depth: int = 0, _seen: set[int] | None = None) -> Any:
        """递归将 data 转换为 JSON 安全类型，防止 send_json 因不可序列化对象抛异常。"""
        if _depth > 50:
            return repr(data)
        if _seen is None:
            _seen = set()
        obj_id = id(data)
        if obj_id in _seen:
            return repr(data)
        _seen.add(obj_id)

        if data is None or isinstance(data, (bool, int, float, str)):
            return data
        if isinstance(data, (datetime.datetime, datetime.date)):
            return data.isoformat()
        if isinstance(data, Enum):
            return data.value
        if isinstance(data, bytes):
            return data.hex()
        if isinstance(data, dict):
            result: dict[str, Any] = {}
            for k, v in data.items():
                sk = str(k)
                if sk in result:
                    sk = f"{sk}_{type(k).__name__}"
                result[sk] = AliyaAgent._safe_data(v, _depth + 1, _seen)
            return result
        if isinstance(data, (list, tuple)):
            return [AliyaAgent._safe_data(v, _depth + 1, _seen) for v in data]
        return repr(data)

    # ── Turn 处理主流程 ───────────────────────────────────────────────────────

    async def _process_turn(self, user_input: str) -> None:
        t_start = time.monotonic()
        logger.info("==== Turn开始 | input=%.60s", user_input)

        self._output({"type": "brain_start", "user_input": user_input})
        memory_context = await self._retrieve_memory(user_input)
        self._output(
            {
                "type": "brain_progress",
                "step": "memory_retrieved",
                "detail": memory_context or "未检索到相关记忆",
            }
        )

        response = await self._execute_initial_think(user_input, memory_context)
        if response is None:
            return

        turn_prompt = response.prompt_tokens
        turn_completion = response.completion_tokens
        turn_total = response.total_tokens

        try:
            await self._send_brain_complete(response, turn_total)

            if not response.tool_calls:
                if turn_total > 0:
                    self._output({
                        "type": "token_usage",
                        "prompt_tokens": turn_prompt,
                        "completion_tokens": turn_completion,
                        "total_tokens": turn_total,
                    })
                logger.info("==== Turn完成 | tools=none | reply=%.60s | %dms",
                            response.reply_text, (time.monotonic() - t_start) * 1000)
                self._track_background_task(
                    asyncio.create_task(self._store_memory_async(user_input, response.reply_text))
                )
                return

            final_reply, turn_prompt, turn_completion, turn_total, tool_count = await self._dispatch_and_refine(
                user_input, memory_context, response, turn_prompt, turn_completion, turn_total
            )

            total_ms = (time.monotonic() - t_start) * 1000
            if turn_total > 0:
                self._output({
                    "type": "token_usage",
                    "prompt_tokens": turn_prompt,
                    "completion_tokens": turn_completion,
                    "total_tokens": turn_total,
                })
            logger.info("==== Turn完成 | tools=%d | reply=%.60s | %dms | tokens=%d",
                        tool_count, final_reply, total_ms, turn_total)
            self._track_background_task(
                asyncio.create_task(self._store_memory_async(user_input, final_reply))
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Turn异常 | error=%s", exc, exc_info=True)
            await self._send_error("TOOL_DISPATCH_FAILED", "tool_dispatch", str(exc))

    # ── Turn 阶段方法 ─────────────────────────────────────────────────────────

    async def _execute_initial_think(
        self, user_input: str, memory_context: str
    ) -> AgentResponse | None:
        """执行初始 LLM 推理，失败时发送错误并返回 None。"""
        try:
            return await self._brain.think(user_input, memory_context=memory_context)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Turn brain.think 失败 | error=%s", exc)
            await self._send_error("LLM_CALL_FAILED", "llm_thinking", str(exc))
            return None

    async def _send_brain_complete(self, response: AgentResponse, turn_total: int) -> None:
        self._output(
            {
                "type": "brain_complete",
                "reply": response.reply_text,
                "tool_calls": [
                    {"tool_name": call.tool_name, "arguments": call.arguments}
                    for call in response.tool_calls
                ],
            }
        )

    async def _dispatch_and_refine(
        self,
        user_input: str,
        memory_context: str,
        response: AgentResponse,
        turn_prompt: int,
        turn_completion: int,
        turn_total: int,
    ) -> tuple[str, int, int, int, int]:
        """并行分发工具并触发精炼循环，返回 (final_reply, prompt, completion, total, tool_count)。"""
        await self._send_tool_start(response.tool_calls)

        def _on_tool_progress(p: ToolProgress) -> None:
            self._output({
                "type": "tool_progress",
                "tool": p.tool_name,
                "progress_type": p.progress_type,
                "message": p.message,
                "progress": p.progress,
            })

        results = await self._tool_registry.dispatch(response.tool_calls, on_progress=_on_tool_progress)
        await self._send_tool_results_and_summary(results)

        final_reply = response.reply_text
        if not self._has_valid_tool_feedback(results):
            return final_reply, turn_prompt, turn_completion, turn_total, len(response.tool_calls)

        return await self._run_refine_loop(
            user_input, memory_context, response.reply_text, results,
            turn_prompt, turn_completion, turn_total, _on_tool_progress,
        )

    async def _send_tool_start(self, tool_calls: list[ToolCall]) -> None:
        for call in tool_calls:
            self._output(
                {"type": "tool_start", "tool": call.tool_name, "arguments": call.arguments}
            )

    async def _send_tool_results_and_summary(self, results: list[ToolResult]) -> None:
        """发送每个工具的完成事件并推送执行汇总。"""
        success_count = 0
        fail_count = 0
        error_summary: list[str] = []

        for result in results:
            payload: dict[str, Any] = {"type": "tool_complete", "tool": result.tool_name}
            if result.success:
                payload["status"] = "success"
                payload["result"] = self._safe_data(result.data)
                success_count += 1
            else:
                payload["status"] = "error"
                payload["error"] = result.error
                if result.error_code:
                    payload["error_code"] = result.error_code
                fail_count += 1
                code_tag = f" [{result.error_code}]" if result.error_code else ""
                error_summary.append(f"{result.tool_name}:{code_tag} {result.error}")
            self._output(payload)

        summary: dict[str, Any] = {
            "type": "tool_summary",
            "total": len(results),
            "success": success_count,
            "fail": fail_count,
        }
        if error_summary:
            summary["errors"] = error_summary
        self._output(summary)

    @staticmethod
    def _has_valid_tool_feedback(results: list[ToolResult]) -> bool:
        _skip = frozenset({"reply", "tts"})
        return any(r.tool_name not in _skip for r in results)

    @staticmethod
    def _build_tool_feedback(results: list[ToolResult]) -> str:
        """从工具执行结果构建可注入 LLM 上下文的反馈文本。"""
        _skip = frozenset({"reply", "tts"})
        lines: list[str] = []
        for r in results:
            if r.tool_name in _skip:
                continue
            if r.success:
                data_str = str(r.data)
                if len(data_str) > 500:
                    data_str = data_str[:500] + "..."
                lines.append(f"- {r.tool_name}: {data_str}")
            else:
                code_tag = f" [{r.error_code}]" if r.error_code else ""
                lines.append(f"- {r.tool_name}: (失败{code_tag}: {r.error})")
        return "\n".join(lines)

    async def _run_refine_loop(
        self,
        user_input: str,
        memory_context: str,
        initial_reply: str,
        dispatch_results: list[ToolResult],
        turn_prompt: int,
        turn_completion: int,
        turn_total: int,
        _on_tool_progress: Callable[[ToolProgress], None] | None = None,
    ) -> tuple[str, int, int, int, int]:
        """工具结果反馈循环（最多 3 轮），返回 (final_reply, prompt, completion, total, tool_count)。"""
        max_refine_rounds = 3
        final_reply = initial_reply
        all_feedbacks: list[str] = []
        prev_tool_signature: str | None = None
        tool_count = len(dispatch_results)

        for refine_round in range(max_refine_rounds):
            feedback = self._build_tool_feedback(dispatch_results)
            if not feedback:
                logger.debug("Refine 无有效结果，终止")
                break

            all_feedbacks.append(feedback)
            combined_feedback = "\n\n".join(all_feedbacks)

            logger.info("Refine第%d轮: 反馈 %d 行", refine_round + 1, feedback.count("\n") + 1)
            refined = await self._brain.think(
                user_input,
                memory_context=memory_context,
                tool_results=combined_feedback,
            )
            turn_prompt += refined.prompt_tokens
            turn_completion += refined.completion_tokens
            turn_total += refined.total_tokens

            curr_sig = compute_tool_signature(refined.tool_calls)
            if prev_tool_signature is not None and curr_sig == prev_tool_signature:
                logger.warning("Refine检测到工具循环: %s，终止",
                               [tc.tool_name for tc in refined.tool_calls])
                if refined.reply_text != final_reply:
                    final_reply = refined.reply_text
                    self._output({"type": "brain_refine", "reply": final_reply})
                break
            prev_tool_signature = curr_sig

            if not refined.tool_calls:
                if refined.reply_text != final_reply:
                    final_reply = refined.reply_text
                    self._output({"type": "brain_refine", "reply": final_reply})
                    logger.info("Refine完成: reply=%.60s", final_reply)
                break

            if refine_round == max_refine_rounds - 1:
                if refined.reply_text != final_reply:
                    final_reply = refined.reply_text
                    self._output({"type": "brain_refine", "reply": final_reply})
                remaining = [tc for tc in refined.tool_calls if tc.tool_name != "reply"]
                if remaining:
                    logger.warning("Refine达到上限 %s | 未执行: %s",
                                   max_refine_rounds, [tc.tool_name for tc in remaining])
                break

            next_tool_calls = [tc for tc in refined.tool_calls if tc.tool_name != "reply"]
            if not next_tool_calls:
                break

            logger.info("Refine第%d轮: 新工具 %s",
                        refine_round + 1, [tc.tool_name for tc in next_tool_calls])

            tool_count += len(next_tool_calls)

            for call in next_tool_calls:
                self._output({
                    "type": "tool_start", "tool": call.tool_name, "arguments": call.arguments,
                })
            next_results = await self._tool_registry.dispatch(next_tool_calls, on_progress=_on_tool_progress)
            for result in next_results:
                payload: dict[str, Any] = {
                    "type": "tool_complete",
                    "tool": result.tool_name,
                }
                if result.success:
                    payload["status"] = "success"
                    payload["result"] = self._safe_data(result.data)
                else:
                    payload["status"] = "error"
                    payload["error"] = str(result.error)
                    if result.error_code:
                        payload["error_code"] = result.error_code
                self._output(payload)

            dispatch_results = next_results

        return final_reply, turn_prompt, turn_completion, turn_total, tool_count

    # ── 记忆与任务管理 ─────────────────────────────────────────────────────────

    async def _store_memory_async(self, user_input: str, response_text: str) -> None:
        """异步存储对话记忆到知识图谱

        分别存储到两条平行时间链：
        - 用户时间链（现实时间，如 2026-06-30）
        - Aliya 时间链（游戏内未来时间，如 3026-06-30）
        """
        today = datetime.date.today()
        user_day = today.strftime("%Y-%m-%d")
        # Aliya 时间链 = 现实时间 + 1000 年
        aliya_day = today.replace(year=today.year + 1000).strftime("%Y-%m-%d")

        async with self._memory_semaphore:
            try:
                # 存储到用户时间链
                await self._memory_manager.add_conversation_memory(
                    user_input, response_text, day_date=user_day, timeline="user"
                )
                # 存储到 Aliya 时间链
                await self._memory_manager.add_conversation_memory(
                    user_input, response_text, day_date=aliya_day, timeline="aliya"
                )
            except Exception as exc:
                logger.warning("异步记忆存储失败：%s", exc)

    def _track_background_task(self, task: asyncio.Task) -> None:
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def cancel_background_tasks(self) -> None:
        for task in list(self._background_tasks):
            task.cancel()

    async def _retrieve_memory(self, user_input: str) -> str:
        start_time = asyncio.get_running_loop().time()

        cached = self._memory_cache.get(user_input, self._top_k)
        if cached is not None:
            self._metrics.record_cache_hit()
            return self._format_memory_context(cached)

        self._metrics.record_cache_miss()

        try:
            memories = await self._memory_manager.get_relevant_memories(
                user_input, limit=self._top_k
            )
            self._memory_cache.set(user_input, self._top_k, memories)

            elapsed_ms = (asyncio.get_running_loop().time() - start_time) * 1000
            self._metrics.record_retrieval(elapsed_ms)

            return self._format_memory_context(memories)
        except Exception as e:
            logger.warning("记忆检索失败：%s", e)
            return ""

    def _format_memory_context(self, memories: list) -> str:
        return format_memory_list(memories, empty_text="(无相关记忆)")

    async def _send_error(self, code: str, step: str, message: str) -> None:
        self._output(
            {
                "type": "brain_error",
                "code": code,
                "step": step,
                "message": message,
            }
        )
