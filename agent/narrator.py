"""主叙事器（Narrator）

封装主叙事 LLM 调用：
- 整个上下文作为单条 JSON 消息（system 固定合约 + user 纯 JSON 结构化上下文）
- response_format: {type:'json_object'}
- 一次调用产出 script + 行为决策 + 结构化副产物
- prose(script) 与 transport(reply) 分离
- 空 script → 语义失败 → narrative-retry 重试，不推进 cursor
- 超时/异常 → 降级纯文本模式
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol

from agent.metadata_parser import NarrativeOutput, parse_narrative_output
from core.logger import get_logger

logger = get_logger(__name__)

# JSON 输出格式指令（注入 system prompt 末尾）
_JSON_FORMAT_INSTRUCTION = (
    "\n\n## 输出格式\n"
    "你必须以 JSON 对象回复，包含以下字段：\n"
    "```json\n"
    '{"script":"故事剧本","reply":{"mode":"immediate","content":"可见回复"},'
    '"memories":[],"intents":[],"actions":[]}\n'
    "```\n"
    "script: 故事剧本（叙事文本，必填）\n"
    "reply.mode: immediate | delayed | none\n"
    "reply.content: 可见回复文本\n"
    "memories: 记忆候选数组\n"
    "intents: 行为意图数组\n"
    "actions: 受限行动数组"
)

# 默认最大重试次数
_DEFAULT_MAX_RETRIES = 3
# 默认超时秒数
_DEFAULT_TIMEOUT = 30.0


class LLMService(Protocol):
    """LLM 服务协议（兼容 core/llm ConversationService）。"""

    async def create_completion(
        self, messages: list[dict[str, Any]], **kwargs: Any
    ) -> Any: ...


class Narrator:
    """主叙事器：一次 LLM 调用产出 script + 行为决策 + 结构化副产物。"""

    def __init__(self, llm_service: LLMService) -> None:
        self._llm = llm_service

    async def invoke(
        self,
        system_prompt: str,
        context_json: dict[str, Any],
        *,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> NarrativeOutput:
        """调用主叙事 LLM，返回解析后的 NarrativeOutput。

        自动重试逻辑：
        - 空 script → 语义失败，最多重试 max_retries 次
        - 超时 → 降级纯文本
        - 非 JSON → 降级纯文本
        """
        # 构建完整 system prompt（含 JSON 格式指令）
        full_system = system_prompt + _JSON_FORMAT_INSTRUCTION

        for attempt in range(max_retries):
            try:
                result = await asyncio.wait_for(
                    self._call_llm(full_system, context_json),
                    timeout=timeout,
                )
                # 检查 script 是否为空（语义失败）
                if result.has_required_script:
                    return result
                # 空 script → 重试
                logger.warning(
                    "主叙事器空 script (attempt %d/%d)，重试",
                    attempt + 1,
                    max_retries,
                )
                continue
            except asyncio.TimeoutError:
                logger.warning("主叙事器超时 (%.1fs)，降级纯文本", timeout)
                return self._fallback(f"主叙事器超时（{timeout}s），已降级")
            except Exception as exc:
                logger.warning("主叙事器异常: %s，降级纯文本", exc)
                return self._fallback(f"主叙事器异常: {exc}")

        # 所有重试用完，返回最后一次结果（可能 script 仍为空）
        logger.warning("主叙事器重试耗尽 (%d 次)，返回降级结果", max_retries)
        return self._fallback("主叙事器重试耗尽，已降级")

    async def _call_llm(
        self, system_prompt: str, context_json: dict[str, Any]
    ) -> NarrativeOutput:
        """执行单次 LLM 调用。"""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(
                context_json, ensure_ascii=False, default=str
            )},
        ]
        response = await self._llm.create_completion(
            messages,
            response_format={"type": "json_object"},
        )
        # 提取内容
        content = response.choices[0].message.content
        return parse_narrative_output(content)

    @staticmethod
    def _fallback(message: str) -> NarrativeOutput:
        """生成降级纯文本结果。"""
        return NarrativeOutput(
            script="",
            has_required_script=False,
            reply_mode="none",
            reply_content="",
            raw={"_fallback": True, "_reason": message},
        )
