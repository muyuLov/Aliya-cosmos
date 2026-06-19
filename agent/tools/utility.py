"""实用工具集：文件、日期时间"""

from __future__ import annotations

import hashlib
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.tools.base import BaseTool


class FileContentCache:
    """文件内容缓存，基于路径 + 修改时间（线程安全）"""

    def __init__(self, max_size: int = 50, ttl: float = 60.0):
        self.max_size = max_size
        self.ttl = ttl
        self._cache: dict[str, tuple[str, float, float]] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    def _make_key(self, path: str) -> str:
        return hashlib.sha256(path.encode()).hexdigest()

    def _get_mtime(self, path: Path) -> float:
        try:
            return path.stat().st_mtime
        except FileNotFoundError:
            return 0.0

    def get(self, path: Path) -> str | None:
        key = self._make_key(str(path))
        with self._lock:
            if key not in self._cache:
                return None

            content, mtime, cached_at = self._cache[key]

        current_mtime = self._get_mtime(path)
        now = time.time()

        if now - cached_at > self.ttl:
            with self._lock:
                # 二次检查防止并发删除
                if key in self._cache and self._cache[key][2] == cached_at:
                    del self._cache[key]
                    self._order.remove(key)
            return None

        if mtime != current_mtime:
            with self._lock:
                if key in self._cache and self._cache[key][1] == mtime:
                    del self._cache[key]
                    self._order.remove(key)
            return None

        with self._lock:
            if key not in self._cache:
                return None
            self._order.remove(key)
            self._order.append(key)
        return content

    def set(self, path: Path, content: str) -> None:
        key = self._make_key(str(path))
        mtime = self._get_mtime(path)

        with self._lock:
            if key in self._cache:
                self._order.remove(key)
            elif len(self._cache) >= self.max_size:
                oldest_key = self._order.pop(0)
                del self._cache[oldest_key]

            self._cache[key] = (content, mtime, time.time())
            self._order.append(key)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._order.clear()

    def invalidate(self, path: Path) -> None:
        key = self._make_key(str(path))
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                self._order.remove(key)


class FileTool(BaseTool):
    """文件操作工具（受限访问）"""

    name = "file"
    description = "读取或写入工作区文件"
    input_schema = {
        "action": {"type": "string", "description": "操作类型: read / write / exists / delete"},
        "path": {"type": "string", "description": "文件路径"},
        "content": {"type": "string", "description": "写入内容（仅 write 操作需要）"},
    }

    _MAX_READ_BYTES = 5 * 1024 * 1024  # 5 MB 上限，防止大文件撑爆内存

    def __init__(
        self,
        allowed_paths: list[str] | None = None,
        enable_cache: bool = True,
        cache_ttl: float = 60.0,
    ) -> None:
        self.allowed_paths = list(allowed_paths) if allowed_paths else ["."]
        self._cache = FileContentCache(ttl=cache_ttl) if enable_cache else None

    def _is_symlink_in_path(self, path: Path) -> bool:
        for part in path.parents:
            if part.is_symlink():
                return True
        return path.is_symlink()

    def _check_symlink_safety(self, path: Path) -> tuple[bool, str | None]:
        if not self._is_symlink_in_path(path):
            return True, None

        resolved = path.resolve()
        for allowed in self.allowed_paths:
            allowed_resolved = Path(allowed).resolve()
            try:
                resolved.relative_to(allowed_resolved)
                return True, None
            except ValueError:
                continue

        return False, f"符号链接指向不允许的路径：{resolved}"

    def _is_path_allowed(self, path: str, action: str) -> tuple[bool, str | None]:
        try:
            path_obj = Path(path)

            if path_obj.exists():
                is_safe, error = self._check_symlink_safety(path_obj)
                if not is_safe:
                    return False, error
            elif action == "read":
                return False, f"文件不存在：{path}"

            resolved = path_obj.resolve()

            for allowed in self.allowed_paths:
                allowed_resolved = Path(allowed).resolve()
                try:
                    resolved.relative_to(allowed_resolved)
                    return True, None
                except ValueError:
                    continue

            return False, f"路径不允许：{path}，仅限 {self.allowed_paths}"

        except (OSError, ValueError) as e:
            return False, f"路径验证失败：{e}"

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = arguments.get("action", "read")
        path = arguments.get("path", "")

        if not path:
            return {"success": False, "error": "路径为空"}

        is_allowed, error = self._is_path_allowed(path, action)
        if not is_allowed:
            return {"success": False, "error": error}

        try:
            file_path = Path(path)

            if action == "read":
                if not file_path.exists():
                    return {"success": False, "error": f"文件不存在：{path}"}
                if not file_path.is_file():
                    return {"success": False, "error": f"不是文件：{path}"}

                # 文件大小检查（防止大文件撑爆内存）
                if file_path.stat().st_size > self._MAX_READ_BYTES:
                    return {"success": False, "error": f"文件过大（{file_path.stat().st_size} bytes，上限 {self._MAX_READ_BYTES}）"}

                if self._cache is not None:
                    cached = self._cache.get(file_path)
                    if cached is not None:
                        return {
                            "success": True,
                            "content": cached,
                            "size": len(cached),
                            "from_cache": True,
                        }

                content = file_path.read_text(encoding="utf-8")

                if self._cache is not None:
                    self._cache.set(file_path, content)

                return {
                    "success": True,
                    "content": content,
                    "size": len(content),
                    "from_cache": False,
                }

            elif action == "write":
                content = arguments.get("content", "")
                file_path.write_text(content, encoding="utf-8")

                if self._cache is not None:
                    self._cache.invalidate(file_path)

                return {"success": True, "written": True, "path": path, "size": len(content)}

            elif action == "exists":
                return {"success": True, "exists": file_path.exists(), "path": path}

            elif action == "delete":
                if file_path.exists():
                    file_path.unlink()

                    if self._cache is not None:
                        self._cache.invalidate(file_path)

                    return {"success": True, "deleted": True, "path": path}
                return {"success": False, "error": f"文件不存在：{path}"}

            else:
                return {"success": False, "error": f"未知操作：{action}"}

        except PermissionError as e:
            return {"success": False, "error": f"权限拒绝：{e}"}
        except Exception as e:
            return {"success": False, "error": f"文件操作失败：{e}"}


class DateTimeTool(BaseTool):
    """日期时间工具"""

    name = "datetime"
    description = "获取当前时间或格式化时间戳"
    input_schema = {
        "action": {"type": "string", "description": "操作类型: now / format / parse"},
        "timestamp": {"type": "integer", "description": "Unix 时间戳（仅 format 需要）"},
        "format": {"type": "string", "description": "时间格式字符串，如 %Y-%m-%d"},
    }

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = arguments.get("action", "now")

        try:
            if action == "now":
                now = datetime.now()
                return {
                    "success": True,
                    "iso": now.isoformat(),
                    "timestamp": int(now.timestamp()),
                    "formatted": now.strftime("%Y-%m-%d %H:%M:%S"),
                }

            elif action == "format":
                timestamp = arguments.get("timestamp")
                fmt = arguments.get("format", "%Y-%m-%d %H:%M:%S")
                if timestamp is None:
                    return {"success": False, "error": "需要 timestamp 参数"}
                dt = datetime.fromtimestamp(float(timestamp))
                return {"success": True, "formatted": dt.strftime(fmt), "iso": dt.isoformat()}

            elif action == "parse":
                date_str = arguments.get("date", "")
                fmt = arguments.get("format", "%Y-%m-%d %H:%M:%S")
                if not date_str:
                    return {"success": False, "error": "需要 date 参数"}
                dt = datetime.strptime(date_str, fmt)
                return {"success": True, "timestamp": int(dt.timestamp()), "iso": dt.isoformat()}

            else:
                return {"success": False, "error": f"未知操作：{action}"}

        except ValueError as e:
            return {"success": False, "error": f"格式错误：{e}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
