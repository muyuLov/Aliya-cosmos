"""AliyaAgent — 两阶段状态机架构的 Agent 主编排器

Phase 1 ⚙ 工具阶段：
  system prompt = tools_system.md（仅有工具规则，不含角色人格）
  多轮循环：Think → Act → Observe，专注工具决策

Phase 2 灵魂阶段：
  system prompt = soul + identity + system + tone + style（完整人格）
  基于工具执行结果，用角色自身的表达方式生成最终回复

当首次思考无需工具时，直接跳过灵魂阶段（无额外 LLM 调用）。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable

from core.llm import ConversationService
from core.llm.models import ChatRequest, Message
from core.logger import get_logger
from agent.tools.base import ToolContext
from agent.tools.registry import ToolRegistry
from agent.prompts import PromptManager, get_prompt_manager
from agent.prompts.style_switcher import StyleSwitcher, get_style_switcher
from agent.emotion import FeelingScores, observe_feeling
from agent.emotion.feeling_scores import ALL_FEELINGS

logger = get_logger(__name__)

# ── 默认配置常量 ───────────────────────────────────────────────────────────────
_PROGRESS_INTERVAL = 2.0
_MAX_TURNS = 10                # Agent 循环最大轮次（防止无限循环）
_MAX_REFINE_ACCUM = 10         # 最多保留多少条工具结果注入消息
_TOOL_PHASE_TIMEOUT = 60.0     # 工具阶段每轮 LLM 调用超时（秒）
_MAX_CONSECUTIVE_TIMEOUTS = 10  # 连续超时上限，超限后强制退出工具阶段
_COMPRESSION_THRESHOLD = 80000  # 对话历史压缩阈值（字符数）
_COMPRESSION_KEEP = 10         # 压缩后保留的最新消息条数


class AgentState(Enum):
    """Agent 循环状态"""
    IDLE = "idle"
    CONTEXT_ASSEMBLY = "context_assembly"
    THINKING = "thinking"
    TOOL_EXECUTION = "tool_execution"
    OBSERVING = "observing"
    SOUL_PHASE = "soul_phase"  # 新增：灵魂阶段
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class AgentConfig:
    """Agent 运行配置"""
    max_turns: int = _MAX_TURNS
    progress_interval: float = _PROGRESS_INTERVAL
    max_refine_accum: int = _MAX_REFINE_ACCUM
    tool_format_version: str = "cot"  # "basic"=基础格式, "cot"=ReAct 格式
    cot_enabled: bool = True  # 思维链模式: true=启用, false=禁用
    reasoning_effort: str = "high"  # 思考强度: "high"/"max"/"low"
    # 权限配置文件路径（空字符串表示不启用配置驱动权限）
    permission_config_path: str = "data/config/Permissions.yml"
    # 以下为两阶段循环新增配置
    round_timeout: float = _TOOL_PHASE_TIMEOUT  # 工具阶段每轮超时
    max_consecutive_timeouts: int = _MAX_CONSECUTIVE_TIMEOUTS  # 连续超时上限
    compression_threshold: int = _COMPRESSION_THRESHOLD  # 对话压缩阈值
    # 分层 Prompt 配置
    prompt_style: str = "default"  # 表达风格: default / lively / healing / sweet
    # 自动风格切换配置
    auto_style_enabled: bool = True  # 风格自动切换（纯 LLM 模式）


def agent_config_from_yaml(config_path: str = "data/config/main.yml") -> AgentConfig:
    """从 YAML 配置文件读取 Agent 相关配置。"""
    from core.config import get_config_instance
    cfg = get_config_instance(config_path)
    llm_section = cfg.get("cosmos.service.llm") or {}
    raw = llm_section.get("cot_enabled", True)
    cot_enabled = raw if isinstance(raw, bool) else (str(raw).strip().lower() in ("true", "yes", "1"))
    raw_effort = llm_section.get("reasoning_effort")
    reasoning_effort = raw_effort if isinstance(raw_effort, str) and raw_effort in ("high", "max", "low") else "high"
    # 权限配置路径
    agent_section = cfg.get("cosmos.service.agent") or {}
    perm_section = agent_section.get("permissions") or {}
    perm_config_path = str(perm_section.get("config_path", "data/config/Permissions.yml"))
    # 表达风格 + 自动切换配置
    prompt_section = cfg.get("cosmos.service.prompt") or {}
    style = str(prompt_section.get("style", "default"))
    raw_auto = prompt_section.get("auto_style", True)
    auto_style = raw_auto if isinstance(raw_auto, bool) else (str(raw_auto).strip().lower() in ("true", "yes", "1"))
    return AgentConfig(
        cot_enabled=cot_enabled, reasoning_effort=reasoning_effort,
        permission_config_path=perm_config_path,
        prompt_style=style,
        auto_style_enabled=auto_style,
    )


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


# 注入到临时消息中的前缀标记，用于后续 cleanup
_TOOL_RESULT_MARKER = "tool_result"

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
    "请基于以上对话历史和工具执行结果，生成自然亲切的最终回复。"
    "直接输出你的回复即可，无需 JSON 格式或工具调用。"
)

# 强制总结 prompt（当 LLM 完全不可用时兜底使用）
_FORCE_SUMMARY_PROMPT = (
    "请对以上对话进行简要总结，然后给出一个自然友好的回复。"
    "请直接输出回复文本。"
)

# 最终降级回复（所有兜底都失败时使用）
_FALLBACK_REPLY = "让我想想……嗯，你能再说一遍吗？"

# 对话压缩 prompt
_COMPRESSION_PROMPT = (
    "请用简洁中文总结以下对话的关键信息（保留所有事实、用户偏好、约定、重要情感记忆），"
    "去掉冗余的寒暄和无关细节：\n\n{text}"
)


# ── LLM 输出解析 ─────────────────────────────────────────────────────────────


def _safe_str(data: dict, key: str, default: str) -> str:
    v = data.get(key, default)
    return str(v) if v is not None else default


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


# ── Agent 主编排器 ──────────────────────────────────────────────────────────


class AliyaAgent:
    """Agent 主编排器——两阶段状态机驱动的 Agent 循环

    两阶段架构：
    Phase 1 — ⚙ 工具阶段：工具描述 + 格式规则（无角色人格）
    Phase 2 — 灵魂阶段：角色人格 + 记忆上下文（无工具描述）

    状态流转：
    IDLE → CONTEXT_ASSEMBLY → THINKING →
      (无工具) → SOUL_PHASE(跳过) → COMPLETED
      (有工具) → TOOL_EXECUTION → OBSERVING → THINKING → ...
                 → SOUL_PHASE → COMPLETED
    """

    def __init__(
        self,
        conversation_service: ConversationService,
        tool_registry: ToolRegistry,
        memory_manager: Any | None = None,
        send_message: Callable[[dict], Awaitable[None]] | None = None,
        tts_service: Any | None = None,
        audio_player: Any | None = None,
        audio_relay: Callable[[dict], Awaitable[None]] | None = None,
        config: AgentConfig | None = None,
        confirm_callback: Callable[[str, dict], Awaitable[bool]] | None = None,
        prompt_manager: PromptManager | None = None,
    ) -> None:
        self._conv = conversation_service
        self._registry = tool_registry
        self._memory_manager = memory_manager
        self._send_message = send_message
        self._tts_service = tts_service
        self._audio_player = audio_player
        self._audio_relay = audio_relay
        self._config = config or AgentConfig()

        self._state: AgentState = AgentState.IDLE
        self._turn: int = 0
        self._progress_task: asyncio.Task | None = None

        # 思维链模式直接取自配置
        self._cot_enabled = self._config.cot_enabled
        self._use_native_thinking = self._cot_enabled and getattr(conversation_service, 'supports_thinking', False)

        # 权限配置管理器
        self._permission_config: Any = self._init_permission_config()

        # 用户确认回调（由运行时环境提供）
        self._confirm_callback = confirm_callback

        # 预缓存 thinking_kwargs 避免每次调用新建 dict
        self._think_kwargs_cache = {"reasoning_effort": self._config.reasoning_effort}

        # ── 分层 Prompt 管理 ──
        self._prompt_manager = prompt_manager or get_prompt_manager()
        self._current_style: str = self._config.prompt_style
        self._current_emotion: str = ""  # 当前情绪状态（用于生成情绪补丁）

        # ── 自动风格切换 ──
        self._auto_style_enabled: bool = self._config.auto_style_enabled
        self._style_switcher: StyleSwitcher = get_style_switcher()

        # ── 情绪连续性 ──
        self._feeling_scores = FeelingScores()

        # ── 两阶段循环状态 ──
        self._compressed_context: str = ""   # 压缩后的历史摘要
        self._consecutive_timeouts: int = 0  # 连续超时计数
        self._has_called_tools: bool = False  # 本轮是否调用了工具

        logger.info(
            "[Init] Agent 初始化完成 | cot=%s | native=%s | format=%s | effort=%s | timeout=%.1f | style=%s",
            self._cot_enabled, self._use_native_thinking,
            self._config.tool_format_version, self._config.reasoning_effort,
            self._config.round_timeout, self._current_style,
        )

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def turn(self) -> int:
        return self._turn

    @property
    def cot_enabled(self) -> bool:
        return self._cot_enabled

    @property
    def use_native_thinking(self) -> bool:
        return self._use_native_thinking

    # ── 主入口 ──────────────────────────────────────────────────────────────

    async def handle_user_message(self, text: str) -> None:
        """处理用户消息：自动风格切换 → 工具阶段 → 灵魂阶段。"""
        self._turn = 0
        self._state = AgentState.IDLE
        self._consecutive_timeouts = 0
        self._has_called_tools = False
        self._compressed_context = ""
        final_reply = ""

        await self._notify({"type": "brain_start"})
        self._progress_task = asyncio.create_task(self._push_progress())

        # 预创建 ToolContext，在循环中复用
        tool_ctx = ToolContext(
            tts_service=self._tts_service,
            audio_player=self._audio_player,
            memory_manager=self._memory_manager,
            send_message=self._send_message,
            permission_config=self._permission_config,
            confirm_callback=self._confirm_callback,
        )

        try:
            # ── Step 0: 根据用户输入自动切换表达风格 ──
            if self._auto_style_enabled:
                recommended = await self._style_switcher.analyze(
                    text, provider=getattr(self._conv, '_provider', None),
                )
                if recommended != self._current_style:
                    self._current_style = recommended
                    logger.debug("[AutoStyle] 自动切换风格 | text_preview=%s... → style=%s",
                                 text[:20], recommended)
                    await self._notify({
                        "type": "style_changed",
                        "style": recommended,
                        "auto": True,
                    })

            # ── Step 1: 上下文组装 ──
            await self._transition(AgentState.CONTEXT_ASSEMBLY)
            await self._enter_tool_phase()

            # ── Step 2: ⚙ 工具阶段首轮思考 ──
            await self._transition(AgentState.THINKING)
            result = await self._think(text)
            final_reply = result.reply

            # ── Step 3: ⚙ 工具阶段循环（Think → Act → Observe） ──
            while result.tool_calls:
                self._has_called_tools = True
                self._turn += 1

                # 轮次上限检查
                if self._turn > self._config.max_turns:
                    logger.warning("[Plan] 达到最大循环轮次，强制进入灵魂阶段 | turn=%d | max_turns=%d",
                                   self._turn, self._config.max_turns)
                    break

                # Step 3a: 执行工具
                await self._transition(AgentState.TOOL_EXECUTION)
                tools_list = [c.get("name") for c in result.tool_calls]
                logger.debug("[Tool] 执行工具调用 | turn=%d | tools=%s", self._turn, tools_list)
                await self._notify({
                    "type": "brain_progress",
                    "message": f"执行工具调用（第 {self._turn} 轮）",
                    "tools": tools_list,
                })

                tool_results = await self._registry.dispatch_all(result.tool_calls, tool_ctx)

                # Step 3b: 观察 — 将工具结果注入上下文
                await self._transition(AgentState.OBSERVING)
                summary = self._registry.format_tool_summary(tool_results)
                logger.debug("[Observe] 工具结果注入 | turn=%d | tools=%s",
                             self._turn, tools_list)
                await self._conv.append_message(
                    "assistant",
                    f"[工具执行结果]\n{summary}",
                    metadata={"injected": True, "prefix": _TOOL_RESULT_MARKER},
                )

                # Step 3c: 继续思考
                await self._transition(AgentState.THINKING)
                await self._notify({
                    "type": "brain_progress",
                    "message": f"根据工具结果继续推理（第 {self._turn} 轮）",
                })

                result = await self._think_with_context()
                final_reply = result.reply

                await self._notify({
                    "type": "brain_refine",
                    "reply": result.reply,
                    "thought": result.thought,
                    "turn": self._turn,
                })

            # ── Step 4: 灵魂阶段 ──
            if self._has_called_tools:
                await self._transition(AgentState.SOUL_PHASE)
                await self._notify({"type": "brain_progress", "message": "进入灵魂表达阶段"})
                final_reply = await self._generate_soul_reply()
                logger.debug("[Soul] 灵魂阶段回复 | reply_len=%d", len(final_reply))

        except asyncio.CancelledError:
            self._state = AgentState.CANCELLED
            logger.info("[Plan] Agent 循环被取消 | turn=%d", self._turn)
            raise
        except Exception as e:
            self._state = AgentState.ERROR
            logger.error("[Plan] Agent 循环异常 | turn=%d | error=%s", self._turn, e, exc_info=True)
            await self._notify({"type": "brain_error", "message": str(e)})

            # 异常降级：尝试强制总结兜底
            if not final_reply:
                final_reply = await self._force_summary_reply()
        finally:
            # 立即停止进度推送
            if self._progress_task and not self._progress_task.done():
                self._progress_task.cancel()
            self._progress_task = None

            if self._state not in (AgentState.ERROR, AgentState.CANCELLED):
                self._state = AgentState.COMPLETED
                logger.info("[Complete] 回复完成 | turn=%d | reply_len=%d",
                            self._turn, len(final_reply))

            # 清理临时注入消息
            try:
                await self._conv.discard_messages(_TOOL_RESULT_MARKER, self._config.max_refine_accum)
            except Exception:
                pass

            if final_reply:
                await self._save_memory(text, final_reply)
                # 对话完成后情绪观察与平滑（在回复完成后触发）
                await self._observe_and_smooth_emotion(text, final_reply)

            # 自动 TTS 播放最终回复
            if final_reply and self._tts_service:
                await self._speak(final_reply)

    # ── 两阶段上下文切换 ─────────────────────────────────────────────────────

    async def _enter_tool_phase(self) -> None:
        """切换到工具阶段：使用 tools_system.md 作为 system prompt（无角色人格）。"""
        tool_system = self._prompt_manager.build_tool_system_prompt()
        await self._conv.set_system_prompt(tool_system)
        await self._conv.set_context_injection(tools="")

        # 尝试对话压缩
        await self._compress_conversation()

        logger.debug("[ToolPhase] 工具阶段上下文注入完成 | tools=%d",
                     len(self._registry.list()))

    async def _enter_soul_phase(self) -> None:
        """切换到灵魂阶段：恢复人格、注入记忆、设置情绪补丁。"""
        soul_system = self._prompt_manager.build_soul_system_prompt(style=self._current_style)
        await self._conv.set_system_prompt(soul_system)

        if self._current_emotion:
            patch = self._prompt_manager.build_emotion_patch(self._current_emotion)
            await self._conv.set_emotion_patch(patch)

        extra_parts = []
        if self._compressed_context:
            extra_parts.append(f"[历史对话摘要]\n{self._compressed_context}")
        if self._memory_manager:
            try:
                related = await self._memory_manager.get_relevant_memories("", limit=3)
                if related:
                    extra_parts.append(f"[相关记忆]\n{str(related)}")
            except Exception:
                pass

        memory_context = "\n\n".join(extra_parts) if extra_parts else ""
        await self._conv.set_context_injection(memory=memory_context)

        logger.debug("[SoulPhase] 已完成 | style=%s | emotion=%s | has_summary=%s | has_memory=%s",
                     self._current_style, self._current_emotion or "none",
                     bool(self._compressed_context), bool(memory_context))

    # ── Agent 循环子步骤 ────────────────────────────────────────────────────

    def _thinking_kwargs(self) -> dict:
        return self._think_kwargs_cache

    async def _call_llm_and_parse(self, prompt: str) -> BrainResult:
        """调用 LLM → 解析为 BrainResult → 自动捕获原生推理内容。

        带超时控制：超时时记录连续超时计数，超限后返回空结果触发降级路径。
        """
        try:
            reply_text = await asyncio.wait_for(
                self._conv.asend(prompt, store_history=True, **self._thinking_kwargs()),
                timeout=self._config.round_timeout,
            )
            # 超时计数器复位
            self._consecutive_timeouts = 0

            result = parse_llm_response(reply_text)
            if self._use_native_thinking and hasattr(self._conv, 'last_reasoning_content') and self._conv.last_reasoning_content:
                result.thought = self._conv.last_reasoning_content
            return result

        except asyncio.TimeoutError:
            self._consecutive_timeouts += 1
            logger.warning("[Timeout] LLM 调用超时 | consecutive=%d/%d | turn=%d",
                           self._consecutive_timeouts, self._config.max_consecutive_timeouts, self._turn)

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

    async def _think(self, text: str) -> BrainResult:
        """初次 LLM 思考（工具阶段入口）"""
        result = await self._call_llm_and_parse(text)
        if self._use_native_thinking and result.thought:
            logger.debug("[Think] 原生推理捕获 | thought_len=%d", len(result.thought))
        return result

    async def _think_with_context(self) -> BrainResult:
        """利用已注入的工具结果上下文继续 LLM 思考。"""
        if self._cot_enabled:
            prompt = _THINK_WITH_CONTEXT_COT_PROMPT
            if not self._use_native_thinking:
                prompt += "\n请先在 thought 字段中记录以上推理链，再输出 reply。"
        else:
            prompt = "请根据以上工具执行结果，继续你的推理和回复。"
        result = await self._call_llm_and_parse(prompt)
        logger.debug("[Refine] 推理结果 | turn=%d | reply_len=%d | tools=%d | thought_len=%d",
                     self._turn, len(result.reply), len(result.tool_calls), len(result.thought))
        return result

    # ── 灵魂阶段 ────────────────────────────────────────────────────────────

    async def _generate_soul_reply(self) -> str:
        """灵魂阶段：使用完整角色人格 + 工具结果生成最终回复。

        仅在工具阶段实际调用了工具时执行。
        """
        await self._enter_soul_phase()

        try:
            reply = await asyncio.wait_for(
                self._conv.asend(_SOUL_PHASE_PROMPT, store_history=False, **self._thinking_kwargs()),
                timeout=self._config.round_timeout,
            )
            return reply.strip() or _FALLBACK_REPLY
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning("[Soul] 灵魂阶段异常，使用兜底回复 | error=%s", e)
            return _FALLBACK_REPLY

    # ── 对话压缩 ────────────────────────────────────────────────────────────

    async def _compress_conversation(self) -> None:
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
            provider = getattr(self._conv, '_provider', None)
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
            async with self._conv._lock:
                self._conv._context.messages = self._conv._context.messages[-_COMPRESSION_KEEP:]
                self._conv._context.updated_at = time.time()
                self._conv._save()

            logger.info("[Compress] 对话压缩完成 | 压缩 %d 条 -> 摘要 | 剩余 %d 条",
                        len(to_compress), len(self._conv._context.messages))

        except asyncio.TimeoutError:
            logger.warning("[Compress] 压缩调用超时（跳过）")
        except Exception as e:
            logger.warning("[Compress] 对话压缩失败（不阻塞）: %s", e)

    # ── 情绪观察 ────────────────────────────────────────────────────────────

    async def _observe_and_smooth_emotion(self, user_input: str, ai_reply: str) -> None:
        """对话完成后，LLM 观察 Aliya 的情绪 → 平滑更新分数 → 更新当前情绪。"""
        provider = getattr(self._conv, '_provider', None)
        if not provider:
            return

        observed = await observe_feeling(user_input, ai_reply, provider)
        self._feeling_scores.smooth(observed)
        self._current_emotion = self._feeling_scores.dominant
        logger.debug("[Emotion] 观察+平滑完成 | observed=%s → dominant=%s",
                     observed, self._current_emotion)

    # ── 兜底降级 ────────────────────────────────────────────────────────────

    async def _force_summary_reply(self) -> str:
        """强制总结兜底：当 LLM 主流程异常/超时时，尝试用最低成本生成回复。

        降级链：强制总结 prompt → 固定兜底文本
        """
        try:
            reply = await asyncio.wait_for(
                self._conv.asend(_FORCE_SUMMARY_PROMPT, store_history=False),
                timeout=30.0,
            )
            return reply.strip() or _FALLBACK_REPLY
        except Exception as e:
            logger.warning("[Fallback] 强制总结失败，使用固定兜底 | error=%s", e)
            return _FALLBACK_REPLY

    # ── 状态管理 ────────────────────────────────────────────────────────────

    async def _transition(self, new_state: AgentState) -> None:
        """状态转换，发送状态变更通知。"""
        old_state = self._state
        if old_state != new_state:
            self._state = new_state
            await self._notify({
                "type": "state_change",
                "from": old_state.value,
                "to": new_state.value,
                "turn": self._turn,
            })

    # ── 外部接口 ────────────────────────────────────────────────────────────

    async def handle_clear_history(self, confirm: bool = False) -> None:
        if confirm:
            try:
                loop = asyncio.get_running_loop()
                reply = await loop.run_in_executor(
                    None, lambda: input("确认清空历史？(y/n): ").strip().lower()
                )
            except (RuntimeError, EOFError):
                reply = "y"
            if reply != "y":
                return
        await self._conv.clear_history()
        logger.info("对话历史已清空")

    # ── 风格与情绪管理 ─────────────────────────────────────────────────────

    def set_style(self, style: str) -> None:
        """设置表达风格。风格在下次灵魂阶段注入时应用。

        Args:
            style: 风格名称（default / lively / healing / sweet）。
        """
        self._current_style = style
        logger.info("[Style] 表达风格已切换 | style=%s", style)

    def get_style(self) -> str:
        """获取当前表达风格名称。"""
        return self._current_style

    def set_emotion(self, feeling: str) -> None:
        """设置当前情绪状态，同时更新情绪连续性分数。

        Args:
            feeling: 情绪名称（开心/温柔/感动/担心/难过/害羞/撒娇/认真/平静）。
        """
        self._current_emotion = feeling
        if feeling in ALL_FEELINGS:
            self._feeling_scores.smooth(feeling)  # type: ignore[arg-type]
        logger.debug("[Emotion] 情绪状态已更新 | feeling=%s | scores=%s",
                     feeling, self._feeling_scores.dominant)

    def get_emotion(self) -> str:
        """获取当前情绪状态名称。"""
        return self._current_emotion

    def get_feeling_scores(self) -> dict[str, float]:
        """获取情绪连续性分数快照。"""
        return self._feeling_scores.scores

    def get_prompt_config(self) -> dict:
        """获取当前 Prompt 配置信息。"""
        return {
            "style": self._current_style,
            "emotion": self._current_emotion or "none",
            "styles": self._prompt_manager.list_styles(),
        }

    # ── 辅助方法 ────────────────────────────────────────────────────────────

    async def _speak(self, text: str) -> None:
        """自动 TTS 语音播放，失败不影响主流程。"""
        try:
            from agent.tools.tts_speak import speak_text

            ctx = ToolContext(
                tts_service=self._tts_service,
                audio_player=self._audio_player,
                send_message=self._send_message,
                audio_relay=self._audio_relay,
                confirm_callback=self._confirm_callback,
            )
            await speak_text(text, ctx)
        except Exception as e:
            logger.warning("TTS 自动播放失败（已忽略）: %s", e)

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

    def _init_permission_config(self) -> Any:
        """初始化权限配置管理器。失败时返回 None，校验将回退到工具默认权限。"""
        if not self._config.permission_config_path:
            return None
        try:
            from agent.tools.permission_config import PermissionConfigManager
            cfg = PermissionConfigManager(self._config.permission_config_path)
            logger.debug("权限配置已加载 | path=%s", self._config.permission_config_path)
            return cfg
        except Exception as e:
            logger.warning("权限配置加载失败（权限校验将使用默认允许）: %s", e)
            return None

    async def _push_progress(self) -> None:
        while True:
            await asyncio.sleep(self._config.progress_interval)
            # 仅在 THINKING / SOUL_PHASE 状态推送进度
            if self._state in (AgentState.THINKING, AgentState.SOUL_PHASE):
                await self._notify({"type": "brain_progress", "message": "思考中"})

    async def _notify(self, data: dict) -> None:
        if self._send_message:
            await self._send_message(data)
