"""测试 Agent 核心逻辑：LLM 响应解析、状态机、AgentConfig"""

from __future__ import annotations

import pytest

from unittest.mock import AsyncMock, MagicMock

from agent.agent import AgentState, AliyaAgent
from agent.config import AgentConfig
from agent.brain import Brain, BrainResult, parse_llm_response, clean_soul_reply
from core.llm.cache import ContextCache
from core.llm.models import ChatResponse, TokenUsage
from core.llm.service import ConversationService


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

    def test_thought_with_newlines(self):
        """多行 thought 内容（需要 re.DOTALL）"""
        raw = '''{"reply": "好的", "thought": "步骤1：分析\n步骤2：拆解\n步骤3：输出"}'''
        result = parse_llm_response(raw)
        assert "步骤2" in result.thought
        assert result.reply == "好的"


class TestBrainResult:
    def test_default_tool_calls_empty(self):
        br = BrainResult(reply="test")
        assert br.tool_calls == []

    def test_default_finish_reason(self):
        br = BrainResult(reply="test")
        assert br.finish_reason == "stop"

    def test_turn_default(self):
        br = BrainResult(reply="test")
        assert br.turn == 0

    def test_turn_explicit(self):
        br = BrainResult(reply="test", turn=3)
        assert br.turn == 3


class TestAgentConfig:
    def test_default_config(self):
        cfg = AgentConfig()
        assert cfg.max_turns == 10
        assert cfg.progress_interval == 2.0
        assert cfg.max_refine_accum == 10
        assert cfg.tool_format_version == "cot"

    def test_custom_config(self):
        cfg = AgentConfig(max_turns=20, progress_interval=5.0)
        assert cfg.max_turns == 20
        assert cfg.progress_interval == 5.0


class TestSoulCleaning:
    """灵魂阶段回复净化。"""

    def test_pure_text_passthrough(self):
        """纯自然语言直接返回，不触发 JSON fallback。"""
        assert clean_soul_reply("我也想你呀。") == "我也想你呀。"

    def test_json_prefix_stripped(self):
        """JSON 前缀 + 正文 → 保留正文。"""
        raw = '{"tool_calls": []}\n\n我也想你呀。'
        assert clean_soul_reply(raw) == "我也想你呀。"

    def test_json_with_reply(self):
        """JSON 中 reply 非空 → 提取 reply。"""
        raw = '{"reply": "我也想你呀", "tool_calls": []}'
        assert clean_soul_reply(raw) == "我也想你呀"

    def test_pure_json_no_reply(self):
        """纯 JSON 无 reply 无正文 → 返回空（触发重试/兜底）。"""
        assert clean_soul_reply('{"thought": "分析", "tool_calls": []}') == ""

    def test_empty_input(self):
        assert clean_soul_reply("") == ""
        assert clean_soul_reply("   ") == ""


class TestToolPhaseSanitize:
    """工具阶段 JSON 决策消息的历史净化。"""

    def _make_service(self) -> ConversationService:
        provider = MagicMock()
        provider.model = "test-model"
        provider.provider_name = "test"
        provider.async_chat_completion = AsyncMock(
            return_value=ChatResponse(content="", usage=TokenUsage())
        )
        provider.stream_chat_completion = AsyncMock()
        provider.aclose = AsyncMock()
        return ConversationService(
            provider=provider,
            cache=ContextCache(ttl=3600),
            conversation_id="sanitize-test",
        )

    @pytest.mark.asyncio
    async def test_json_decision_cleaned(self):
        """纯 JSON 决策消息被替换为纯文本标记。"""
        service = self._make_service()
        brain = Brain(service, AgentConfig())
        # 模拟工具阶段写入的 JSON assistant 消息
        await service.append_message("assistant", '{"tool_calls": [], "reply": ""}')

        result = BrainResult(reply="", tool_calls=[])
        await brain._sanitize_tool_phase_message(result)

        history = await service.get_history()
        assert history[-1].content == "[无需调用工具，继续对话]"
        assert history[-1].reasoning_content == ""

    @pytest.mark.asyncio
    async def test_real_reply_not_cleaned(self):
        """工具阶段已产出正式回复时保留原文，不净化。"""
        service = self._make_service()
        brain = Brain(service, AgentConfig())
        await service.append_message("assistant", "我已考虑好，接下来直接聊天")

        result = BrainResult(reply="我已考虑好，接下来直接聊天", tool_calls=[])
        await brain._sanitize_tool_phase_message(result)

        history = await service.get_history()
        assert history[-1].content == "我已考虑好，接下来直接聊天"

    @pytest.mark.asyncio
    async def test_tool_call_decision_cleaned(self):
        """含工具调用的 JSON 决策消息被替换为标记。"""
        service = self._make_service()
        brain = Brain(service, AgentConfig())
        await service.append_message("assistant", '{"tool_calls": [{"name": "memory_query"}]}')

        result = BrainResult(reply="", tool_calls=[{"name": "memory_query", "params": {}}])
        await brain._sanitize_tool_phase_message(result)

        history = await service.get_history()
        assert history[-1].content == "[已完成工具阶段分析]"


class TestAgentState:
    def test_state_values(self):
        assert AgentState.IDLE.value == "idle"
        assert AgentState.THINKING.value == "thinking"
        assert AgentState.SOUL_PHASE.value == "soul_phase"
        assert AgentState.COMPLETED.value == "completed"
        assert AgentState.ERROR.value == "error"

    def test_state_cycle_order(self):
        """验证状态枚举之间的逻辑关系"""
        states = [
            AgentState.IDLE,
            AgentState.CONTEXT_ASSEMBLY,
            AgentState.THINKING,
            AgentState.TOOL_EXECUTION,
            AgentState.OBSERVING,
            AgentState.SOUL_PHASE,
            AgentState.COMPLETED,
            AgentState.ERROR,
            AgentState.CANCELLED,
        ]
        assert len(states) == 9
        assert all(isinstance(s, AgentState) for s in states)


class TestAgentConstruction:
    """测试 AliyaAgent 构造（使用 mock）"""

    @pytest.mark.asyncio
    async def test_initial_state_is_idle(self, mocker):
        conv = mocker.AsyncMock()
        reg = mocker.MagicMock()
        agent = AliyaAgent(conversation_service=conv, tool_registry=reg)
        assert agent.state == AgentState.IDLE
        assert agent.turn == 0

    @pytest.mark.asyncio
    async def test_custom_config(self, mocker):
        conv = mocker.AsyncMock()
        reg = mocker.MagicMock()
        cfg = AgentConfig(max_turns=5)
        agent = AliyaAgent(conversation_service=conv, tool_registry=reg, config=cfg)
        assert agent._config.max_turns == 5


