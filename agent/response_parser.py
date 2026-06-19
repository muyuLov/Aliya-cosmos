from __future__ import annotations

from collections.abc import Iterator
import json
import re

from core.logger import get_logger
from agent.models import AgentResponse, ToolCall

logger = get_logger(__name__)


class ResponseParser:
    _CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

    def parse(self, raw_text: str) -> AgentResponse:
        for candidate in self._iter_candidates(raw_text):
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue

            if not isinstance(payload, dict):
                continue

            reply = payload.get("reply")
            tool_calls = payload.get("tool_calls", [])
            if not isinstance(reply, str) or not isinstance(tool_calls, list):
                continue

            parsed_calls: list[ToolCall] = []
            for item in tool_calls:
                if not isinstance(item, dict):
                    continue

                tool_name = item.get("tool_name")
                arguments = item.get("arguments")
                if not isinstance(tool_name, str) or not tool_name.strip():
                    continue
                if not isinstance(arguments, dict):
                    continue

                parsed_calls.append(ToolCall(tool_name=tool_name, arguments=arguments))
            return AgentResponse(reply_text=reply, tool_calls=parsed_calls)

        logger.warning("LLM 输出解析失败，降级为纯文本 | raw=%.200s", raw_text.strip())
        return AgentResponse(reply_text=raw_text.strip(), tool_calls=[])

    def _iter_candidates(self, raw_text: str) -> Iterator[str]:
        for match in self._CODE_BLOCK_RE.finditer(raw_text):
            yield match.group(1)
        yield raw_text.strip()
