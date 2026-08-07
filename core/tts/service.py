"""TTS 服务：管理合成会话生命周期，集成日志与异常处理"""

from __future__ import annotations

import asyncio
import time
from typing import AsyncGenerator, AsyncIterator

from core.logger import get_logger
from core.tts.constants import (
    DEFAULT_MAX_CONCURRENT_CREATES,
    DEFAULT_PREFETCH_QUEUE_SIZE,
    DEFAULT_PREFETCH_WINDOW,
    SENTINEL,
)
from core.tts.exceptions import TTSRequestError, TTSSessionError
from core.tts.models import TTSRequest, VoiceConfig
from core.tts.providers.base import TTSProvider
from core.tts.text_splitter import filter_actions, split_text

_logger = get_logger(__name__)


class TTSService:
    """
    TTS 服务，封装会话式流式合成的完整生命周期，支持 ``async with`` 自动管理资源。

    文本按句末标点分段，流水线并发预取，降低首字节延迟与段间停顿。

    Args:
        provider: 具体 TTS 提供商实例。
        voice_config: 音色默认配置，为 None 时使用空配置。
        prefetch_queue_size: 预取队列大小，控制并发预取的段数，默认 16。
        max_concurrent_creates: 最大并发创建会话数，限制同时创建的 TTS 会话数量，默认 10。
    """

    def __init__(
        self,
        provider: TTSProvider,
        voice_config: VoiceConfig | None = None,
        prefetch_queue_size: int = DEFAULT_PREFETCH_QUEUE_SIZE,
        max_concurrent_creates: int = DEFAULT_MAX_CONCURRENT_CREATES,
        prefetch_window: int = DEFAULT_PREFETCH_WINDOW,
    ) -> None:
        # 参数验证（集中校验）
        from core.tts.validation import validate_tts_service_config

        validate_tts_service_config(prefetch_queue_size, max_concurrent_creates, prefetch_window)

        self.provider = provider
        self.voice_config = voice_config or VoiceConfig()
        self.prefetch_queue_size = prefetch_queue_size
        self.prefetch_window = prefetch_window
        # 每个实例独立的 Semaphore，避免不同服务实例互相限流
        self._create_sem = asyncio.Semaphore(max_concurrent_creates)

    async def __aenter__(self) -> "TTSService":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def synthesize(self, request: TTSRequest) -> AsyncIterator[bytes]:
        """
        流式合成，按原文顺序 yield 音频块，记录 TTFB 与总耗时。

        单句文本直接合成，跳过分段流程；多句文本走并发分段流水线。

        Args:
            request: TTS 合成请求，None 字段由 voice_config 填充默认值。

        Yields:
            音频字节块。

        Raises:
            TTSRequestError: 创建会话失败时抛出。
            TTSSessionError: 消费流失败时抛出。
        """
        merged = self.voice_config.apply_to_request(request)
        
        # 过滤动作描写（括号内容）
        original_text = merged.text
        filtered_text = filter_actions(original_text)
        if filtered_text != original_text:
            _logger.debug(
                "TTS 文本过滤 | provider=%s | 原长度=%d | 过滤后=%d",
                self.provider.provider_name,
                len(original_text),
                len(filtered_text),
            )
            merged = merged.model_copy(update={"text": filtered_text})
        
        _logger.debug(
            "开始 TTS 合成 | provider=%s | text_length=%d",
            self.provider.provider_name,
            len(merged.text),
        )

        start_time = time.monotonic()
        _completed = False

        # 单句直接合成，跳过分段开销；多句走并发流水线
        segments = split_text(merged.text)
        source: AsyncIterator[bytes]
        if len(segments) == 0:
            _logger.debug("TTS 空文本，跳过合成 | provider=%s", self.provider.provider_name)
            return
        elif len(segments) == 1:
            source = self._synthesize_single(merged)
        else:
            source = self._synthesize_segmented(merged)

        first_chunk = True
        try:
            async for chunk in source:
                if first_chunk:
                    first_chunk = False
                    _logger.debug(
                        "TTS 首字节 | provider=%s | ttfb=%.3fs",
                        self.provider.provider_name,
                        time.monotonic() - start_time,
                    )
                yield chunk
            _completed = True
        except (TTSRequestError, TTSSessionError):
            _logger.error(
                "TTS 合成失败 | provider=%s | elapsed=%.3fs",
                self.provider.provider_name,
                time.monotonic() - start_time,
                exc_info=True,
            )
            raise
        finally:
            # 消费端提前退出时显式关闭底层生成器，释放会话资源
            await self._safe_aclose(source)
            if _completed:
                _logger.debug(
                    "TTS 合成完成 | provider=%s | elapsed=%.3fs",
                    self.provider.provider_name,
                    time.monotonic() - start_time,
                )

    async def _safe_aclose(self, source: AsyncIterator[bytes]) -> None:
        """安全关闭异步生成器，吞掉关闭阶段的噪音异常。"""
        aclose = getattr(source, "aclose", None)
        if aclose is None:
            return
        try:
            await aclose()
        except RuntimeError as e:
            # 忽略 "asynchronous generator is already running" 错误
            if "already running" not in str(e):
                raise
            _logger.debug(
                "TTS 生成器关闭时检测到并发状态 | provider=%s",
                self.provider.provider_name,
            )
        except Exception as e:
            _logger.debug(
                "TTS 生成器关闭失败 | provider=%s | error=%s",
                self.provider.provider_name,
                e,
            )

    async def _synthesize_single(self, request: TTSRequest) -> AsyncGenerator[bytes, None]:
        """
        单句直接合成，跳过分段与队列开销。

        Args:
            request: 已合并默认值的 TTS 请求。

        Yields:
            音频字节块。
        """
        session_id = await self.provider.create_session(request)
        try:
            async for chunk in self.provider.consume_session(session_id):
                yield chunk
        finally:
            try:
                await self.provider.close_session(session_id)
            except (TTSSessionError, TTSRequestError) as e:
                _logger.debug(
                    "TTS 单句会话释放失败 | session_id=%s | error=%s",
                    session_id,
                    e,
                )
            except Exception as e:
                _logger.debug(
                    "TTS 单句会话释放异常 | session_id=%s | error=%s",
                    session_id,
                    e,
                )

    async def _synthesize_segmented(self, request: TTSRequest) -> AsyncGenerator[bytes, None]:
        """
        分段流水线合成：滑动窗口预取，减少资源浪费，支持快速打断。

        窗口大小由 prefetch_window 控制（默认 3）：
        - 仅预取接下来 N 段，而非全量并发；
        - 消费完第 i 段后，再创建第 i+N 段的任务；
        - 用户提前停止时，未开始的段不会浪费 session 资源。
        """
        segments = split_text(request.text)
        _logger.debug(
            "TTS 分段合成（滑动窗口） | segments=%d | window=%d | text_length=%d",
            len(segments),
            self.prefetch_window,
            len(request.text),
        )

        queues: list[asyncio.Queue[object]] = [
            asyncio.Queue(maxsize=self.prefetch_queue_size) for _ in segments
        ]
        tasks: dict[int, asyncio.Task[None]] = {}  # idx -> task
        created: set[int] = set()

        async def _create_and_prefetch(seg: str, queue: asyncio.Queue[object]) -> None:
            """创建 session 后立即预取，异常写入队列由消费端在正确位置抛出。"""
            seg_request = request.model_copy(update={"text": seg})
            session_id: str | None = None
            session_iter: AsyncGenerator[bytes, None] | None = None

            try:
                async with self._create_sem:
                    session_id = await self.provider.create_session(seg_request)
                session_iter = self.provider.consume_session(session_id)
                async for chunk in session_iter:
                    await queue.put(chunk)
                await queue.put(SENTINEL)
            except asyncio.CancelledError:
                if session_iter is not None:
                    try:
                        await session_iter.aclose()
                    except Exception:
                        pass
                try:
                    queue.put_nowait(SENTINEL)
                except asyncio.QueueFull:
                    pass
                raise
            except (TTSRequestError, TTSSessionError) as e:
                try:
                    queue.put_nowait(e)
                except asyncio.QueueFull:
                    await queue.put(e)
            except Exception as e:
                _logger.error(
                    "TTS 分段任务异常 | segment 预取失败 | error=%s", e, exc_info=True
                )
                try:
                    queue.put_nowait(e)
                except asyncio.QueueFull:
                    await queue.put(e)
            finally:
                if session_id is not None:
                    try:
                        await self.provider.close_session(session_id)
                    except (TTSSessionError, TTSRequestError) as close_err:
                        _logger.debug(
                            "TTS 分段会话释放失败 | session_id=%s | error=%s",
                            session_id,
                            close_err,
                        )
                    except Exception as close_err:
                        _logger.debug(
                            "TTS 分段会话释放异常 | session_id=%s | error=%s",
                            session_id,
                            close_err,
                        )

        def schedule_task(idx: int) -> None:
            """调度第 idx 段的预取任务（非阻塞）。"""
            if idx in created or idx >= len(segments):
                return
            created.add(idx)
            task = asyncio.create_task(_create_and_prefetch(segments[idx], queues[idx]))
            tasks[idx] = task

        window = self.prefetch_window
        # 创建初始窗口内的任务
        for i in range(min(window, len(segments))):
            schedule_task(i)

        try:
            # 按序消费队列，滑动窗口
            for i, queue in enumerate(queues):
                # 消费完第 i 段后，调度第 i+window 段的任务
                schedule_task(i + window)

                while True:
                    item = await queue.get()
                    if item is SENTINEL:
                        break
                    if isinstance(item, BaseException):
                        raise item
                    assert isinstance(item, bytes)
                    yield item
        finally:
            # 取消所有未完成的任务
            for task in tasks.values():
                if not task.done():
                    task.cancel()
            if tasks:
                try:
                    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
                    for idx, result in enumerate(results):
                        if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                            _logger.debug(
                                "TTS 分段任务清理异常 | segment=%d | error=%s",
                                idx,
                                result,
                            )
                    _logger.debug(
                        "TTS 分段合成清理完成 | cancelled_tasks=%d",
                        sum(1 for t in tasks.values() if t.cancelled()),
                    )
                except Exception as cleanup_err:
                    _logger.debug(
                        "TTS 分段合成清理失败 | error=%s",
                        cleanup_err,
                    )
    async def aclose(self) -> None:
        """释放底层提供商资源，建议通过 ``async with`` 自动管理。"""
        await self.provider.aclose()
