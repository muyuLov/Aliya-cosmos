"""TTS 文本预处理：动作描写过滤、分句、分段合并"""

from __future__ import annotations

import re

# 句末分段：中文标点、换行符、英文句号（后跟空白字符）
# \s 包含所有空白字符（空格、\n、\t、\r 等），因此 .\n、.\t 等都能正确分段
SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?…\n])|(?<=\.\s)")

# 动作描写过滤：匹配括号（中英文）及其内容
ACTION_FILTER_RE = re.compile(r"[（(][^）)]*[）)]")

# 省略号过滤：匹配中文省略号（…）和英文省略号（...）
ELLIPSIS_FILTER_RE = re.compile(r"…+|\.{3,}")


def filter_actions(text: str) -> str:
    """
    过滤文本中的动作描写（括号及其内容）和省略号。

    支持中文括号（）和英文括号()，以及中文省略号（…）和英文省略号（...），移除后自动清理多余空白。

    Examples:
        >>> filter_actions("你好（微笑）世界")
        '你好世界'
        >>> filter_actions("Hello (waves) there")
        'Hello there'
        >>> filter_actions("测试（动作1）文本（动作2）结束")
        '测试文本结束'
        >>> filter_actions("你好...世界")
        '你好世界'
        >>> filter_actions("嗯…好的")
        '嗯好的'
    """
    # 移除所有括号及其内容
    filtered = ACTION_FILTER_RE.sub("", text)
    # 移除所有省略号
    filtered = ELLIPSIS_FILTER_RE.sub("", filtered)
    # 清理多余空白：将连续空白替换为单个空格，并去除首尾空白
    filtered = re.sub(r"\s+", " ", filtered).strip()
    return filtered


def split_text(text: str, min_segment_length: int = 5) -> list[str]:
    """
    按句末标点分段，过滤空段，合并过短段，至少返回一段。

    Args:
        text: 待分段文本。
        min_segment_length: 短句合并阈值（字符数），默认 5。
            小于此长度的段会被合并到相邻段，减少 session 创建数。

    Returns:
        分段列表，至少包含一个元素。
    """
    segments = [s.strip() for s in SENTENCE_SPLIT_RE.split(text)]
    segments = [s for s in segments if s]
    if not segments:
        return [text]
    return merge_short_segments(segments, min_segment_length)


def merge_short_segments(segments: list[str], min_length: int = 8) -> list[str]:
    """
    将过短的段合并到相邻段，减少 session 创建数。

    合并规则：
    - 段长度 < min_length 时，优先合并到前一段；
    - 第一段过短时，暂存，等第二段合并；
    - 最后一段过短时，合并到前一段；
    - 合并后重新检查（可能两段合并后仍短，继续合并）。

    Args:
        segments: 已分段的文本列表。
        min_length: 短句阈值（字符数）。

    Returns:
        合并后的分段列表。

    Examples:
        >>> merge_short_segments(["你", "好", "世界"], 8)
        ['你好世界']
        >>> merge_short_segments(["很好", "再见"], 8)
        ['很好再见']
        >>> merge_short_segments(["你好世界"], 8)
        ['你好世界']
    """
    if len(segments) <= 1:
        return segments

    result: list[str] = []
    for seg in segments:
        if len(seg) < min_length:
            if result:
                # 合并到前一段
                result[-1] = result[-1] + seg
            else:
                # 第一段过短，暂存
                result.append(seg)
        else:
            # 当前段足够长
            if result and len(result[-1]) < min_length:
                # 前一段过短，合并到当前段
                result[-1] = result[-1] + seg
            else:
                result.append(seg)

    # 最后一段仍过短，合并到前一段
    if len(result) > 1 and len(result[-1]) < min_length:
        result[-2] = result[-2] + result[-1]
        result.pop()

    return result if result else [segments[0]]


__all__ = [
    "SENTENCE_SPLIT_RE",
    "ACTION_FILTER_RE",
    "ELLIPSIS_FILTER_RE",
    "filter_actions",
    "split_text",
    "merge_short_segments",
]
