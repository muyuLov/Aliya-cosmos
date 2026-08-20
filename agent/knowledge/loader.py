"""文档加载：将 Markdown 文件切分为检索片段。"""

from __future__ import annotations

import re
from pathlib import Path

_CHUNK_SIZE = 500  # 每片段目标字符数
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？])")  # 中文句界后切（保留标点）


def _join(buf: str, part: str) -> str:
    """把片段 part 追加到 buf；buf 为空时避免前导换行。"""
    return f"{buf}\n{part}" if buf else part


def split_markdown(text: str, chunk_size: int = _CHUNK_SIZE) -> list[str]:
    """按段落切分，长段落再按句界硬切，避免超出 chunk_size。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 1 <= chunk_size:
            buf = _join(buf, p)
        else:
            if buf:
                chunks.append(buf)
                buf = ""
            # 长段落按句切
            if len(p) > chunk_size:
                for s in _SENTENCE_SPLIT.split(p):
                    s = s.strip()
                    if not s:
                        continue
                    if len(buf) + len(s) + 1 <= chunk_size:
                        buf = _join(buf, s)
                    else:
                        if buf:
                            chunks.append(buf)
                        buf = s
            else:
                buf = p
    if buf:
        chunks.append(buf)
    return chunks


def load_directory(directory: str | Path) -> list[tuple[str, str, list[str]]]:
    """读取目录下全部 .md，返回 [(doc_id, title, chunks)]。

    doc_id = 文件名（去扩展名）；title = 首个一级标题或文件名。
    """
    directory = Path(directory)
    docs: list[tuple[str, str, list[str]]] = []
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        title = title.group(1).strip() if title else path.stem
        docs.append((path.stem, title, split_markdown(text)))
    return docs
