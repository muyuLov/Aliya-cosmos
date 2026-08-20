"""测试 agent.knowledge.loader：Markdown 切分与目录加载。"""

from __future__ import annotations

from pathlib import Path

from agent.knowledge.loader import load_directory, split_markdown

FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"


def test_split_short_text_single_chunk():
    chunks = split_markdown("第一段。\n\n第二段。", chunk_size=500)
    assert len(chunks) == 1
    assert "第一段" in chunks[0] and "第二段" in chunks[0]


def test_split_multi_chunk_by_paragraphs():
    text = "\n\n".join(f"第{i}段内容，包含若干文字。" for i in range(20))
    chunks = split_markdown(text, chunk_size=50)
    assert len(chunks) > 1
    # 每段 ≤ chunk_size + 句界容差（最坏情况为单句长度）
    assert all(len(c) <= 50 + 20 for c in chunks)


def test_split_long_paragraph_by_sentences():
    """长段落按句切，且已切出的片段不重复拼接（buf 重置）。"""
    long_para = "句子一。" * 100  # 400 字符长段落
    text = f"开头段落。\n\n{long_para}"
    chunks = split_markdown(text, chunk_size=50)
    assert len(chunks) > 1
    # 每个片段长度不超过 chunk_size + 单句长度
    assert all(len(c) <= 50 + 4 for c in chunks)
    # 全部句子无丢失
    joined = "".join(chunks)
    assert joined.count("句子一。") == 100


def test_split_blank_input():
    assert split_markdown("") == []
    assert split_markdown("   \n\n  ") == []


def test_load_directory_returns_docs():
    docs = load_directory(FIXTURES)
    assert len(docs) == 2
    doc_ids = [d[0] for d in docs]
    assert "aliya-guide" in doc_ids
    assert "aliya-backstory" in doc_ids
    guide = dict((d[0], d) for d in docs)["aliya-guide"]
    assert guide[1] == "Aliya 使用指南"
    assert guide[2]  # chunks 非空


def test_load_directory_missing_dir_returns_empty(tmp_path):
    assert load_directory(tmp_path / "no-such-dir") == []
