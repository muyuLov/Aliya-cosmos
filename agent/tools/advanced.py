from __future__ import annotations

import io
import re
import sys
from typing import TYPE_CHECKING, Any

import httpx

from agent.cache import format_memory_list
from agent.models import ToolResult
from agent.tools.base import BaseTool, InternalTool
from core.logger import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from memory.memory_manager import GRAGMemoryManager


class WebSearchTool(BaseTool):
    """网页搜索工具（调用外部搜索 API）"""

    name = "web_search"
    description = "搜索网页获取实时信息"
    input_schema = {
        "query": {"type": "string", "description": "搜索关键词"},
        "limit": {"type": "integer", "description": "返回结果数量，默认 5"},
    }

    def __init__(self, search_api_url: str = ""):
        self.search_api_url = search_api_url

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = arguments.get("query", "")
        if not query:
            return {"success": False, "error": "查询为空"}
        if not self.search_api_url:
            return {"success": False, "error": "搜索 API 未配置"}

        limit = arguments.get("limit", 5)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(self.search_api_url, params={"q": query, "limit": limit})
                response.raise_for_status()
                results = response.json()
                return {"success": True, "results": results}
        except httpx.HTTPError as e:
            return {"success": False, "error": f"搜索失败：{e}"}
        except Exception as e:
            return {"success": False, "error": str(e)}


class CodeExecutionTool(BaseTool):
    """Python 代码执行工具（受限沙箱，仅允许纯计算操作）"""

    name = "code_exec"
    description = "执行 Python 代码并返回结果"
    input_schema = {
        "code": {"type": "string", "description": "要执行的 Python 代码"},
    }

    _SAFE_BUILTINS: dict[str, Any] = {
        "abs": abs, "all": all, "any": any, "bool": bool,
        "chr": chr, "dict": dict, "dir": dir, "enumerate": enumerate,
        "filter": filter, "float": float, "format": format, "frozenset": frozenset,
        "hasattr": hasattr, "hash": hash, "hex": hex, "id": id,
        "int": int, "isinstance": isinstance, "issubclass": issubclass,
        "iter": iter, "len": len, "list": list, "map": map,
        "max": max, "min": min, "next": next, "object": object,
        "oct": oct, "ord": ord, "pow": pow, "print": print,
        "range": range, "repr": repr, "reversed": reversed,
        "round": round, "set": set, "slice": slice, "sorted": sorted,
        "str": str, "sum": sum, "tuple": tuple, "type": type,
        "vars": vars, "zip": zip,
        "True": True, "False": False, "None": None,
    }

    def _validate_code(self, code: str) -> str | None:
        """检查代码中是否包含危险操作的关键字。"""
        danger = re.search(r'__\w+__', code)
        if danger:
            return f"代码包含禁用的 dunder 操作：{danger.group()}"
        dyn = re.search(r'chr\s*\(\s*9[0-5]\s*\)', code)
        if dyn:
            return f"代码包含动态构造 dunder 的尝试：{dyn.group()}"
        return None

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        code = arguments.get("code", "")
        if not code:
            return {"success": False, "error": "代码为空"}

        err = self._validate_code(code)
        if err:
            return {"success": False, "error": err}

        try:
            local_ns: dict[str, Any] = {}
            safe_globals = {"__builtins__": self._SAFE_BUILTINS}

            # 捕获 print 输出
            stdout_buf = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = stdout_buf
            try:
                exec(code, safe_globals, local_ns)
            finally:
                sys.stdout = old_stdout

            stdout = stdout_buf.getvalue()
            result = local_ns.get("result", "")
            return {
                "success": True,
                "stdout": stdout,
                "result": str(result) if result is not None else "",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}


class MemoryQueryTool(InternalTool):
    """记忆查询工具（结果注入对话，让 LLM 继续推理）"""

    name = "memory_query"
    description = "从 GRAG 记忆系统检索相关信息"
    message_prefix = "【记忆查询结果】"
    input_schema = {
        "query": {"type": "string", "description": "查询内容"},
        "limit": {"type": "integer", "description": "返回结果数量（1-20）", "default": 5},
    }

    def __init__(self, memory_manager: GRAGMemoryManager):
        self.memory_manager = memory_manager

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        query = arguments.get("query", "")
        if not query:
            return ToolResult(
                tool_name=self.name, success=False,
                error="查询为空", error_code="EMPTY_QUERY",
            )

        try:
            limit = max(1, min(20, int(arguments.get("limit", 5))))
        except (ValueError, TypeError):
            limit = 5

        try:
            memories = await self.memory_manager.get_relevant_memories(query, limit=limit)
            return ToolResult(
                tool_name=self.name, success=True,
                data={"memories": memories, "count": len(memories)},
            )
        except Exception as e:
            logger.exception("MemoryQueryTool 执行失败")
            return ToolResult(
                tool_name=self.name, success=False,
                error="记忆系统暂时不可用", error_code="MEMORY_QUERY_FAILED",
            )

    async def execute_and_format(self, arguments: dict[str, Any]) -> str:
        """执行查询并格式化为可注入对话的文本。"""
        result = await self.run(arguments)
        if not result.success:
            return f"{self.message_prefix}查询失败: {result.error}"
        if not result.data or not result.data.get("memories"):
            return f"{self.message_prefix}未找到相关记忆"
        return format_memory_list(
            result.data["memories"],
            empty_text=f"{self.message_prefix}未找到相关记忆",
            prefix=self.message_prefix,
        )
