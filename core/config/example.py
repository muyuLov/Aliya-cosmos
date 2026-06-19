"""ConfigManager 使用示例"""

import sys
from pathlib import Path

# 确保从任意位置直接运行时都能找到项目根目录
sys.path.insert(0, str(Path(__file__).parents[2]))

from core.config import ConfigManager, get_config_instance


def demo_get(config: ConfigManager) -> None:
    """点路径读取"""
    print(config.get("cosmos.logger.level"))
    print(config.get("cosmos.service.llm.providers.name"))
    print(config.get("cosmos.not.exist", default="默认值"))
    print("cosmos.logger.level" in config)  # True
    print("cosmos.not.exist" in config)  # False


def demo_set(config: ConfigManager) -> None:
    """点路径写入"""
    config.set("cosmos.logger.level", "debug")
    config.set("cosmos.logger.debug", True)
    print(config.get("cosmos.logger.level"))
    print(config.get("cosmos.logger.debug"))


def demo_fields(config: ConfigManager) -> None:
    """扁平化导出所有字段"""
    for path, value in config.get_all_fields().items():
        print(f"  {path}: {value}")


def demo_reload(config: ConfigManager) -> None:
    """热重载，覆盖所有运行时修改"""
    config.reload()
    print(config.get("cosmos.logger.level"))


def main() -> None:
    config = get_config_instance("data/config/main.yml")

    print("── get ──")
    demo_get(config)

    print("── set ──")
    demo_set(config)

    print("── fields ──")
    demo_fields(config)

    print("── reload ──")
    demo_reload(config)


if __name__ == "__main__":
    main()
