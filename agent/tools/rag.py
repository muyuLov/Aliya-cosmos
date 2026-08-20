"""RAG 工具：让 Agent 在对话中检索知识库。"""

from __future__ import annotations

from agent.knowledge import get_knowledge_store
from agent.tools.base import ToolContext, ToolDefinition

search_knowledge_def = ToolDefinition(
    id="search_knowledge",
    name="search_knowledge",
    description="当用户问题涉及 Aliya 的背景知识、设定、过往记录或文档内容时调用，"
    "从知识库检索相关片段。参数 query 为检索关键词或问题。",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索问题或关键词"},
            "top_k": {"type": "integer", "description": "返回片段数，默认 5", "default": 5},
        },
        "required": ["query"],
    },
    enabled=True,
)


async def search_knowledge(_ctx: ToolContext, args: dict) -> str:
    # _ctx 真实字段：user_query / conversation_id / memory（GRAGMemoryManager | None）
    query = str(args.get("query", "")).strip()
    if not query:
        return "（知识库无相关片段）"
    top_k = max(1, min(int(args.get("top_k", 5)), 20))  # clamp 防模型传超限值
    results = await get_knowledge_store().search(query, top_k=top_k)
    if not results:
        return "（知识库无相关片段）"
    lines = []
    for i, r in enumerate(results, 1):
        title = r.metadata.get("title", "未知文档")
        lines.append(f"[{i}] 来自《{title}》\n{r.text}")
    return "\n\n".join(lines)


__all__ = ["search_knowledge_def", "search_knowledge"]
