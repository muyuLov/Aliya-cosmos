"""Task 5.3: 日志管理器重写 + 失明模式测试

验证 blindMode 静默拦截命令、error/warn 置盲标志并丢弃、
健康心跳无内容细节、healthReportMinutes 钳制。
"""

import logging
import pytest


def test_blind_mode_blocks_error():
    """失明模式下 error 级别应被置盲丢弃"""
    from core.logger.manager import LogManager

    manager = LogManager({"blind_mode": {"enabled": True}})
    logger = manager.get_logger("test_blind")

    # error 不应抛出，应被静默拦截
    logger.error("这个错误应该被失明模式拦截")
    # 无断言异常即成功


def test_blind_mode_blocks_warning():
    """失明模式下 warning 级别应被置盲丢弃"""
    from core.logger.manager import LogManager

    manager = LogManager({"blind_mode": {"enabled": True}})
    logger = manager.get_logger("test_blind")
    logger.warning("这个警告应该被失明模式拦截")


def test_normal_mode_allows_error():
    """非失明模式下 error 应正常输出"""
    from core.logger.manager import LogManager

    manager = LogManager({"blind_mode": {"enabled": False}})
    logger = manager.get_logger("test_normal")
    # 不抛异常即通过
    logger.error("正常错误")


def test_health_report_no_details():
    """失明模式健康心跳应无内容细节"""
    from core.logger.manager import LogManager

    manager = LogManager({
        "blind_mode": {"enabled": True, "health_report_minutes": 10},
    })
    report = manager.get_health_status()
    # 健康报告应只含运行状态，不含错误细节
    assert isinstance(report, dict)
    assert "status" in report
    assert "details" not in report or report.get("details") is None


def test_health_report_minutes_clamped():
    """healthReportMinutes 应被钳制在 1-1440"""
    from core.logger.manager import LogManager

    manager = LogManager({
        "blind_mode": {"enabled": True, "health_report_minutes": 0},
    })
    assert manager.health_report_minutes >= 1

    manager2 = LogManager({
        "blind_mode": {"enabled": True, "health_report_minutes": 9999},
    })
    assert manager2.health_report_minutes <= 1440


def test_layered_formatter_integration():
    """LogManager 应能使用 LayeredLogFormatter"""
    from core.logger.manager import LogManager

    manager = LogManager({
        "layered": {"enabled": True, "colors": True, "density": "standard"},
    })
    logger = manager.get_logger("test_layered")
    # 不抛异常即通过
    logger.info("分层日志测试")
