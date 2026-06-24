"""RAG 知识查询模块

使用项目 core/llm/ 替换直接 API 调用，通过 memory._providers 复用共享 Provider。
结合图谱查询实现知识检索。

RAG 查询路径：
  1. LLM 提取关键词（call #1）
  2. 图谱关键词检索
  3. LLM 生成回答（call #2）
"""

from __future__ import annotations

import asyncio
from typing import List, Optional, Tuple

from core.llm.models import ChatRequest, Message
from core.logger import get_logger

from memory._utils import parse_json_array
from memory.config import get_grag_config
from memory import graph
from memory._providers import get_memory_provider
from memory.exceptions import RAGQueryError, RAGGenerationError

logger = get_logger(__name__)

# 五元组类型别名
QuintupleType = Tuple[str, str, str, str, str]

# 系统提示词
SYSTEM_PROMPT = """你是一个专业的知识图谱查询助手。你的任务是根据用户问题和上下文，
从知识图谱中提取相关的关键词，用于检索五元组关系。

请分析问题，提取核心实体和关系关键词。
只返回 JSON 格式的关键词数组，不要输出其他内容。"""

# 用户提示词模板
KEYWORD_EXTRACT_PROMPT = """基于以下上下文和用户问题，提取与知识图谱相关的关键词。

要求：
- 只提取核心实体（如人物、物品、地点、组织等）
- 只提取关键关系词
- 避免无关的修饰词和停用词
- 直接返回 JSON 数组格式的关键词列表

上下文：
{context}

问题：{question}

请直接返回 JSON 数组，例如：["关键词1", "关键词2", "关键词3"]
只返回 JSON，不要其他内容。"""

# 回答生成提示词模板
ANSWER_PROMPT_TEMPLATE = """基于以下从知识图谱检索到的五元组关系，回答用户问题。

检索到的关系：
{quintuples}

用户问题：{question}

请根据检索到的关系，自然地回答问题。
如果检索结果与问题相关，请基于事实回答。
如果检索结果不相关，请说明无法从已知信息中回答。"""


class RAGQueryEngine:
    """RAG 知识查询引擎

    上下文可通过 set_context() 预置或通过 query_async() 的 context 参数传入。
    参数传入优先于预置上下文。
    """

    def __init__(self) -> None:
        """初始化 RAG 查询引擎"""
        self._recent_context: List[str] = []

    @property
    def provider(self):
        """获取 LLM Provider（通过模块级懒加载共享单例）"""
        return get_memory_provider()

    def set_context(self, texts: List[str]) -> None:
        """
        设置对话上下文（预置方式；参数传入方式优先）

        Args:
            texts: 上下文文本列表
        """
        cfg = get_grag_config()
        context_length = cfg.context_length
        self._recent_context = texts[:context_length]
        logger.debug(f"更新查询上下文: {len(self._recent_context)} 条记录")

    def query(self, question: str, context: List[str] | None = None) -> Optional[str]:
        """
        同步查询知识（供无事件循环的上下文使用）。

        内部驱动 query_async，调用方须在无运行中事件循环的上下文中使用。
        """
        return asyncio.run(self.query_async(question, context=context))

    async def query_async(
        self, question: str, context: List[str] | None = None
    ) -> Optional[str]:
        """
        异步查询知识，不阻塞事件循环。

        Args:
            question: 用户问题
            context:  对话上下文；为 None 时使用 set_context() 预置的上下文

        Returns:
            回答文本，无结果时返回 None

        Raises:
            RAGQueryError: RAG 查询失败
        """
        try:
            cfg = get_grag_config()

            # 1. 提取关键词（LLM call #1）
            keywords = await self._extract_keywords(question, context=context)
            if not keywords:
                logger.warning("未提取到关键词")
                return None

            logger.info(f"提取关键词: {keywords}")

            # 2. 查询图谱（同步调用，由 asyncio.to_thread 避免阻塞）
            quintuples = await asyncio.to_thread(
                graph.query_graph_by_keywords, keywords,
                similarity_threshold=cfg.similarity_threshold,
            )
            if not quintuples:
                logger.info("图谱中未找到相关关系")
                return None

            # 3. 生成回答（LLM call #2）
            return await self._generate_answer(question, quintuples)

        except (RAGQueryError, RAGGenerationError):
            raise
        except Exception as e:
            logger.error(f"RAG 查询失败: {e}")
            raise RAGQueryError(
                message=str(e),
                details={"question": question},
                cause=e,
            )

    async def _extract_keywords(
        self, question: str, context: List[str] | None = None
    ) -> List[str]:
        """异步提取查询关键词（含自我认知增强）"""
        ctx = context if context is not None else self._recent_context
        context_str = "\n".join(ctx) if ctx else "无上下文"

        prompt = KEYWORD_EXTRACT_PROMPT.format(
            context=context_str,
            question=question,
        )

        try:
            request = ChatRequest(
                messages=[
                    Message(role="system", content=SYSTEM_PROMPT).to_api_dict(),
                    Message(role="user", content=prompt).to_api_dict(),
                ],
                model=self.provider.model,
                temperature=0.3,
                max_tokens=500,
            )

            cfg = get_grag_config()
            response = await asyncio.wait_for(
                self.provider.async_chat_completion(request),
                timeout=cfg.extractor.timeout,
            )
            keywords = self._parse_keywords(response.content.strip())

            # 自我认知增强：问题涉及身份/记忆时，补充用户相关关键词
            if self._is_identity_question(question):
                if "用户" not in keywords:
                    keywords.append("用户")
                if "我" not in keywords:
                    keywords.append("我")

            return keywords

        except Exception as e:
            logger.error(f"关键词提取失败: {e}")
            return []

    def _is_identity_question(self, question: str) -> bool:
        """判断是否为自我认知类问题"""
        identity_patterns = [
            "我是谁", "我叫什么", "我的名字", "你记得我",
            "你认识我", "我是", "我不会忘", "你还记得",
            "会不会忘", "忘了", "还记得", "别忘了",
        ]
        q = question.lower()
        return any(p in q for p in identity_patterns)

    def _parse_keywords(self, content: str) -> List[str]:
        """解析关键词响应"""
        data = parse_json_array(content, "关键词响应")
        if data is not None:
            return [str(k).strip() for k in data if k]
        return []

    async def _generate_answer(
        self, question: str, quintuples: List[QuintupleType]
    ) -> str:
        """异步生成回答（LLM call #2）"""
        quintuple_strs = [
            f"- {h}({h_type}) —[{r}]→ {t}({t_type})"
            for h, h_type, r, t, t_type in quintuples
        ]

        prompt = ANSWER_PROMPT_TEMPLATE.format(
            quintuples="\n".join(quintuple_strs),
            question=question,
        )

        try:
            request = ChatRequest(
                messages=[Message(role="user", content=prompt).to_api_dict()],
                model=self.provider.model,
                temperature=0.5,
                max_tokens=1000,
            )

            cfg = get_grag_config()
            response = await asyncio.wait_for(
                self.provider.async_chat_completion(request),
                timeout=cfg.extractor.timeout,
            )
            return response.content.strip()

        except Exception as e:
            logger.error(f"回答生成失败: {e}")
            raise RAGGenerationError(
                message=str(e),
                details={"quintuple_count": len(quintuples)},
                cause=e,
            )


# 全局 RAG 查询引擎实例
_rag_engine: RAGQueryEngine | None = None


def get_rag_engine() -> RAGQueryEngine:
    """获取 RAG 查询引擎单例"""
    global _rag_engine
    if _rag_engine is None:
        _rag_engine = RAGQueryEngine()
    return _rag_engine


def set_context(texts: List[str]) -> None:
    """设置查询上下文（全局函数）"""
    get_rag_engine().set_context(texts)


def query_knowledge(question: str, context: List[str] | None = None) -> Optional[str]:
    """全局查询函数（同步，内部驱动异步路径）"""
    return asyncio.run(get_rag_engine().query_async(question, context=context))


async def query_knowledge_async(
    question: str, context: List[str] | None = None
) -> Optional[str]:
    """全局查询函数（异步）"""
    return await get_rag_engine().query_async(question, context=context)


__all__ = [
    "RAGQueryEngine",
    "get_rag_engine",
    "set_context",
    "query_knowledge",
    "query_knowledge_async",
]
