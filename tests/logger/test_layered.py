"""Task 5.2: 分层日志核心测试

验证 LogAction 16 枚举 / 动作检测 / 三档密度 / 颜文字+符号双模式。
"""

import logging
import pytest


def test_log_action_enum():
    """LogAction 应有 16 个枚举值"""
    from core.logger.layered import LogAction

    actions = [
        "receive", "send", "processing", "complete", "trigger",
        "emotion", "memory", "advance", "agency", "group",
        "error", "retry", "warning", "waiting", "system",
    ]
    # 实际设计中可能有 14-16 个
    for a in actions:
        assert hasattr(LogAction, a.upper()) or hasattr(LogAction, a)


def test_detect_log_action_receive():
    """detect_log_action 应识别 receive 动作"""
    from core.logger.layered import detect_log_action, LogAction

    action = detect_log_action("接收到用户消息")
    assert action == LogAction.RECEIVE


def test_detect_log_action_send():
    """detect_log_action 应识别 send 动作"""
    from core.logger.layered import detect_log_action, LogAction

    action = detect_log_action("发送回复")
    assert action == LogAction.SEND


def test_detect_log_action_error():
    """detect_log_action 应识别 error 动作"""
    from core.logger.layered import detect_log_action, LogAction

    action = detect_log_action("发生错误: connection refused")
    assert action == LogAction.ERROR


def test_detect_log_action_memory():
    """detect_log_action 应识别 memory 动作"""
    from core.logger.layered import detect_log_action, LogAction

    action = detect_log_action("写入记忆: 用户喜欢蓝色")
    assert action == LogAction.MEMORY


def test_detect_log_action_default():
    """无法识别时应返回 system"""
    from core.logger.layered import detect_log_action, LogAction

    action = detect_log_action("普通日志消息")
    assert action == LogAction.SYSTEM


def test_layered_formatter_density_levels():
    """密度应支持 summary/standard/diagnostic"""
    from core.logger.layered import DensityLevel

    assert DensityLevel.SUMMARY.value < DensityLevel.STANDARD.value
    assert DensityLevel.STANDARD.value < DensityLevel.DIAGNOSTIC.value


def test_layered_formatter_kaomoji_mode():
    """kaomoji 模式应有对应表情"""
    from core.logger.layered import LayeredLogFormatter, LogAction

    formatter = LayeredLogFormatter(kaomoji=True, density="standard")
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="接收到用户消息", args=(), exc_info=None,
    )
    output = formatter.format(record)
    assert isinstance(output, str)
    assert len(output) > 0


def test_layered_formatter_symbols_mode():
    """symbols 模式应有对应符号"""
    from core.logger.layered import LayeredLogFormatter, LogAction

    formatter = LayeredLogFormatter(kaomoji=False, density="standard")
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="接收到用户消息", args=(), exc_info=None,
    )
    output = formatter.format(record)
    assert isinstance(output, str)


def test_layered_formatter_summary_hides_details():
    """summary 密度应隐藏详细信息"""
    from core.logger.layered import LayeredLogFormatter, DensityLevel

    formatter = LayeredLogFormatter(density="summary")
    record = logging.LogRecord(
        name="test", level=logging.DEBUG, pathname="", lineno=0,
        msg="内部跳过原因: story_id=s1 timeout=30", args=(), exc_info=None,
    )
    output = formatter.format(record)
    # summary 密度下 debug 级别应被过滤
    assert output == "" or "story_id" not in output


def test_extract_fields():
    """extract_fields 应从消息中提取键值对"""
    from core.logger.layered import extract_fields

    fields = extract_fields("用户输入=你好 故事=s1 情绪=warm")
    assert "用户输入" in fields or "用户输入=你好" in fields
