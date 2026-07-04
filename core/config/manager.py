"""配置管理器：从 YAML 加载配置，提供点路径读写与热重载，支持 ${ENV_VAR} 环境变量解析"""

from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import yaml

from core.config.env_resolver import resolve_env_vars as _resolve_env_vars


_config_instances: dict[str, "ConfigManager"] = {}


def get_config_instance(config_path: str | Path | None = None) -> "ConfigManager":
    """
    获取配置管理器单例。

    Args:
        config_path: 配置文件路径，首次调用时传入，后续调用可省略。

    Returns:
        ConfigManager 单例实例。
    """
    if config_path is not None:
        path_str = str(config_path)
        if path_str not in _config_instances:
            _config_instances[path_str] = ConfigManager(config_path)
        return _config_instances[path_str]
    if not _config_instances:
        raise RuntimeError("尚未创建任何配置实例，请先提供 config_path 调用 get_config_instance()")
    return next(iter(_config_instances.values()))


class ConfigManager:
    """
    从 YAML 文件加载配置，支持点路径读写与热重载。

    Example:
        config = get_config_instance("data/config/main.yml")
        config.get("cosmos.logger.level")
        config.set("cosmos.logger.level", "debug")
        "cosmos.logger.level" in config
    """

    def __init__(self, config_path: str | Path | None = None, *, resolve_env: bool = True) -> None:
        self._config_path: Path | None = None
        self._data: dict[str, Any] = {}
        self._callbacks: dict[str, list[Callable[[str, Any], None]]] = {}
        self._global_callbacks: list[Callable[[str, Any], None]] = []
        self._resolve_env = resolve_env
        if config_path:
            self.load_config(config_path)

    def register_callback(
        self, path_pattern: str | None, callback: Callable[[str, Any], None]
    ) -> None:
        """
        注册配置变更回调。

        Args:
            path_pattern: 配置路径前缀，如 "cosmos.service.llm"。为 None 时监听所有变更。
            callback: 回调函数，签名 (path: str, value: Any) -> None。
        """
        if path_pattern is None:
            self._global_callbacks.append(callback)
        else:
            if path_pattern not in self._callbacks:
                self._callbacks[path_pattern] = []
            self._callbacks[path_pattern].append(callback)

    def _notify_callbacks(self, path: str, value: Any) -> None:
        """通知所有匹配的回调"""
        for callback in self._global_callbacks:
            callback(path, value)
        for pattern, callbacks in self._callbacks.items():
            if path.startswith(pattern):
                for callback in callbacks:
                    callback(path, value)

    def load_config(self, file_path: str | Path) -> None:
        """
        加载 YAML 文件，替换当前配置数据。

        Raises:
            FileNotFoundError: 文件不存在。
            ValueError: 文件顶层不是字典。
        """
        path = Path(file_path)
        try:
            with path.open("r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"配置文件不存在: {file_path}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"配置文件格式错误，期望字典，得到: {type(raw)}")
        self._config_path = path
        self._data = raw

    def get(self, path: str, default: Any = None) -> Any:
        """
        按点路径读取值，路径不存在时返回 default。

        默认会解析值中 ``${ENV_VAR}`` 和 ``${ENV_VAR:default}`` 占位符。
        在 :meth:`__init__` 中传入 ``resolve_env=False`` 或使用 :meth:`get_raw`
        可关闭此行为。
        """
        node: Any = self._data
        for key in _split_path(path):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        if self._resolve_env:
            return _resolve_env_vars(node)
        return node

    def get_raw(self, path: str, default: Any = None) -> Any:
        """按点路径读取原始值（不解析环境变量占位符）。"""
        node: Any = self._data
        for key in _split_path(path):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def set(self, path: str, value: Any) -> None:
        """按点路径写入值，缺失的中间层自动创建为空字典，并触发回调。"""
        keys = _split_path(path)
        node = self._data
        for key in keys[:-1]:
            if not isinstance(node.get(key), dict):
                node[key] = {}
            node = node[key]
        node[keys[-1]] = value
        self._notify_callbacks(path, value)

    def reload(self) -> None:
        """重新加载当前文件，覆盖所有运行时修改，并触发重载事件。"""
        if not self._config_path:
            raise RuntimeError("尚未加载任何配置文件")
        self.load_config(self._config_path)
        self._notify_callbacks("__reload__", None)

    def get_all_fields(self) -> dict[str, Any]:
        """返回所有叶子字段的扁平化点路径键值对。"""
        return _flatten(self._data)

    def __contains__(self, path: str) -> bool:
        """支持 `"a.b.c" in config` 语法，检查路径是否存在。"""
        sentinel = object()
        return self.get(path, sentinel) is not sentinel


@lru_cache(maxsize=512)
def _split_path(path: str) -> tuple[str, ...]:
    """缓存分割结果，避免高频调用时重复 split。"""
    return tuple(path.split("."))


def _flatten(data: dict[str, Any]) -> dict[str, Any]:
    """
    迭代扁平化嵌套字典，用显式栈替代递归避免栈溢出。

    Returns:
        以点路径为键的扁平字典。
    """
    result: dict[str, Any] = {}
    stack: list[tuple[dict[str, Any], str]] = [(data, "")]
    while stack:
        node, prefix = stack.pop()
        for key, val in node.items():
            full_key = f"{prefix}.{key}" if prefix else key
            if isinstance(val, dict):
                stack.append((val, full_key))
            else:
                result[full_key] = val
    return result
