"""Brain — LLM 交互层

负责 Agent 与 LLM 之间的全部交互：

- think / think_with_context：工具阶段思考（含超时控制与连续超时降级）
- generate_soul_reply：灵魂阶段回复（含 JSON 前缀净化链）
- force_summary_reply：异常时的强制总结兜底
- compress_conversation：超长对话历史摘要化
- parse_llm_response / BrainResult：LLM 输出解析

Brain 不持有 Agent 的编排状态，只维护自身调用计数与连续超时计数。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

from core.llm import ConversationService
from core.llm.models import ChatRequest
from core.logger import get_logger

from agent.config import AgentConfig

logger = get_logger(__name__)


@dataclass
class BrainResult:
    """LLM 思考结果

    Attributes:
        reply: 最终回复文本
        tool_calls: 工具调用列表
        thought: 思维链推理过程（原生 thinking 或 prompt CoT 均有值）
        finish_reason: 结束原因
        turn: 当前轮次
    """
    reply: str
    tool_calls: list[dict] = field(default_factory=list)
    thought: str = ""
    finish_reason: str = "stop"
    turn: int = 0


# ── 常量 ──────────────────────────────────────────────────────────────────────

# 压缩后保留的最新消息条数（压缩阈值由 AgentConfig.compression_threshold 决定）
_COMPRESSION_KEEP = 10

# 继续推理的 CoT prompt（模块级常量，避免每次循环重建字符串）
_THINK_WITH_CONTEXT_COT_PROMPT = (
    "我收到了工具执行结果。\n"
    "\n"
    "## 推理链（在 thought 中依次完成）\n"
    "\n"
    "步骤1 ─ 结果分析\n"
    "  - 每个工具的执行结果是什么？\n"
    "  - 结果是否满足了我的信息需求？\n"
    "\n"
    "步骤2 ─ 缺口判断\n"
    "  - 现有信息是否足以给出完整回复？\n"
    "  - 是 → 直接进入步骤3\n"
    "  - 否 → 还需要什么信息？应调用的工具？\n"
    "\n"
    "步骤3 ─ 行动决策（终点）\n"
    "  - 信息足够 → reply = 最终回复\n"
    "  - 信息不足 → tool_calls = 继续获取\n"
)

# 灵魂阶段回复生成 prompt
_SOUL_PHASE_PROMPT = (
    "请基于以上对话历史和工具执行结果，以 Aliya 的身份生成自然亲切的最终回复。\n"
    "要求：\n"
    "1. 直接输出回复正文，禁止输出 JSON、禁止引用或解释工具、禁止输出思考过程。\n"
    "2. 回复简短自然，日常聊天控制在 3-5 句话、100 字以内，像发微信消息。\n"
    "3. 先回应当前这句话里对方的情绪，再自然延续话题，可以自然地融入记忆中提到的相关事实。\n"
    "4. 不要说\"你能再说一遍吗\"\"让我想想\"之类的敷衍话。"
)

# 强制总结 prompt（当 LLM 完全不可用时兜底使用）
_FORCE_SUMMARY_PROMPT = (
    "请对以上对话进行简要总结，然后给出一个自然友好的回复。"
    "请直接输出回复文本。"
)

# 最终降级回复（所有兜底都失败时使用）
# 注意：避免使用"你能再说一遍吗"式敷衍话（灵魂阶段 prompt 明确禁止）。
_FALLBACK_REPLY = "嗯……我好像有点卡住了，不过没关系，我会继续陪着你。你愿意再跟我说说吗？"

# 对话压缩 prompt
_COMPRESSION_PROMPT = (
    "请用简洁中文总结以下对话的关键信息（保留所有事实、用户偏好、约定、重要情感记忆），"
    "去掉冗余的寒暄和无关细节：\n\n{text}"
)


# ── 输出解析 ──────────────────────────────────────────────────────────────────


def _safe_str(data: dict[str, Any], key: str, default: str) -> str:
    v = data.get(key, default)
    return str(v) if v is not None else default


def _find_json_boundary(text: str) -> int:
    """找到以 ``{`` 开头的第一个完整 JSON 对象的结束位置。

    从开头扫描，跟踪大括号深度，当深度回零时返回结束索引。
    未找到完整对象时返回 -1。
    """
    depth = 0
    for i, ch in enumerate(text):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def parse_llm_response(raw: str) -> BrainResult:
    """解析 LLM 输出的 JSON 字符串，含多层 fallback。

    LLM 预期输出格式：
    {"thought": "推理过程...", "reply": "回复文本", "tool_calls": [...]}

    Fallback 链：直接 JSON → 代码块提取 → 正则兜底
    """
    raw = raw.strip()

    # 第 1 层：直接 JSON 解析
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return BrainResult(
                reply=_safe_str(data, "reply", ""),
                tool_calls=data.get("tool_calls", []),
                thought=_safe_str(data, "thought", ""),
                finish_reason="stop",
            )
    except json.JSONDecodeError:
        pass

    # 第 1.5 层：以 { 开头但非完整 JSON → 尝试剥离头部 JSON 对象
    # LLM 有时会输出 {"tool_calls": [], "reply": ""}\n\n自然语言正文...
    if raw.startswith("{"):
        end = _find_json_boundary(raw)
        if end > 0:
            json_part = raw[:end + 1]
            rest = raw[end + 1:].strip()
            try:
                data = json.loads(json_part)
                if isinstance(data, dict):
                    # JSON 中的 reply 非空 → 优先用 JSON 的 reply
                    reply_from_json = _safe_str(data, "reply", "")
                    if reply_from_json:
                        return BrainResult(
                            reply=reply_from_json,
                            tool_calls=data.get("tool_calls", []),
                            thought=_safe_str(data, "thought", ""),
                        )
                    # JSON 中 reply 为空但尾部有自然语言正文 → 用尾部正文
                    if rest:
                        return BrainResult(
                            reply=rest,
                            tool_calls=data.get("tool_calls", []),
                            thought=_safe_str(data, "thought", ""),
                        )
            except json.JSONDecodeError:
                pass

    # 第 2 层：Markdown 代码块提取
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict):
                return BrainResult(
                    reply=_safe_str(data, "reply", ""),
                    tool_calls=data.get("tool_calls", []),
                    thought=_safe_str(data, "thought", ""),
                )
        except json.JSONDecodeError:
            pass

    # 第 3 层：正则兜底
    reply_match = re.search(r'"reply"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
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
    thought: str = ""
    t_match = re.search(r'"thought"\s*:\s*"((?:[^"\\]|\\.)*)"', raw, re.DOTALL)
    if t_match:
        thought = t_match.group(1)

    logger.debug("[Parse] fallback 解析 | reply_len=%d | tools=%d | thought=%s",
                 len(reply), len(tool_calls), bool(thought))
    return BrainResult(reply=reply, tool_calls=tool_calls, thought=thought, finish_reason="stop")


def _strip_json_prefix(text: str) -> str:
    """移除 LLM 回复开头的 JSON 前缀（如 `{"tool_calls": []}\\n\\n`）。

    灵魂阶段 LLM 有时会在自然语言回复前输出一段 JSON 格式的工具状态，
    此函数将这部分剥离，只保留后面的正文。
    JSON 后无正文时返回空字符串，由调用方决定回退策略。
    """
    text = text.strip()
    if not text.startswith("{"):
        return text
    end = _find_json_boundary(text)
    if end < 0:
        return text
    return text[end + 1:].strip()


def clean_soul_reply(text: str) -> str:
    """净化灵魂阶段回复：提取纯文本正文。

    净化链：
    - 非 `{` 开头的纯自然语言 → 直接返回（不触发 JSON fallback 解析）；
    - 以 `{` 开头 → 优先提取 JSON 中的 reply 字段，再尝试剥离 JSON 前缀；
    - 全部失败 → 返回空字符串（由调用方决定兜底/重试）。
    """
    t = text.strip()
    if not t:
        return ""
    if not t.startswith("{"):
        return t
    parsed = parse_llm_response(t)
    if parsed.reply:
        return parsed.reply
    clean = _strip_json_prefix(t)
    if clean and not clean.startswith("{"):
        return clean
    return ""


# ── Brain：LLM 交互层 ─────────────────────────────────────────────────────────


class Brain:
    """LLM 交互层：思考 / 灵魂 / 压缩 / 降级。"""

    def __init__(self, conv: ConversationService, config: AgentConfig) -> None:
        self._conv = conv
        self._config = config
        self._cot_enabled = config.cot_enabled
        self._use_native_thinking = config.cot_enabled and getattr(conv, 'supports_thinking', False)
        # 预缓存 thinking_kwargs 避免每次调用新建 dict
        self._think_kwargs_cache: dict[str, Any] = {"reasoning_effort": config.reasoning_effort}
        self._consecutive_timeouts: int = 0  # 连续超时计数
        self._refine_rounds: int = 0  # 工具阶段 refine 轮次（日志用）
        self._compressed_context: str = ""  # 压缩后的历史摘要

    @property
    def cot_enabled(self) -> bool:
        return self._cot_enabled

    @property
    def use_native_thinking(self) -> bool:
        return self._use_native_thinking

    @property
    def compressed_context(self) -> str:
        """压缩后的历史摘要（灵魂阶段注入用）。"""
        return self._compressed_context

    def reset(self) -> None:
        """每轮对话开始前重置连续超时计数。"""
        self._consecutive_timeouts = 0
        self._refine_rounds = 0

    def reset_compressed_context(self) -> None:
        self._compressed_context = ""

    # ── 工具阶段思考 ────────────────────────────────────────────────────────

    async def _call_llm_and_parse(self, prompt: str) -> BrainResult:
        """调用 LLM → 解析为 BrainResult → 自动捕获原生推理内容。

        带超时控制：超时时记录连续超时计数，超限后返回空结果触发降级路径。
        """
        try:
            reply_text = await asyncio.wait_for(
                self._conv.asend(prompt, store_history=True, **self._think_kwargs_cache),
                timeout=self._config.round_timeout,
            )
            # 超时计数器复位
            self._consecutive_timeouts = 0

            result = parse_llm_response(reply_text)
            if self._use_native_thinking and hasattr(self._conv, 'last_reasoning_content') and self._conv.last_reasoning_content:
                result.thought = self._conv.last_reasoning_content
            # 工具阶段输出净化：避免 JSON 决策消息污染后续阶段（灵魂阶段）上下文
            await self._sanitize_tool_phase_message(result)
            return result

        except asyncio.TimeoutError:
            self._consecutive_timeouts += 1
            logger.warning("[Timeout] LLM 调用超时 | consecutive=%d/%d | refine=%d",
                           self._consecutive_timeouts, self._config.max_consecutive_timeouts,
                           self._refine_rounds)

            if self._consecutive_timeouts >= self._config.max_consecutive_timeouts:
                logger.error("[Timeout] 连续超时达上限，强制退出工具阶段")
                return BrainResult(
                    reply="",
                    finish_reason="consecutive_timeout",
                )

            return BrainResult(
                reply="",
                finish_reason="timeout",
            )

    async def think(self, text: str) -> BrainResult:
        """初次 LLM 思考（工具阶段入口）"""
        result = await self._call_llm_and_parse(text)
        if self._use_native_thinking and result.thought:
            logger.debug("[Think] 原生推理捕获 | thought_len=%d", len(result.thought))
        return result

    async def think_with_context(self) -> BrainResult:
        """利用已注入的工具结果上下文继续 LLM 思考。"""
        if self._cot_enabled:
            prompt = _THINK_WITH_CONTEXT_COT_PROMPT
            if not self._use_native_thinking:
                prompt += "\n请先在 thought 字段中记录以上推理链，再输出 reply。"
        else:
            prompt = "请根据以上工具执行结果，继续你的推理和回复。"
        self._refine_rounds += 1
        result = await self._call_llm_and_parse(prompt)
        logger.debug("[Refine] 推理结果 | refine=%d | reply_len=%d | tools=%d | thought_len=%d",
                     self._refine_rounds, len(result.reply), len(result.tool_calls), len(result.thought))
        return result

    # ── 灵魂阶段 ────────────────────────────────────────────────────────────

    async def _sanitize_tool_phase_message(self, result: BrainResult) -> None:
        """将刚写入历史的工具阶段 assistant JSON 决策消息替换为纯文本。

        工具阶段 LLM 常输出 `{"tool_calls": [...]}` 等 JSON 决策消息，
        若以原始 JSON 存入历史，会诱导灵魂阶段 LLM 模仿输出 JSON，
        导致净化链失败、回复退化为兜底文本（如"你能再说一遍吗"）。

        仅当工具阶段未产出正式回复（reply 为空，纯 JSON 决策）时才净化；
        若已产出正式回复，该文本保留在历史中（它就是对话的一部分）。
        失败不阻塞主流程。
        """
        content = (result.reply or "").strip()
        if content:
            return  # 工具阶段已产出正式回复，无需净化
        content = "[已完成工具阶段分析]" if result.tool_calls else "[无需调用工具，继续对话]"
        try:
            await self._conv.replace_last_message(content)
        except Exception as e:
            logger.debug("[Sanitize] 工具阶段消息净化失败（不阻塞）: %s", e)

    async def generate_soul_reply(self) -> str:
        """灵魂阶段：使用完整角色人格 + 工具结果生成最终回复。

        调用前由主编排器完成灵魂阶段上下文切换（_enter_soul_phase）。
        净化失败时最多重试 max_soul_retries 次（调用超时不重试），
        全部失败后返回兜底文本。
        """
        for attempt in range(self._config.max_soul_retries):
            try:
                reply = await asyncio.wait_for(
                    self._conv.asend(_SOUL_PHASE_PROMPT, store_history=False, **self._think_kwargs_cache),
                    timeout=self._config.round_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("[Soul] 灵魂阶段调用超时 | attempt=%d | timeout=%.1fs",
                               attempt + 1, self._config.round_timeout)
                break
            except Exception as e:
                logger.warning("[Soul] 灵魂阶段异常，使用兜底回复 | error=%s", e)
                return _FALLBACK_REPLY

            clean = clean_soul_reply(reply)
            if clean:
                return clean
            logger.debug("[Soul] 净化失败，重试 | attempt=%d/%d",
                         attempt + 1, self._config.max_soul_retries)
        return _FALLBACK_REPLY

    # ── 降级兜底 ────────────────────────────────────────────────────────────

    async def force_summary_reply(self) -> str:
        """强制总结兜底：当 LLM 主流程异常/超时时，尝试用最低成本生成回复。

        降级链：强制总结 prompt → 固定兜底文本
        """
        try:
            reply = await asyncio.wait_for(
                self._conv.asend(_FORCE_SUMMARY_PROMPT, store_history=False),
                timeout=30.0,
            )
            clean = clean_soul_reply(reply)
            return clean or _FALLBACK_REPLY
        except Exception as e:
            logger.warning("[Fallback] 强制总结失败，使用固定兜底 | error=%s", e)
            return _FALLBACK_REPLY

    # ── 对话压缩 ────────────────────────────────────────────────────────────

    async def compress_conversation(self) -> None:
        """压缩超长对话历史，使用 LLM 摘要化最旧消息，减少后续 token 消耗。

        仅在历史总字符数超过 compression_threshold 时触发。
        保留最近 _COMPRESSION_KEEP 条消息，其余由 LLM 生成摘要。
        """
        history = await self._conv.get_history()
        total_chars = sum(len(m.content) for m in history)
        if total_chars <= self._config.compression_threshold:
            return

        if len(history) <= _COMPRESSION_KEEP:
            return

        to_compress = history[:-_COMPRESSION_KEEP]
        text_to_summarize = "\n".join(f"{m.role}: {m.content}" for m in to_compress)

        prompt = _COMPRESSION_PROMPT.format(text=text_to_summarize)

        try:
            provider = self._conv.provider
            if not provider:
                return

            messages = [{"role": "user", "content": prompt}]
            request = ChatRequest(messages=messages, model=provider.model)
            response = await asyncio.wait_for(
                provider.async_chat_completion(request),
                timeout=30.0,
            )

            self._compressed_context = response.content.strip()

            # 从历史中移除已被压缩的旧消息
            await self._conv.truncate_messages(_COMPRESSION_KEEP)

            logger.info("[Compress] 对话压缩完成 | 压缩 %d 条 -> 摘要 | 剩余 %d 条",
                        len(to_compress), len(await self._conv.get_history()))

        except asyncio.TimeoutError:
            logger.warning("[Compress] 压缩调用超时（跳过）")
        except Exception as e:
            logger.warning("[Compress] 对话压缩失败（不阻塞）: %s", e)


__all__ = ["Brain", "BrainResult", "parse_llm_response", "clean_soul_reply"]
