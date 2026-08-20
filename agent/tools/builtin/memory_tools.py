"""内置工具：记忆查询与最近对话查询"""

from __future__ import annotations

from agent.tools.base import ToolContext, ToolDefinition

_MEMORY_QUERY_DEF = ToolDefinition(
    id="memory_query",
    name="memory_query",
    description="检索长期记忆图谱中的相关五元组事实，用于回答关于过去对话内容的问题",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要检索的记忆查询文本"},
            "limit": {"type": "integer", "description": "返回条数上限，默认 3"},
        },
        "required": ["query"],
    },
    risk="safe",
)

_RECENT_DEF = ToolDefinition(
    id="query_recent_conversation",
    name="query_recent_conversation",
    description="基于最近对话上下文回答用户关于近况/最近聊了什么的问题",
    input_schema={
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "要回答的问题"},
        },
        "required": ["question"],
    },
    risk="safe",
)


async def memory_query(ctx: ToolContext, args: dict) -> str:
    """从记忆图谱检索相关五元组并格式化为可读文本。"""
    if ctx.memory is None:
        return "[记忆不可用]"
    quintuples = await ctx.memory.get_relevant_memories(
        query=args.get("query", ""),
        limit=args.get("limit", 3),
    )
    if not quintuples:
        return "[没有找到相关记忆]"
    lines = [f"- {h} {r} {t}" for h, _ht, r, t, _tt in quintuples]
    return "\n".join(lines)


async def query_recent_conversation(ctx: ToolContext, args: dict) -> str:
    """基于最近上下文进行 RAG 问答。"""
    if ctx.memory is None:
        return "[记忆不可用]"
    answer = await ctx.memory.query_memory(question=args.get("question", ""))
    if not answer:
        return "[无相关记忆]"
    return answer


def register_memory_tools(registry) -> None:
    """将记忆工具注册进注册表。"""
    registry.register(_MEMORY_QUERY_DEF, memory_query)
    registry.register(_RECENT_DEF, query_recent_conversation)
