"""测试 Agent 核心逻辑：LLM 响应解析、BrainResult 构造"""

from __future__ import annotations

import pytest

from agent.agent import BrainResult, parse_llm_response


class TestParseLlmResponse:
    def test_direct_json(self):
        """直接 JSON 解析"""
        result = parse_llm_response('{"reply": "你好！", "tool_calls": []}')
        assert result.reply == "你好！"
        assert result.tool_calls == []

    def test_direct_json_no_tool_calls(self):
        """无可选字段的 JSON"""
        result = parse_llm_response('{"reply": "hello"}')
        assert result.reply == "hello"
        assert result.tool_calls == []

    def test_direct_json_with_tool_calls(self):
        raw = '{"reply": "查询中", "tool_calls": [{"name": "memory_query", "params": {"query": "test"}}]}'
        result = parse_llm_response(raw)
        assert result.reply == "查询中"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["name"] == "memory_query"

    def test_markdown_code_block(self):
        """Markdown 代码块包裹的 JSON"""
        raw = """```json\n{"reply": "在代码块里"}\n```"""
        result = parse_llm_response(raw)
        assert result.reply == "在代码块里"

    def test_markdown_code_block_no_lang(self):
        """无语言标记的代码块"""
        raw = """```\n{"reply": "纯代码块"}\n```"""
        result = parse_llm_response(raw)
        assert result.reply == "纯代码块"

    def test_fallback_regex(self):
        """多层 fallback：正则提取 reply 和 tool_calls"""
        raw = '一些文字 "reply": "兜底回复" 更多文字 "tool_calls": [{"name": "reply", "params": {"text": "t"}}] 结束'
        result = parse_llm_response(raw)
        assert result.reply == "兜底回复"
        assert len(result.tool_calls) == 1

    def test_fallback_no_reply_match(self):
        """完全不可解析时返回原始文本"""
        result = parse_llm_response("无法解析的文本")
        assert result.reply == "无法解析的文本"
        assert result.tool_calls == []

    def test_empty_string(self):
        result = parse_llm_response("")
        assert result.reply == ""

    def test_reply_with_escaped_quotes(self):
        """reply 中包含转义引号"""
        result = parse_llm_response('{"reply": "他说\\"你好\\""}')
        assert "你好" in result.reply

    def test_empty_tool_calls(self):
        """tool_calls 为空列表"""
        result = parse_llm_response('{"reply": "ok", "tool_calls": []}')
        assert result.reply == "ok"
        assert result.tool_calls == []

    def test_none_reply_falls_back(self):
        """reply 为 null 时使用默认值"""
        result = parse_llm_response('{"reply": null}')
        assert result.reply == ""

    def test_finish_reason_default(self):
        result = parse_llm_response('{"reply": "hi"}')
        assert result.finish_reason == "stop"

    def test_tool_calls_invalid_json_in_fallback(self):
        """fallback 中 tool_calls 不是合法 JSON 时返回空"""
        result = parse_llm_response('"reply": "hi" "tool_calls": 不是json')
        assert result.tool_calls == []

    def test_code_block_tool_calls(self):
        """代码块中带 tool_calls"""
        raw = """```\n{"reply": "查询", "tool_calls": [{"name": "memory_query", "params": {}}]}\n```"""
        result = parse_llm_response(raw)
        assert result.reply == "查询"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["name"] == "memory_query"


class TestBrainResult:
    def test_default_tool_calls_empty(self):
        br = BrainResult(reply="test")
        assert br.tool_calls == []

    def test_default_finish_reason(self):
        br = BrainResult(reply="test")
        assert br.finish_reason == "stop"
