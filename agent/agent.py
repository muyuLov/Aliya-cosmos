"""AliyaAgent — 顶层 Agent 编排器（状态机架构）

参考设计来源：
- Claude Code Tool Use Loop：状态化 Agent 循环，Think → Act → Observe
- datawhalechina Agent-Learning-Hub 六步执行管线
- ToolCall Claw 设计思路：模块化注册、分区调度、权限校验

架构：
  IDLE → CONTEXT_ASSEMBLY → THINKING → (无工具 → COMPLETED)
                                    ↓ (有工具)
                               TOOL_EXECUTION → OBSERVING → THINKING → ...

流程：
  1. 上下文组装：注入工具描述 + 格式指令
  2. LLM 思考：输出 JSON (reply + tool_calls)
  3. 无工具 → 直接完成
  4. 有工具 → 分区调度执行 → 结果以临时消息注入 → 继续思考
  5. 完成 → 停止进度 → cleanup 临时消息 → 记忆保存 → TTS 播放
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
from core.logger import get_logger
from agent.tools.base import ToolContext
from agent.tools.registry import ToolRegistry

logger = get_logger(__name__)

# 默认配置常量
_PROGRESS_INTERVAL = 2.0
_MAX_TURNS = 10                # Agent 循环最大轮次（防止无限循环）
_MAX_REFINE_ACCUM = 10         # 最多保留多少条工具结果注入消息


class AgentState(Enum):
    """Agent 循环状态"""
    IDLE = "idle"
    CONTEXT_ASSEMBLY = "context_assembly"
    THINKING = "thinking"
    TOOL_EXECUTION = "tool_execution"
    OBSERVING = "observing"
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
    return AgentConfig(
        cot_enabled=cot_enabled, reasoning_effort=reasoning_effort,
        permission_config_path=perm_config_path,
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


# ── 格式指令 ───────────────────────────────────────────────────────────────────
_FORMAT_BASIC = (
    '你每次必须输出纯 JSON，格式：\n'
    '{"reply": "回复文本", "tool_calls": [...]}\n'
    '- "reply" 必填，是你的回复文本\n'
    '- "tool_calls" 可选，不调用工具时省略或留空\n'
    '- 可同时调用多个工具，它们会并行执行\n'
    '- 工具执行结果会在下一轮反馈给你，你可据此优化回复\n'
    '- 如果不需要调用工具，直接用 reply 回复即可\n'
)

_FORMAT_COT = (
    '你每次必须输出纯 JSON，格式：\n'
    '{\n'
    '  "thought": "推理过程（步骤1 → 步骤2 → ...）",\n'
    '  "reply": "回复文本",\n'
    '  "tool_calls": [...]\n'
    '}\n'
    '\n'
    '## 推理链\n'
    '\n'
    '在 "thought" 字段中按以下链式步骤依次推理：\n'
    '\n'
    '步骤1 ─ 问题分析（起点）\n'
    '  - 用户的核心诉求是什么？\n'
    '  - 是否有情感需求需要优先回应？\n'
    '\n'
    '步骤2 ─ 需求拆解\n'
    '  - 满足该诉求需要哪些信息或操作？\n'
    '  - 可划分为哪些子任务？\n'
    '\n'
    '步骤3 ─ 工具决策\n'
    '  - 每个子任务是否需要调用工具？\n'
    '  - 需要 → 在 tool_calls 中列出\n'
    '  - 不需要 → 基于已有知识直接回答\n'
    '\n'
    '步骤4 ─ 综合输出（终点）\n'
    '  - 汇总已有知识和工具结果\n'
    '  - 合成自然亲切的最终回复\n'
    '\n'
    '## 输出规则\n'
    '- "thought" 必填，记录完整推理链\n'
    '- "reply" 必填，最终对用户说的话\n'
    '- "tool_calls" 可选，不调用时省略\n'
)

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
    """Agent 主编排器——状态机驱动的 Agent 循环

    使用状态机管理 Agent 生命周期：
    IDLE → CONTEXT_ASSEMBLY → THINKING →
      (无工具) → COMPLETED
      (有工具) → TOOL_EXECUTION → OBSERVING → THINKING → ...
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

        logger.info(
            "[Init] Agent 初始化完成 | cot=%s | native=%s | format=%s | effort=%s",
            self._cot_enabled, self._use_native_thinking,
            self._config.tool_format_version, self._config.reasoning_effort,
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
        """处理用户消息：完整的 Agent 循环入口。"""
        self._turn = 0
        self._state = AgentState.IDLE
        final_reply = ""

        await self._notify({"type": "brain_start"})
        self._progress_task = asyncio.create_task(self._push_progress())

        try:
            # Step 1: 上下文组装
            await self._transition(AgentState.CONTEXT_ASSEMBLY)
            await self._assemble_context()

            # Step 2: 首轮 LLM 思考
            await self._transition(AgentState.THINKING)
            result = await self._think(text)
            final_reply = result.reply

            # 预创建 ToolContext，在循环中复用
            tool_ctx = ToolContext(
                tts_service=self._tts_service,
                audio_player=self._audio_player,
                memory_manager=self._memory_manager,
                send_message=self._send_message,
                permission_config=self._permission_config,
                confirm_callback=self._confirm_callback,
            )

            # Step 3: Agent 循环（Think → Act → Observe）
            while result.tool_calls:
                self._turn += 1
                if self._turn > self._config.max_turns:
                    logger.warning("[Plan] 达到最大循环轮次，强制终止 | turn=%d | max_turns=%d",
                                   self._turn, self._config.max_turns)
                    result = BrainResult(
                        reply=result.reply or "我已思考了很久，请让我继续。",
                        finish_reason="max_turns",
                    )
                    break

                # Step 4: 工具执行（分区调度）
                await self._transition(AgentState.TOOL_EXECUTION)
                tools_list = [c.get("name") for c in result.tool_calls]
                logger.debug("[Tool] 执行工具调用 | turn=%d | tools=%s", self._turn, tools_list)
                await self._notify({
                    "type": "brain_progress",
                    "message": f"执行工具调用（第 {self._turn} 轮）",
                    "tools": tools_list,
                })

                tool_results = await self._registry.dispatch_all(result.tool_calls, tool_ctx)

                # Step 5: 观察 — 将工具结果注入上下文
                await self._transition(AgentState.OBSERVING)
                summary = self._registry.format_tool_summary(tool_results)
                logger.debug("[Observe] 工具结果注入 | turn=%d | tools=%s",
                             self._turn, tools_list)
                await self._conv.append_message(
                    "assistant",
                    f"[工具执行结果]\n{summary}",
                    metadata={"injected": True, "prefix": _TOOL_RESULT_MARKER},
                )

                # Step 6: 继续思考
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

        except asyncio.CancelledError:
            self._state = AgentState.CANCELLED
            logger.info("[Plan] Agent 循环被取消 | turn=%d", self._turn)
            raise
        except Exception as e:
            self._state = AgentState.ERROR
            logger.error("[Plan] Agent 循环异常 | turn=%d | error=%s", self._turn, e, exc_info=True)
            await self._notify({"type": "brain_error", "message": str(e)})
        finally:
            # 立即停止进度推送，不再在 COMPLETED 期间继续推"思考中"
            if self._progress_task and not self._progress_task.done():
                self._progress_task.cancel()
            self._progress_task = None

            if self._state not in (AgentState.ERROR, AgentState.CANCELLED):
                self._state = AgentState.COMPLETED
                logger.info("[Complete] 回复完成 | turn=%d | reply_len=%d",
                            self._turn, len(final_reply))

            # 优先清理临时注入消息，再执行后续 I/O（TTS 等）
            try:
                await self._conv.discard_messages(_TOOL_RESULT_MARKER, self._config.max_refine_accum)
            except Exception:
                pass

            if final_reply:
                await self._save_memory(text, final_reply)

            # 自动 TTS 播放最终回复（最后执行，不阻塞清理）
            if final_reply and self._tts_service:
                await self._speak(final_reply)

    # ── Agent 循环子步骤 ────────────────────────────────────────────────────

    async def _assemble_context(self) -> None:
        """组装本轮上下文的工具描述和格式指令，注入到 ConversationService。"""
        tools_desc = self._registry.format_descriptions()

        instructions_parts = []
        if tools_desc:
            instructions_parts.append(tools_desc)
            if self._use_native_thinking or not self._cot_enabled:
                instructions_parts.append(_FORMAT_BASIC)
            else:
                instructions_parts.append(_FORMAT_COT)

        instructions = "\n\n".join(instructions_parts) if instructions_parts else ""

        await self._conv.set_context_injection(tools=instructions)
        logger.debug("[Context] 上下文注入完成 | tools=%d | chars=%d | format=%s",
                     len(self._registry.list()), len(instructions),
                     "basic" if (self._use_native_thinking or not self._cot_enabled) else "cot")

    def _thinking_kwargs(self) -> dict:
        return self._think_kwargs_cache

    async def _call_llm_and_parse(self, prompt: str) -> BrainResult:
        """调用 LLM → 解析为 BrainResult → 自动捕获原生推理内容。"""
        reply_text = await self._conv.asend(prompt, store_history=True, **self._thinking_kwargs())
        result = parse_llm_response(reply_text)
        if self._use_native_thinking and hasattr(self._conv, 'last_reasoning_content') and self._conv.last_reasoning_content:
            result.thought = self._conv.last_reasoning_content
        return result

    async def _think(self, text: str) -> BrainResult:
        """初次 LLM 思考"""
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
            # 仅在 THINKING 状态推送进度，避免 COMPLETED 等态误推"思考中"
            if self._state == AgentState.THINKING:
                await self._notify({"type": "brain_progress", "message": "思考中"})

    async def _notify(self, data: dict) -> None:
        if self._send_message:
            await self._send_message(data)
