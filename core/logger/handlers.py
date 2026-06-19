"""日志处理器：控制台彩色输出、异步文件轮转输出"""

import logging
import logging.handlers
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from core.logger.formatter import JSONFormatter, StructuredFormatter


def build_console_handler(
    level: int = logging.DEBUG,
    color: bool = True,
    structured: bool = False,
) -> logging.StreamHandler:
    """
    构建控制台日志处理器。

    Args:
        level: 处理器最低日志级别。
        color: 是否启用 ANSI 彩色输出。
        structured: 若为 True，使用 JSON 格式化器。

    Returns:
        配置好的 StreamHandler 实例。
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    formatter = JSONFormatter() if structured else StructuredFormatter(color=color)
    handler.setFormatter(formatter)
    return handler


class BufferedFileHandler(logging.Handler):
    """
    带缓冲的文件处理器，批量写入以提升 I/O 效率。

    特性：
    - 内存缓冲区累积日志记录，达到阈值或超时后批量写入
    - 自动刷新机制：定期检查缓冲区，避免日志延迟过久
    - 异常降级：写入失败时记录到备用位置，避免日志丢失
    """

    def __init__(
        self,
        base_handler: logging.Handler,
        buffer_size: int = 100,
        flush_interval: float = 5.0,
    ) -> None:
        """
        Args:
            base_handler: 底层文件处理器（RotatingFileHandler 或 TimedRotatingFileHandler）
            buffer_size: 缓冲区大小（条数），达到此值立即刷新
            flush_interval: 自动刷新间隔（秒），避免日志延迟过久
        """
        super().__init__()
        self._base_handler = base_handler
        self._buffer_size = buffer_size
        self._flush_interval = flush_interval
        self._buffer: list[logging.LogRecord] = []
        self._lock = threading.Lock()
        self._last_flush_time = time.time()
        self._stopped = False
        self._stop_event = threading.Event()

        # 启动后台刷新线程
        self._flush_thread = threading.Thread(
            target=self._auto_flush_loop, daemon=True, name="LogBufferFlusher"
        )
        self._flush_thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        """接收日志记录，加入缓冲区"""
        with self._lock:
            self._buffer.append(record)
            # 达到缓冲区大小阈值，立即刷新
            if len(self._buffer) >= self._buffer_size:
                self._flush_buffer()

    def _flush_buffer(self) -> None:
        """批量写入缓冲区中的所有日志（需持有锁）"""
        if not self._buffer:
            return

        records = self._buffer[:]
        self._buffer.clear()
        self._last_flush_time = time.time()

        # 释放锁后再执行 I/O，避免阻塞其他日志写入
        for record in records:
            try:
                self._base_handler.emit(record)
            except Exception:
                # 写入失败时降级到标准错误输出，避免日志完全丢失
                self._fallback_emit(record)

        # 确保数据落盘
        try:
            self._base_handler.flush()
        except Exception:
            pass

    def _auto_flush_loop(self) -> None:
        """后台线程：定期检查并刷新缓冲区（使用 Event.wait 支持优雅退出）"""
        while not self._stopped:
            if self._stop_event.wait(timeout=1.0):
                # 收到停止信号，立即退出
                break
            with self._lock:
                elapsed = time.time() - self._last_flush_time
                # 超过刷新间隔且缓冲区非空，执行刷新
                if elapsed >= self._flush_interval and self._buffer:
                    self._flush_buffer()

    def _fallback_emit(self, record: logging.LogRecord) -> None:
        """降级处理：写入失败时输出到 stderr"""
        try:
            msg = self.format(record)
            sys.stderr.write(f"[LOG_FALLBACK] {msg}\n")
            sys.stderr.flush()
        except Exception:
            pass  # 降级也失败则放弃，避免无限递归

    def flush(self) -> None:
        """强制刷新缓冲区"""
        with self._lock:
            self._flush_buffer()

    def close(self) -> None:
        """关闭处理器，刷新剩余日志并停止后台线程"""
        self._stopped = True
        self._stop_event.set()
        if self._flush_thread.is_alive():
            self._flush_thread.join(timeout=2.0)

        with self._lock:
            self._flush_buffer()

        self._base_handler.close()
        super().close()


class MonitoredQueueHandler(logging.handlers.QueueHandler):
    """
    带监控的队列处理器，支持队列深度告警。

    特性：
    - 有界队列：防止内存无限增长
    - 队列满时丢弃策略：记录告警并丢弃最旧的日志
    - 定期输出队列深度统计
    """

    def __init__(
        self,
        log_queue: queue.Queue[Any],
        max_queue_size: int = 10000,
        warn_threshold: float = 0.8,
    ) -> None:
        """
        Args:
            log_queue: 日志队列
            max_queue_size: 队列最大容量
            warn_threshold: 告警阈值（队列使用率），超过此值输出警告
        """
        super().__init__(log_queue)
        self._log_queue = log_queue  # 保留具体类型引用，避免通过 self.queue 访问时类型丢失
        self._max_queue_size = max_queue_size
        self._warn_threshold = warn_threshold
        self._drop_count = 0
        self._last_warn_time = 0.0

    def enqueue(self, record: logging.LogRecord) -> None:
        """重写入队逻辑，添加队列监控"""
        try:
            # 检查队列深度
            qsize = self._log_queue.qsize()
            usage_ratio = qsize / self._max_queue_size if self._max_queue_size > 0 else 0

            # 超过告警阈值，输出警告（限流：每 60 秒最多一次）
            now = time.time()
            if usage_ratio >= self._warn_threshold and (now - self._last_warn_time) > 60:
                sys.stderr.write(
                    f"[LOG_QUEUE_WARNING] 队列深度: {qsize}/{self._max_queue_size} "
                    f"({usage_ratio:.1%}), 已丢弃: {self._drop_count}\n"
                )
                sys.stderr.flush()
                self._last_warn_time = now

            # 队列满时丢弃最旧的记录
            if qsize >= self._max_queue_size:
                try:
                    self._log_queue.get_nowait()  # 移除最旧的记录
                    self._drop_count += 1
                except queue.Empty:
                    pass

            # 非阻塞入队
            self._log_queue.put_nowait(record)
        except queue.Full:
            # 理论上不会到达（已预先清理），但保留兜底逻辑
            self._drop_count += 1


def _generate_session_log_path(base_path: str | Path) -> Path:
    """
    生成带时间戳的会话日志文件路径。

    格式：原文件名_YYYYMMDD_HHMMSS.log
    例如：app.log -> app_20260427_123045.log

    Args:
        base_path: 基础日志文件路径

    Returns:
        带时间戳的日志文件路径
    """
    path = Path(base_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = path.stem  # 文件名（不含扩展名）
    suffix = path.suffix  # 扩展名
    new_name = f"{stem}_{timestamp}{suffix}"
    return path.parent / new_name


def build_file_handler(
    file_path: str | Path,
    level: int = logging.DEBUG,
    structured: bool = False,
    rotate: str = "session",
    # timed 模式参数
    when: str = "midnight",
    backup_count: int = 30,
    # sized 模式参数
    max_bytes: int = 10 * 1024 * 1024,
    # 优化参数
    buffer_size: int = 100,
    flush_interval: float = 5.0,
    max_queue_size: int = 10000,
) -> tuple[logging.handlers.QueueHandler, logging.handlers.QueueListener]:
    """
    构建优化的异步文件日志处理器（QueueHandler + QueueListener + BufferedFileHandler）。

    日志调用方只与 QueueHandler 交互，实际 I/O 由后台线程的
    QueueListener 完成，避免文件写入阻塞业务线程。

    优化特性：
    - 批量写入：通过 BufferedFileHandler 缓冲日志，减少 I/O 次数
    - 队列监控：使用 MonitoredQueueHandler 监控队列深度，防止内存溢出
    - 自动刷新：定期刷新缓冲区，避免日志延迟过久
    - 降级处理：写入失败时输出到 stderr，避免日志丢失

    支持三种轮转模式：
    - ``session``（默认）：每次启动创建新日志文件，文件名带时间戳
    - ``timed``：按时间轮转（``TimedRotatingFileHandler``），
      默认每天午夜轮转，历史文件自动追加日期后缀。
    - ``sized``：按文件大小轮转（``RotatingFileHandler``）。

    Args:
        file_path: 日志文件路径，父目录不存在时自动创建。
        level: 处理器最低日志级别。
        structured: 若为 True，使用 JSON 格式化器（无颜色）。
        rotate: 轮转模式，``"session"``/``"timed"``/``"sized"``。
        when: timed 模式的轮转周期（``"midnight"``/``"H"``/``"D"`` 等）。
        backup_count: 保留的历史日志文件数量。
        max_bytes: sized 模式单文件最大字节数。
        buffer_size: 缓冲区大小（条数），达到此值立即刷新。
        flush_interval: 自动刷新间隔（秒），避免日志延迟过久。
        max_queue_size: 队列最大容量，防止内存无限增长。

    Returns:
        ``(queue_handler, queue_listener)`` 元组。
        调用方需将 queue_handler 注册到 Logger，
        并在应用启动后调用 ``queue_listener.start()``，
        退出前调用 ``queue_listener.stop()``。
    """
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # session 模式：生成带时间戳的文件名
    if rotate == "session":
        path = _generate_session_log_path(path)

    formatter = JSONFormatter() if structured else StructuredFormatter(color=False)

    # 创建底层文件处理器
    if rotate == "sized":
        base_handler: logging.Handler = logging.handlers.RotatingFileHandler(
            filename=path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=True,  # 延迟到第一条日志写入时才创建文件，避免空日志文件堆积
        )
    elif rotate == "timed":
        base_handler = logging.handlers.TimedRotatingFileHandler(
            filename=path,
            when=when,
            backupCount=backup_count,
            encoding="utf-8",
            delay=True,  # 延迟到第一条日志写入时才创建文件，避免空日志文件堆积
        )
    else:  # session 模式使用普通 FileHandler
        base_handler = logging.FileHandler(
            filename=path,
            mode="w",  # 覆盖模式，每次启动创建新文件
            encoding="utf-8",
            delay=True,
        )

    base_handler.setLevel(level)
    base_handler.setFormatter(formatter)

    # 包装为带缓冲的处理器
    buffered_handler = BufferedFileHandler(
        base_handler=base_handler,
        buffer_size=buffer_size,
        flush_interval=flush_interval,
    )
    buffered_handler.setLevel(level)
    buffered_handler.setFormatter(formatter)

    # 创建有界队列
    log_queue: queue.Queue[Any] = queue.Queue(maxsize=max_queue_size)

    # 使用监控队列处理器
    queue_handler = MonitoredQueueHandler(
        log_queue=log_queue,
        max_queue_size=max_queue_size,
        warn_threshold=0.8,
    )
    queue_handler.setLevel(level)

    # respect_handler_level=True：让底层 handler 的级别过滤仍然生效，
    # 否则 QueueListener 会绕过底层 handler 的 level 直接输出所有记录
    listener = logging.handlers.QueueListener(
        log_queue,
        buffered_handler,
        respect_handler_level=True,
    )

    return queue_handler, listener
