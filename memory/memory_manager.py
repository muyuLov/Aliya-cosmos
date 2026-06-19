"""记忆管理器集成层

参考 NagaAgent-main/summer_memory/memory_manager.py
集成五元组提取、图谱存储、RAG 查询、任务管理
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
import traceback
import weakref
from typing import Any, Dict, List, Optional, Tuple

from core.logger import get_logger

from memory.config import get_grag_config
from memory.exceptions import GRAGNotEnabledError, GRAGError
from memory import extractor, graph, rag_query, task_manager as task_manager_module

logger = get_logger(__name__)

# 五元组类型别名
QuintupleType = Tuple[str, str, str, str, str]

# 默认 AI 名称
DEFAULT_AI_NAME = "Aliya"


class GRAGMemoryManager:
    """GRAG 知识图谱记忆管理器"""

    def __init__(self, ai_name: str = DEFAULT_AI_NAME):
        """
        初始化记忆管理器

        Args:
            ai_name: AI 角色名称，用于格式化对话文本
        """
        cfg = get_grag_config()

        self.enabled = cfg.enabled
        self.auto_extract = cfg.auto_extract
        self.context_length = cfg.context_length
        self.ai_name = ai_name

        # 最近对话上下文
        self.recent_context: List[str] = []

        # 提取缓存（避免重复提取）
        self.extraction_cache: set = set()

        # 当前活跃的任务 ID
        self.active_tasks: set = set()

        if not self.enabled:
            logger.info("GRAG 记忆系统已禁用")
            return

        try:
            logger.info("GRAG 记忆系统初始化成功（Neo4j 连接保持惰性）")

            # 保存主事件循环引用，用于跨线程回调
            try:
                self._main_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
            except RuntimeError:
                self._main_loop = None

            # 设置任务完成回调（使用 weakref 避免循环引用）
            self._weak_ref = weakref.ref(self)
            task_manager_module.get_task_manager().on_task_completed = (
                self._on_task_completed_wrapper
            )

        except Exception as e:
            logger.error(f"GRAG 记忆系统初始化失败: {e}")
            self.enabled = False

    async def add_conversation_memory(
        self,
        user_input: str,
        ai_response: str,
        session_id: str = "",
    ) -> bool:
        """
        添加对话记忆到知识图谱

        Args:
            user_input:  用户输入
            ai_response: AI 响应
            session_id:  会话 ID（用于图谱关系元属性）

        Returns:
            是否成功
        """
        if not self.enabled:
            return False

        try:
            # 拼接本轮内容
            conversation_text = f"用户: {user_input}\n{self.ai_name}: {ai_response}"
            logger.info(f"添加对话记忆: {conversation_text[:50]}...")

            # 更新 recent_context
            self.recent_context.append(conversation_text)
            if len(self.recent_context) > self.context_length:
                self.recent_context = self.recent_context[-self.context_length:]

            # 使用任务管理器异步提取五元组
            if self.auto_extract:
                await self._submit_extraction_task(conversation_text, session_id)

            return True

        except Exception as e:
            logger.error(f"添加对话记忆失败: {e}")
            return False

    async def _submit_extraction_task(
        self, text: str, session_id: str = ""
    ) -> None:
        """提交五元组提取任务"""
        try:
            mgr = task_manager_module.get_task_manager()

            # 确保任务管理器已启动
            if not mgr.is_running:
                await task_manager_module.start_task_manager()
                await asyncio.sleep(0.5)

            # 检查是否已提取过
            text_hash = hashlib.sha256(text.encode()).hexdigest()
            if text_hash in self.extraction_cache:
                logger.debug(f"跳过已处理的文本: {text[:50]}...")
                return

            # 提交任务
            task_id = await mgr.add_task(text)
            self.active_tasks.add(task_id)
            logger.info(f"已提交五元组提取任务: {task_id}")

        except Exception as e:
            logger.error(f"提交提取任务失败: {e}")
            # 回退到同步提取
            await self._extract_and_store_sync(text, session_id)

    async def _extract_and_store_sync(
        self, text: str, session_id: str = ""
    ) -> bool:
        """同步提取并存储五元组（回退方案）"""
        try:
            text_hash = hashlib.sha256(text.encode()).hexdigest()

            if text_hash in self.extraction_cache:
                return True

            logger.info(f"使用回退方法提取五元组: {text[:100]}...")

            # 提取五元组
            quintuples = await extractor.extract_quintuples(text)
            if not quintuples:
                logger.debug("未提取到五元组")
                return False

            # 存储到图谱（使用异步接口）
            success = await graph.store_quintuples_async(
                quintuples,
                source_text=text,
                session_id=session_id,
            )
            if success:
                self.extraction_cache.add(text_hash)
                logger.info(f"回退方法存储 {len(quintuples)} 个五元组成功")

            return success

        except Exception as e:
            logger.error(f"同步提取存储失败: {e}")
            return False

    def _on_task_completed_wrapper(
        self, task_id: str, quintuples: List[QuintupleType]
    ) -> None:
        """任务完成回调包装（处理跨线程调用）"""
        instance = self._weak_ref()
        if not instance:
            return

        loop = instance._main_loop
        if loop is None:
            logger.warning(f"任务回调无主事件循环，丢弃五元组: {task_id}")
            instance.active_tasks.discard(task_id)
            return

        try:
            asyncio.run_coroutine_threadsafe(
                instance._on_task_completed(task_id, quintuples),
                loop,
            )
        except Exception as e:
            logger.error(f"任务回调调度失败: {e}")
            instance.active_tasks.discard(task_id)

    async def _on_task_completed(
        self, task_id: str, quintuples: List[QuintupleType]
    ) -> None:
        """任务完成回调"""
        try:
            self.active_tasks.discard(task_id)
            logger.info(f"任务完成: {task_id}, 五元组数: {len(quintuples)}")

            if not quintuples:
                logger.debug(f"任务 {task_id} 未提取到五元组")
                return

            # 存储到图谱（使用异步接口）
            success = await graph.store_quintuples_async(quintuples)
            if success:
                logger.info(f"成功存储 {len(quintuples)} 个五元组到图谱")
                # 更新提取缓存（与 _extract_and_store_sync 保持一致）
                mgr = task_manager_module.get_task_manager()
                task_obj = mgr.tasks.get(task_id)
                if task_obj:
                    self.extraction_cache.add(task_obj.text_hash)

            # 更新 RAG 上下文
            rag_query.set_context(self.recent_context)

        except Exception as e:
            logger.error(f"任务完成回调处理失败: {e}")

    async def query_memory(self, question: str) -> Optional[str]:
        """
        查询记忆

        Args:
            question: 用户问题

        Returns:
            回答文本，无结果时返回 None
        """
        if not self.enabled:
            return None

        try:
            # 更新 RAG 上下文
            rag_query.set_context(self.recent_context)

            # 执行查询
            result = await rag_query.query_knowledge_async(question)

            if result and "未在知识图谱中找到相关信息" not in result:
                logger.info("从记忆中找到相关信息")
                return result

            return None

        except Exception as e:
            logger.error(f"查询记忆失败: {e}")
            return None

    async def get_relevant_memories(
        self, query: str, limit: int = 3
    ) -> List[QuintupleType]:
        """
        获取相关记忆（五元组格式）

        Args:
            query: 查询文本
            limit: 返回数量限制

        Returns:
            相关五元组列表
        """
        if not self.enabled:
            return []

        try:
            cfg = get_grag_config()
            # 从图谱查询相关五元组（使用异步接口，传入相似度阈值）
            quintuples = await graph.query_graph_by_keywords_async(
                [query], limit=limit,
                similarity_threshold=cfg.similarity_threshold,
            )
            return quintuples[:limit]

        except Exception as e:
            logger.error(f"获取相关记忆失败: {e}")
            return []

    def get_memory_stats(self) -> Dict[str, Any]:
        """
        获取记忆统计信息

        Returns:
            统计信息字典
        """
        if not self.enabled:
            return {"enabled": False}

        try:
            mgr = task_manager_module.get_task_manager()
            task_stats = mgr.get_stats()
            graph_stats = graph.get_graph_stats()

            return {
                "enabled": True,
                "context_length": len(self.recent_context),
                "cache_size": len(self.extraction_cache),
                "active_tasks": len(self.active_tasks),
                "task_manager": task_stats,
                "graph": graph_stats,
            }

        except Exception as e:
            logger.error(f"获取记忆统计失败: {e}")
            return {"enabled": False, "error": str(e)}

    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务状态"""
        return task_manager_module.get_task_manager().get_task_status(task_id)

    def get_all_task_status(self) -> List[Dict[str, Any]]:
        """获取所有任务状态"""
        return task_manager_module.get_task_manager().get_all_tasks()

    async def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        self.active_tasks.discard(task_id)
        return await task_manager_module.get_task_manager().cancel_task(task_id)

    async def clear_memory(self) -> bool:
        """
        清空记忆

        Returns:
            是否成功
        """
        if not self.enabled:
            return False

        try:
            self.recent_context.clear()
            self.extraction_cache.clear()

            # 取消所有活跃任务
            mgr = task_manager_module.get_task_manager()
            for task_id in list(self.active_tasks):
                await mgr.cancel_task(task_id)
            self.active_tasks.clear()

            # 清空图谱（使用异步接口）
            await graph.clear_all_quintuples_async()

            logger.info("记忆已清空")
            return True

        except Exception as e:
            logger.error(f"清空记忆失败: {e}")
            return False


# 全局记忆管理器实例（懒加载，首次访问时创建）
_memory_manager_instance: Optional[GRAGMemoryManager] = None
_memory_manager_lock = threading.Lock()


def get_memory_manager() -> GRAGMemoryManager:
    """获取记忆管理器单例（线程安全懒加载）。"""
    global _memory_manager_instance
    if _memory_manager_instance is None:
        with _memory_manager_lock:
            if _memory_manager_instance is None:
                _memory_manager_instance = GRAGMemoryManager()
    return _memory_manager_instance


