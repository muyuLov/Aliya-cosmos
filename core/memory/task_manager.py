"""并发任务管理模块

五元组提取的异步任务队列，支持并发 worker 协程。

重构要点：
- asyncio.Queue 移至 start() 方法内初始化，避免模块导入时无事件循环报错
- 使用 get_task_manager() 懒加载工厂，替代模块级实例化
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.logger import get_logger

from core.memory.config import get_grag_config
from core.memory import extractor
from core.memory.exceptions import TaskQueueFullError, TaskTimeoutError, TaskExecutionError

logger = get_logger(__name__)

# 五元组类型别名
QuintupleType = Tuple[str, str, str, str, str]


class TaskStatus(Enum):
    """任务状态枚举"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ExtractionTask:
    """五元组提取任务"""
    task_id: str
    text: str
    text_hash: str
    source_text: str = ""
    session_id: str = ""
    day_date: str = ""
    timeline: str = ""
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[List[QuintupleType]] = None
    error: Optional[str] = None
    retry_count: int = 0
    # future 在 add_task 时按需创建，不在 dataclass 默认中初始化（避免跨事件循环）
    future: Optional[asyncio.Future] = field(default=None)


class QuintupleTaskManager:
    """五元组提取任务管理器

    Note: asyncio.Queue 在 start() 内创建，保证绑定到正确的事件循环。
          __init__ 仅存储配置，不创建任何异步对象。

    依赖注入: extract_func 参数允许测试时注入 mock 提取函数。
    """

    def __init__(
        self,
        max_workers: int | None = None,
        max_queue_size: int | None = None,
        extract_func: Callable | None = None,
    ):
        """
        初始化任务管理器（仅存储配置，不创建异步对象）

        Args:
            max_workers:    最大并发工作协程数，None 时从配置读取
            max_queue_size: 最大任务队列大小，None 时从配置读取
            extract_func:   五元组提取函数（async），None 时使用默认 extractor.extract_quintuples
        """
        cfg = get_grag_config()

        self.max_workers: int = max_workers if max_workers is not None else cfg.task_manager.max_workers
        self.max_queue_size: int = max_queue_size if max_queue_size is not None else cfg.task_manager.max_queue_size
        self.task_timeout: int = cfg.task_manager.task_timeout
        self.auto_cleanup_hours: int = cfg.task_manager.auto_cleanup_hours
        self.cleanup_interval: int = cfg.task_manager.cleanup_interval_seconds

        # 任务存储
        self.tasks: Dict[str, ExtractionTask] = {}
        # text_hash → task_id 辅助索引（add_task 去重 O(1)，替代 O(n) 遍历）
        self._hash_to_active_task: Dict[str, str] = {}

        # 异步对象（延迟到 start() 中创建）
        self.task_queue: Optional[asyncio.Queue[ExtractionTask]] = None
        self.lock: Optional[asyncio.Lock] = None

        # 工作协程管理
        self.worker_tasks: List[asyncio.Task] = []
        self.is_running: bool = False

        # 统计信息
        self.completed_tasks: int = 0
        self.failed_tasks: int = 0

        # 回调函数
        self.on_task_completed: Optional[Callable[[ExtractionTask], None]] = None
        self.on_task_failed: Optional[Callable] = None

        # 依赖注入：提取函数（None 时在 _worker_loop 中回退到默认实现）
        self._extract_func: Callable | None = extract_func

        # 自动清理任务
        self.cleanup_task: Optional[asyncio.Task] = None

        logger.info(
            f"任务管理器配置加载完成: workers={self.max_workers}, "
            f"queue_size={self.max_queue_size}"
        )

    def _ensure_async_objects(self) -> None:
        """确保异步对象已在当前事件循环中创建"""
        if self.task_queue is None:
            self.task_queue = asyncio.Queue(maxsize=self.max_queue_size)
        if self.lock is None:
            self.lock = asyncio.Lock()

    async def start(self) -> None:
        """启动任务管理器（在当前事件循环中创建 Queue 和 workers）"""
        if self.is_running:
            logger.debug("任务管理器已在运行")
            return

        logger.info("正在启动任务管理器...")

        # 在事件循环中创建异步对象
        self._ensure_async_objects()

        self.is_running = True

        try:
            loop = asyncio.get_running_loop()

            # 创建工作协程
            self.worker_tasks = []
            for i in range(self.max_workers):
                worker_task = loop.create_task(
                    self._worker_loop(f"worker-{i + 1}"),
                    name=f"quintuple_worker_{i}",
                )
                self.worker_tasks.append(worker_task)

            # 启动自动清理任务
            self.cleanup_task = asyncio.create_task(self._auto_cleanup_loop())
            logger.info(f"任务管理器已启动，工作协程数: {self.max_workers}")

            # 让出控制权，确保 worker 协程有机会进入主循环后再返回
            await asyncio.sleep(0)

        except Exception as e:
            logger.error(f"启动任务管理器失败: {e}")
            self.is_running = False
            raise

    async def shutdown(self, drain_timeout: float = 60.0) -> None:
        """停止任务管理器

        先等待队列中所有任务处理完毕（或超时），再取消 worker。

        Args:
            drain_timeout: 等待队列排空的最长秒数，超时后强制取消剩余任务
        """
        if not self.is_running:
            return

        # 等待队列排空（graceful drain）
        if self.task_queue is not None and not self.task_queue.empty():
            logger.info(
                "等待队列排空（剩余 %d 个任务，超时 %.0fs）...",
                self.task_queue.qsize(), drain_timeout,
            )
            try:
                await asyncio.wait_for(self.task_queue.join(), timeout=drain_timeout)
                logger.info("队列已排空")
            except asyncio.TimeoutError:
                logger.warning("等待队列排空超时（%.0fs），强制停止", drain_timeout)

        self.is_running = False

        # 取消所有工作协程
        for task in self.worker_tasks:
            task.cancel()

        # 等待工作协程完成
        if self.worker_tasks:
            await asyncio.gather(*self.worker_tasks, return_exceptions=True)
        self.worker_tasks = []

        # 取消清理任务
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
            self.cleanup_task = None

        logger.info("任务管理器已停止")

    def _generate_task_id(self, text: str, timeline: str = "") -> str:
        """生成唯一的任务ID"""
        # 包含时间链信息确保不同时间链的相同文本生成不同ID
        text_with_timeline = f"{text}|{timeline}" if timeline else text
        text_hash = hashlib.sha256(text_with_timeline.encode()).hexdigest()
        timestamp = int(time.time() * 1000)
        return f"extract_{text_hash[:8]}_{timestamp}"

    def _generate_text_hash(self, text: str, timeline: str = "") -> str:
        """生成文本哈希值（包含时间链信息）"""
        # 包含时间链信息确保不同时间链的相同文本生成不同哈希
        text_with_timeline = f"{text}|{timeline}" if timeline else text
        return hashlib.sha256(text_with_timeline.encode()).hexdigest()

    async def add_task(
        self, text: str, source_text: str = "", session_id: str = "",
        day_date: str = "", timeline: str = "",
    ) -> str:
        """
        添加新的提取任务

        Args:
            text:        待提取的文本
            source_text: 原始来源文本（用于图谱关系元属性）
            session_id:  会话 ID（用于图谱关系元属性）
            day_date:    日期字符串，如 "2026-06-01"（用于关联 Day 节点）
            timeline:    时间链标识，如 "user" 或 "aliya"

        Returns:
            任务 ID

        Raises:
            ValueError:     文本为空
            TaskQueueFullError: 队列已满
        """
        if not text or not text.strip():
            raise ValueError("文本不能为空")

        # 确保已启动（自动启动）
        if not self.is_running:
            await self.start()

        self._ensure_async_objects()

        text_hash = self._generate_text_hash(text, timeline)
        task_id = self._generate_task_id(text, timeline)

        # 检查重复 + 创建 + 添加（原子操作，消除竞态窗口，O(1) 去重）
        async with self.lock:  # type: ignore[union-attr]
            existing_id = self._hash_to_active_task.get(text_hash)
            if existing_id is not None:
                existing = self.tasks.get(existing_id)
                if existing and existing.status in (
                    TaskStatus.PENDING,
                    TaskStatus.RUNNING,
                ):
                    logger.debug(f"发现重复任务: {existing.task_id}")
                    return existing.task_id
                # 索引中记录的任务已完成/取消，清理过期索引
                del self._hash_to_active_task[text_hash]

            task = ExtractionTask(
                task_id=task_id,
                text=text,
                text_hash=text_hash,
                source_text=source_text,
                session_id=session_id,
                day_date=day_date,
                timeline=timeline,
                status=TaskStatus.PENDING,
                created_at=time.time(),
                future=asyncio.get_running_loop().create_future(),
            )
            self.tasks[task_id] = task
            self._hash_to_active_task[text_hash] = task_id

        # 将任务放入队列
        try:
            await asyncio.wait_for(
                self.task_queue.put(task),  # type: ignore[union-attr]
                timeout=5.0,
            )
            logger.debug(f"任务已加入队列: {task_id}")
            return task_id

        except asyncio.TimeoutError:
            if task.future and not task.future.done():
                task.future.cancel()
            async with self.lock:  # type: ignore[union-attr]
                if task_id in self.tasks:
                    del self.tasks[task_id]
                # 同步清理 _hash_to_active_task，防止文本永远无法重新提交
                self._hash_to_active_task.pop(text_hash, None)
            raise TaskQueueFullError(
                queue_size=self.task_queue.qsize(),  # type: ignore[union-attr]
                max_size=self.max_queue_size,
            )

    async def get_task_result(
        self, task_id: str, timeout: float | None = None
    ) -> Tuple[List[QuintupleType] | None, str | None]:
        """
        获取任务结果

        Args:
            task_id: 任务 ID
            timeout: 超时时间（秒）

        Returns:
            (结果列表, 错误信息) 元组
        """
        assert self.lock is not None
        async with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return None, f"任务不存在: {task_id}"

            if task.status == TaskStatus.COMPLETED:
                return task.result, None
            elif task.status in [TaskStatus.FAILED, TaskStatus.CANCELLED]:
                return None, task.error or "任务失败或被取消"

        if task.future is None:
            return None, "任务无 future 对象"

        # 等待任务完成
        try:
            timeout_value = timeout if timeout is not None else self.task_timeout
            await asyncio.wait_for(asyncio.shield(task.future), timeout=timeout_value)
            if task.status == TaskStatus.COMPLETED:
                return task.result, None
            return None, task.error or "任务失败"
        except asyncio.TimeoutError:
            return None, "任务超时"
        except asyncio.CancelledError:
            return None, "任务被取消"

    async def _worker_loop(self, worker_id: str) -> None:
        """工作协程主循环"""
        logger.debug(f"{worker_id} 已启动")

        while self.is_running:
            try:
                # 从队列获取任务（带超时）
                try:
                    task = await asyncio.wait_for(
                        self.task_queue.get(),  # type: ignore[union-attr]
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    continue

                # 原子操作：检查 PENDING 并转为 RUNNING，与 cancel_task 互斥
                assert self.lock is not None
                async with self.lock:
                    if task.status != TaskStatus.PENDING:
                        self.task_queue.task_done()  # type: ignore[union-attr]
                        continue
                    task.status = TaskStatus.RUNNING
                    task.started_at = time.time()

                result: List[QuintupleType] | None = None
                error: str | None = None

                try:
                    extract_fn = self._extract_func or extractor.extract_quintuples
                    result = await asyncio.wait_for(
                        extract_fn(task.text),
                        timeout=self.task_timeout,
                    )
                    final_status = TaskStatus.COMPLETED

                except asyncio.TimeoutError:
                    error = "任务执行超时"
                    final_status = TaskStatus.FAILED

                except Exception as e:
                    error = str(e)
                    final_status = TaskStatus.FAILED
                    logger.error(f"{worker_id} 任务失败: {task.task_id}: {error}")

                # 锁内原子写入最终状态，与 cancel_task 互斥
                # future 设置也置于锁内，消除与 cancel_task 之间的竞态窗口：
                # 若锁内检查 status != CANCELLED 后立即被 cancel_task 取消 future，
                # 脱离锁后再 set_result 将引发 asyncio.InvalidStateError
                assert self.lock is not None
                async with self.lock:
                    if task.status == TaskStatus.CANCELLED:
                        # 提取期间被取消，丢弃结果
                        self.task_queue.task_done()
                        continue

                    task.status = final_status
                    task.completed_at = time.time()
                    if final_status == TaskStatus.COMPLETED:
                        task.result = result
                        self.completed_tasks += 1
                    else:
                        task.error = error
                        self.failed_tasks += 1
                    # 任务结束，从活跃去重索引中移除
                    self._hash_to_active_task.pop(task.text_hash, None)

                    # 设置 future 结果（锁内操作，与 cancel_task 中的 future.cancel() 互斥）
                    if task.future and not task.future.done():
                        try:
                            if task.status == TaskStatus.COMPLETED:
                                task.future.set_result(result)
                            else:
                                task.future.set_exception(Exception(error or "任务失败"))
                        except asyncio.InvalidStateError:
                            # cancel_task 已在竞态中先行取消 future
                            pass

                # 触发回调
                if task.status == TaskStatus.COMPLETED and self.on_task_completed:
                    try:
                        self.on_task_completed(task)
                    except Exception as e:
                        logger.error(f"任务回调失败: {e}")
                elif task.status == TaskStatus.FAILED and self.on_task_failed:
                    try:
                        self.on_task_failed(task.task_id, error)
                    except Exception as e:
                        logger.error(f"任务失败回调失败: {e}")

                self.task_queue.task_done()  # type: ignore[union-attr]

            except asyncio.CancelledError:
                logger.debug(f"{worker_id} 被取消")
                break
            except Exception as e:
                logger.error(f"{worker_id} 工作协程异常: {e}")
                logger.error(traceback.format_exc())
                await asyncio.sleep(1)

    async def clear_completed_tasks(self, max_age_hours: int | None = None) -> int:
        """
        清理已完成的任务

        Args:
            max_age_hours: 保留时间（小时），None 时使用配置值

        Returns:
            清理的任务数量
        """
        if max_age_hours is None:
            max_age_hours = self.auto_cleanup_hours

        current_time = time.time()
        max_age_seconds = max_age_hours * 3600
        removed_count = 0

        assert self.lock is not None
        async with self.lock:
            tasks_to_remove = [
                task_id
                for task_id, task in self.tasks.items()
                if task.status in [
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                ]
                and task.completed_at is not None
                and (current_time - task.completed_at) > max_age_seconds
            ]

            for task_id in tasks_to_remove:
                del self.tasks[task_id]
                removed_count += 1

        if removed_count > 0:
            logger.info(f"清理了 {removed_count} 个过期任务")

        return removed_count

    def get_task_text_hash(self, task_id: str) -> str | None:
        """获取任务的文本哈希"""
        task = self.tasks.get(task_id)
        return task.text_hash if task else None

    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务状态

        返回的是读取时刻的快照，可能在该快照返回后立即变化。
        """
        task = self.tasks.get(task_id)
        if not task:
            return None

        return {
            "task_id": task.task_id,
            "status": task.status.value,
            "created_at": task.created_at,
            "started_at": task.started_at,
            "completed_at": task.completed_at,
            "result_count": len(task.result) if task.result else 0,
            "error": task.error,
            "retry_count": task.retry_count,
        }

    def get_all_tasks(self) -> List[Dict[str, Any]]:
        """获取所有任务状态

        返回的是读取时刻的快照，任务列表在返回后可能变化。
        """
        tasks_snapshot = list(self.tasks.values())
        return [
            {
                "task_id": t.task_id,
                "status": t.status.value,
                "created_at": t.created_at,
                "started_at": t.started_at,
                "completed_at": t.completed_at,
                "result_count": len(t.result) if t.result else 0,
                "error": t.error,
                "retry_count": t.retry_count,
            }
            for t in tasks_snapshot
        ]

    def get_stats(self) -> Dict[str, Any]:
        """获取任务管理器统计信息

        所有计数器和任务统计在同一时刻的快照上计算。
        """
        queue_size = self.task_queue.qsize() if self.task_queue else 0
        tasks_snapshot = list(self.tasks.values())
        return {
            "is_running": self.is_running,
            "total_tasks": len(self.tasks),
            "pending_tasks": sum(
                1 for t in tasks_snapshot if t.status == TaskStatus.PENDING
            ),
            "running_tasks": sum(
                1 for t in tasks_snapshot if t.status == TaskStatus.RUNNING
            ),
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "max_workers": self.max_workers,
            "max_queue_size": self.max_queue_size,
            "queue_size": queue_size,
            "task_timeout": self.task_timeout,
        }

    async def cancel_task(self, task_id: str) -> bool:
        """取消任务

        取消 PENDING / RUNNING 状态的任务。已开始执行的 LLM 调用
        无法被中断（Python asyncio 限制），但 worker 在提取完成后会
        检测到 CANCELLED 状态并丢弃结果。

        Returns:
            是否成功将任务标记为取消
        """
        assert self.lock is not None
        async with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return False

            if task.status in [TaskStatus.PENDING, TaskStatus.RUNNING]:
                task.status = TaskStatus.CANCELLED
                task.completed_at = time.time()
                # 从活跃去重索引中移除
                self._hash_to_active_task.pop(task.text_hash, None)

                if task.future and not task.future.done():
                    task.future.cancel()

                logger.info(f"任务已取消: {task_id}")
                return True

            return False

    async def _auto_cleanup_loop(self) -> None:
        """自动清理任务循环"""
        logger.debug("自动清理任务已启动，间隔 %d 秒", self.cleanup_interval)

        while self.is_running:
            try:
                await asyncio.sleep(self.cleanup_interval)
                await self.clear_completed_tasks()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"自动清理失败: {e}")
                await asyncio.sleep(60)


# ─────────────────────────── 懒加载工厂 ───────────────────────────

_task_manager_instance: Optional[QuintupleTaskManager] = None
_task_manager_lock = threading.Lock()


def get_task_manager() -> QuintupleTaskManager:
    """
    获取任务管理器单例（线程安全懒加载）

    首次调用时才创建 QuintupleTaskManager 实例，
    避免模块导入时因无事件循环而触发 asyncio.Queue 创建问题。
    """
    global _task_manager_instance
    if _task_manager_instance is None:
        with _task_manager_lock:
            if _task_manager_instance is None:
                _task_manager_instance = QuintupleTaskManager()
    return _task_manager_instance


# ─────────────────────────── 便捷函数 ───────────────────────────


async def start_task_manager() -> None:
    """启动任务管理器"""
    mgr = get_task_manager()
    if not mgr.is_running:
        await mgr.start()


async def stop_task_manager() -> None:
    """停止任务管理器"""
    mgr = get_task_manager()
    if mgr.is_running:
        await mgr.shutdown()


__all__ = [
    "TaskStatus",
    "ExtractionTask",
    "QuintupleTaskManager",
    "get_task_manager",
    "start_task_manager",
    "stop_task_manager",
]
