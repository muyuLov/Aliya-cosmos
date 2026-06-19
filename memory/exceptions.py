# -*- coding: utf-8 -*-
"""
GRAG Memory System Exceptions

GRAG 记忆系统异常定义，使用项目的 StructuredException 基类。

错误码分配：MEM_xxx (Memory System)
- MEM_001~099: 基础错误
- MEM_100~199: 图谱操作错误
- MEM_200~299: 提取器错误
- MEM_300~399: RAG 查询错误
- MEM_400~499: 任务管理错误
"""

from core.exception.base import StructuredException


# ============ 基础异常 (MEM_001~099) ============


class GRAGError(StructuredException):
    """GRAG 记忆系统基础异常"""

    def __init__(
        self,
        code: str = "MEM_000",
        message: str = "",
        details: dict | None = None,
        cause: Exception | None = None,
    ):
        super().__init__(
            code=code,
            message=message,
            details=details,
            cause=cause,
        )


class GRAGConfigError(GRAGError):
    """GRAG 配置错误"""

    def __init__(
        self,
        message: str,
        details: dict | None = None,
        cause: Exception | None = None,
    ):
        super().__init__(
            code="MEM_001",
            message=f"配置错误: {message}",
            details=details,
            cause=cause,
        )


class GRAGNotEnabledError(GRAGError):
    """GRAG 功能未启用"""

    def __init__(
        self,
        message: str = "GRAG 记忆系统未启用",
        details: dict | None = None,
        cause: Exception | None = None,
    ):
        super().__init__(
            code="MEM_002",
            message=message,
            details=details,
            cause=cause,
        )


# ============ 图谱操作异常 (MEM_100~199) ============


class GraphOperationError(GRAGError):
    """图谱操作基础异常"""

    def __init__(
        self,
        message: str,
        details: dict | None = None,
        cause: Exception | None = None,
        code: str = "MEM_100",
    ):
        final_message = f"图谱操作失败: {message}" if code == "MEM_100" else message
        super().__init__(
            code=code,
            message=final_message,
            details=details,
            cause=cause,
        )


class GraphConnectionError(GraphOperationError):
    """图谱连接错误"""

    def __init__(
        self,
        message: str = "无法连接到图数据库",
        details: dict | None = None,
        cause: Exception | None = None,
    ):
        super().__init__(
            code="MEM_101",
            message=message,
            details=details,
            cause=cause,
        )


class GraphQueryError(GraphOperationError):
    """图谱查询错误"""

    def __init__(
        self,
        message: str,
        query: str | None = None,
        details: dict | None = None,
        cause: Exception | None = None,
    ):
        details = details or {}
        if query:
            details["query"] = query
        super().__init__(
            code="MEM_102",
            message=f"图谱查询失败: {message}",
            details=details,
            cause=cause,
        )


class GraphWriteError(GraphOperationError):
    """图谱写入错误"""

    def __init__(
        self,
        message: str,
        quintuple_count: int | None = None,
        details: dict | None = None,
        cause: Exception | None = None,
    ):
        details = details or {}
        if quintuple_count is not None:
            details["quintuple_count"] = quintuple_count
        super().__init__(
            code="MEM_103",
            message=f"图谱写入失败: {message}",
            details=details,
            cause=cause,
        )


class FileStorageError(GraphOperationError):
    """文件存储错误（已弃用 - 不再使用本地文件存储）"""

    def __init__(
        self,
        message: str,
        file_path: str | None = None,
        details: dict | None = None,
        cause: Exception | None = None,
    ):
        details = details or {}
        if file_path:
            details["file_path"] = file_path
        super().__init__(
            code="MEM_104",
            message=f"文件存储失败: {message}",
            details=details,
            cause=cause,
        )


# ============ 提取器异常 (MEM_200~299) ============


class ExtractionError(GRAGError):
    """五元组提取基础异常"""

    def __init__(
        self,
        message: str,
        details: dict | None = None,
        cause: Exception | None = None,
        code: str = "MEM_200",
    ):
        final_message = f"五元组提取失败: {message}" if code == "MEM_200" else message
        super().__init__(
            code=code,
            message=final_message,
            details=details,
            cause=cause,
        )


class ExtractionTimeoutError(ExtractionError):
    """提取超时"""

    def __init__(
        self,
        timeout: float,
        details: dict | None = None,
        cause: Exception | None = None,
    ):
        details = details or {}
        details["timeout_seconds"] = timeout
        super().__init__(
            code="MEM_201",
            message=f"提取超时 ({timeout}秒)",
            details=details,
            cause=cause,
        )


class ExtractionParseError(ExtractionError):
    """解析 LLM 响应失败"""

    def __init__(
        self,
        raw_response: str,
        details: dict | None = None,
        cause: Exception | None = None,
    ):
        details = details or {}
        details["raw_response"] = raw_response[:500] if raw_response else ""
        super().__init__(
            code="MEM_202",
            message="无法解析 LLM 响应",
            details=details,
            cause=cause,
        )


class LLMProviderError(ExtractionError):
    """LLM 提供者错误"""

    def __init__(
        self,
        message: str,
        provider: str | None = None,
        details: dict | None = None,
        cause: Exception | None = None,
    ):
        details = details or {}
        if provider:
            details["provider"] = provider
        super().__init__(
            code="MEM_203",
            message=f"LLM 提供者错误: {message}",
            details=details,
            cause=cause,
        )


# ============ RAG 查询异常 (MEM_300~399) ============


class RAGQueryError(GRAGError):
    """RAG 查询基础异常"""

    def __init__(
        self,
        message: str,
        details: dict | None = None,
        cause: Exception | None = None,
        code: str = "MEM_300",
    ):
        final_message = f"RAG 查询失败: {message}" if code == "MEM_300" else message
        super().__init__(
            code=code,
            message=final_message,
            details=details,
            cause=cause,
        )


class RAGContextError(RAGQueryError):
    """RAG 上下文错误"""

    def __init__(
        self,
        message: str,
        context_length: int | None = None,
        details: dict | None = None,
        cause: Exception | None = None,
    ):
        details = details or {}
        if context_length is not None:
            details["context_length"] = context_length
        super().__init__(
            code="MEM_301",
            message=f"RAG 上下文错误: {message}",
            details=details,
            cause=cause,
        )


class RAGGenerationError(RAGQueryError):
    """RAG 生成错误"""

    def __init__(
        self,
        message: str,
        details: dict | None = None,
        cause: Exception | None = None,
    ):
        super().__init__(
            code="MEM_302",
            message=f"RAG 生成失败: {message}",
            details=details,
            cause=cause,
        )


# ============ 任务管理异常 (MEM_400~499) ============


class TaskManagerError(GRAGError):
    """任务管理器基础异常"""

    def __init__(
        self,
        message: str,
        details: dict | None = None,
        cause: Exception | None = None,
        code: str = "MEM_400",
    ):
        final_message = f"任务管理器错误: {message}" if code == "MEM_400" else message
        super().__init__(
            code=code,
            message=final_message,
            details=details,
            cause=cause,
        )


class TaskQueueFullError(TaskManagerError):
    """任务队列已满"""

    def __init__(
        self,
        queue_size: int,
        max_size: int,
        details: dict | None = None,
        cause: Exception | None = None,
    ):
        details = details or {}
        details["queue_size"] = queue_size
        details["max_size"] = max_size
        super().__init__(
            code="MEM_401",
            message=f"任务队列已满 ({queue_size}/{max_size})",
            details=details,
            cause=cause,
        )


class TaskTimeoutError(TaskManagerError):
    """任务执行超时"""

    def __init__(
        self,
        task_id: str,
        timeout: float,
        details: dict | None = None,
        cause: Exception | None = None,
    ):
        details = details or {}
        details["task_id"] = task_id
        details["timeout_seconds"] = timeout
        super().__init__(
            code="MEM_402",
            message=f"任务执行超时 ({timeout}秒)",
            details=details,
            cause=cause,
        )


class TaskExecutionError(TaskManagerError):
    """任务执行失败"""

    def __init__(
        self,
        task_id: str,
        message: str,
        details: dict | None = None,
        cause: Exception | None = None,
    ):
        details = details or {}
        details["task_id"] = task_id
        super().__init__(
            code="MEM_403",
            message=f"任务执行失败: {message}",
            details=details,
            cause=cause,
        )
