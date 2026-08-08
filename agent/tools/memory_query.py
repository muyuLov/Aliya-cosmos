"""MemoryQueryTool — 记忆查询工具

双路径查询，提升召回质量：
- RAG 语义回答（``memory_manager.query_memory``）：生成自然语言回答；
- 结构化记忆图谱召回（``memory_manager.get_relevant_memories``）：返回五元组。

任一能力缺失时自动降级到另一条路径，两者都不可用时返回"未找到相关记忆"。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.logger import get_logger
from agent.tools.base import ToolBase, ToolPermission, ToolResult

if TYPE_CHECKING:
    from agent.context import AgentContext

logger = get_logger(__name__)

# limit 参数约束
_DEFAULT_LIMIT = 5
_MAX_LIMIT = 20


class MemoryQueryTool(ToolBase):
    """从记忆系统查询用户相关历史信息（只读，可并发）。

    需要回忆过去说过的话、约定、偏好或话题背景时使用。
    """

    name = "memory_query"
    description = (
        "从记忆系统中查询与用户相关的历史信息。需要回忆过去说过的话、"
        "过去的约定、偏好、重要事件，或当前话题缺少背景信息时使用此工具。"
    )
    input_schema: dict = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "查询关键词或完整问题，例如 '用户最喜欢的颜色'、'上周约定的计划'",
            },
            "limit": {
                "type": "integer",
                "description": "最多返回多少条结构化记忆（默认 5，最大 20）",
            },
        },
        "required": ["query"],
    }
    is_concurrency_safe = True
    permission = ToolPermission.ALWAYS_ALLOW

    @staticmethod
    def _format_quintuple(q: tuple[str, ...]) -> str:
        """将五元组 (head, head_type, relation, tail, tail_type) 格式化为可读文本。

        参数使用可变长度元组以兼容异常数据（长度不足时回退为原样）。
        """
        try:
            h, h_type, r, t, t_type = q
        except (TypeError, ValueError):
            return str(q)
        return f"{h}({h_type}) —[{r}]-> {t}({t_type})"

    async def execute(self, params: dict, context: "AgentContext") -> ToolResult:
        query = (params.get("query") or "").strip()
        if not query:
            return ToolResult(success=False, error="查询内容不能为空")

        try:
            raw_limit = int(params.get("limit", _DEFAULT_LIMIT) or _DEFAULT_LIMIT)
        except (TypeError, ValueError):
            raw_limit = _DEFAULT_LIMIT
        limit = max(1, min(raw_limit, _MAX_LIMIT))

        mm = context.memory_manager
        if not mm:
            return ToolResult(success=False, error="记忆系统不可用")

        answer: str | None = None
        memories: list[str] = []

        # 路径 1：RAG 语义回答（能力缺失或失败时降级）
        if hasattr(mm, "query_memory"):
            try:
                answer = await mm.query_memory(query)
            except Exception as e:
                logger.debug("[MemoryQuery] RAG 查询失败（降级）: %s", e)

        # 路径 2：结构化记忆图谱召回
        if hasattr(mm, "get_relevant_memories"):
            try:
                quintuples = await mm.get_relevant_memories(query, limit=limit)
                memories = [self._format_quintuple(q) for q in quintuples]
            except Exception as e:
                logger.debug("[MemoryQuery] 图谱召回失败（降级）: %s", e)

        if not answer and not memories:
            return ToolResult(success=True, data={"result": "未找到相关记忆"})

        parts: list[str] = []
        if answer:
            parts.append(f"回答：{answer}")
        if memories:
            parts.append("相关记忆：\n" + "\n".join(memories))
        return ToolResult(success=True, data={"result": "\n\n".join(parts)})
