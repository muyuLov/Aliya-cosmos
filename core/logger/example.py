"""LogManager 使用示例"""

# 运行方式：在项目根目录执行 `uv run python -m core.logger.example`

from core.logger import get_logger, get_manager, setup


def demo_basic() -> None:
    """基础用法：默认配置（INFO 级别，仅控制台）"""
    setup()
    logger = get_logger("demo.basic")

    logger.debug("这条不会显示（低于 INFO）")
    logger.info("服务启动")
    logger.warning("磁盘空间不足")
    logger.error("连接超时")
    logger.critical("系统崩溃")


def demo_with_config() -> None:
    """自定义配置：开启文件输出与彩色控制台"""
    setup(
        {
            "level": "debug",
            "console": {"enabled": True, "color": True},
            "file": {
                "enabled": True,
                "path": "data/log/cosmos.log",
                "rotate": "timed",  # 按天轮转
                "when": "midnight",
                "backup_count": 30,
            },
        }
    )
    logger = get_logger("demo.config")
    logger.debug("DEBUG 消息（同时写入文件）")
    logger.info("INFO 消息（同时写入文件）")
    get_manager().shutdown()


def demo_extra_fields() -> None:
    """extra 附加字段：在结构化日志中保留自定义字段"""
    setup({"level": "info", "structured": True})
    logger = get_logger("demo.extra")
    logger.info("cosmos登录", extra={"user_id": 42, "ip": "127.0.0.1"})
    logger.warning("权限不足", extra={"resource": "/admin", "action": "DELETE"})


def demo_debug_mode() -> None:
    """动态切换 debug 模式"""
    setup({"level": "info"})
    logger = get_logger("demo.debug_mode")
    mgr = get_manager()

    logger.debug("不可见（当前 INFO）")
    logger.info("可见")

    mgr.set_debug_mode(True)
    logger.debug("现在可见了（已切换到 DEBUG）")

    mgr.set_debug_mode(False)
    logger.debug("又不可见了（恢复 INFO）")
    logger.info("恢复正常")


def demo_dynamic_level() -> None:
    """运行时动态调整日志级别"""
    setup({"level": "warning"})
    logger = get_logger("demo.level")
    mgr = get_manager()

    logger.info("不可见（当前 WARNING）")
    logger.warning("可见")

    mgr.set_global_level("info")
    logger.info("现在可见了（已调整为 INFO）")


def demo_reload_config() -> None:
    """热重载配置：切换为 JSON 结构化输出"""
    setup({"level": "info", "console": {"enabled": True, "color": True}})
    logger = get_logger("demo.reload")
    logger.info("普通彩色输出")

    get_manager().reload_config({"level": "info", "structured": True})
    logger.info("重载后切换为 JSON 输出", extra={"version": "0.1.0"})


def demo_config_manager_integration() -> None:
    """与 ConfigManager 集成：从 YAML 读取日志配置"""
    setup("data/config/main.yml")

    logger = get_logger("demo.integration")
    logger.info("从 YAML 加载配置完成")


def demo_multithread() -> None:
    """多线程：验证线程名显示与列对齐"""
    import threading

    setup({"level": "debug"})

    def worker(name: str, count: int) -> None:
        logger = get_logger(f"demo.thread.{name}")
        for i in range(count):
            logger.debug("[%d] debug 消息", i)
            logger.info("[%d] info 消息", i)
            logger.warning("[%d] warning 消息", i)

    threads = [
        threading.Thread(target=worker, args=("alpha", 2), name="Thread-Alpha"),
        threading.Thread(target=worker, args=("beta", 2), name="Thread-Beta"),
        threading.Thread(target=worker, args=("long-name-gamma", 2), name="Thread-LongNameGamma"),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def demo_colors() -> None:
    """彩色输出：展示各级别颜色效果"""
    setup({"level": "debug"})
    logger = get_logger("demo.colors")
    logger.debug("DEBUG   - 青色消息")
    logger.info("INFO    - 绿色消息")
    logger.warning("WARNING - 黄色消息")
    logger.error("ERROR   - 红色消息")
    logger.critical("CRITICAL - 红底白字消息")


def main() -> None:
    print("── 基础用法 ──")
    demo_basic()

    print("\n── 彩色输出展示 ──")
    demo_colors()

    print("\n── 自定义配置（含文件输出）──")
    demo_with_config()

    print("\n── extra 附加字段（JSON 模式）──")
    demo_extra_fields()

    print("\n── 动态切换 debug 模式 ──")
    demo_debug_mode()

    print("\n── 运行时调整日志级别 ──")
    demo_dynamic_level()

    print("\n── 热重载配置 ──")
    demo_reload_config()

    print("\n── 与 ConfigManager 集成 ──")
    demo_config_manager_integration()

    print("\n── 多线程线程名显示 ──")
    demo_multithread()


if __name__ == "__main__":
    main()
