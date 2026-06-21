"""记忆系统内部 JSON 解析工具函数"""

from __future__ import annotations

import json
from typing import Any, List, Optional

from core.logger import get_logger

logger = get_logger(__name__)


def parse_json_array(content: str, description: str = "数据") -> Optional[List[Any]]:
    """
    从 LLM 响应中解析 JSON 数组。

    尝试顺序：
    1. 直接解析完整内容为 JSON
    2. 提取内容中的 [...] 块进行解析

    Args:
        content: LLM 返回的原始文本
        description: 用于日志描述的名称

    Returns:
        JSON 列表，解析失败时返回 None
    """
    content = content.strip()

    try:
        data = json.loads(content)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    if "[" in content and "]" in content:
        start = content.index("[")
        end = content.rindex("]") + 1
        try:
            data = json.loads(content[start:end])
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    logger.warning("无法解析 %s: %.200s", description, content)
    return None


__all__ = [
    "parse_json_array",
]
