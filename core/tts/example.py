"""TTS 模块使用示例。

演示如何从配置文件加载 TTS 服务并进行流式语音合成与实时播放。

支持的 TTS 提供商：
- AstraTTS: 高质量本地 TTS 服务 @ http://127.0.0.1:5000
- EdgeTTS: 微软云端 TTS 服务（免费，需网络连接）
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 将项目根目录添加到 Python 路径
if __name__ == "__main__":
    project_root = Path(__file__).parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from core.exception import get_default_handler
from core.tts import (
    TTSRequest,
    VoiceConfig,
    create_from_config,
    create_player,
    create_service,
)
from core.tts.exceptions import TTSError


# ══════════════════════════════════════════════════════════════════════════════
# 示例 1：基础播放
# ══════════════════════════════════════════════════════════════════════════════

async def example_basic_playback() -> None:
    """示例 1：基础播放 - 从配置文件一键加载并播放。

    演示最简单的使用方式：加载配置、合成、播放。
    支持自动文本过滤（移除括号内的动作描写）。
    """
    print("\n=== 示例 1：基础播放 ===")

    # 包含动作描写的文本，系统会自动过滤括号内容
    text = "hello"

    try:
        service, player = create_from_config()
        async with service, player:
            print(f"📝 原始文本: {text}")
            await player.play_stream(service.synthesize(TTSRequest(text=text)))
    except TTSError as e:
        get_default_handler().handle(e)
        return

    print("✅ 播放完成")


# ══════════════════════════════════════════════════════════════════════════════
# 示例 2：细粒度控制
# ══════════════════════════════════════════════════════════════════════════════

async def example_fine_grained_control() -> None:
    """示例 2：细粒度控制 - 演示逐块送入与手动排空。

    展示如何使用 feed() 和 drain() 实现更精细的播放控制。
    适用于需要在播放过程中插入其他逻辑的场景。
    """
    print("\n=== 示例 2：细粒度控制 ===")

    text = (
        "这是第一段文本，用于演示细粒度控制。"
        "我们将逐块接收音频数据。"
        "每个音频块都会被单独处理。"
    )

    try:
        service, player = create_from_config()
        async with service, player:
            print(f"📝 文本内容: {text}")
            print("🔄 开始逐块送入音频数据...")

            chunk_count = 0
            total_bytes = 0
            async for chunk in service.synthesize(TTSRequest(text=text)):
                await player.feed(chunk)
                chunk_count += 1
                total_bytes += len(chunk)
                print(f"   已送入第 {chunk_count} 块，大小: {len(chunk)} 字节")

            print(f"📊 总计送入 {chunk_count} 个音频块，共 {total_bytes:,} 字节")

            print("⏳ 等待播放完成...")
            await player.drain()
    except TTSError as e:
        get_default_handler().handle(e)
        return

    print("✅ 播放完成")


# ══════════════════════════════════════════════════════════════════════════════
# 示例 3：多提供商支持
# ══════════════════════════════════════════════════════════════════════════════

async def example_multiple_providers() -> None:
    """示例 3：多提供商支持 - 演示不同 TTS 提供商的使用。

    展示如何手动创建不同的 TTS 提供商实例。
    包括 AstraTTS 和 EdgeTTS 的配置示例。
    """
    print("\n=== 示例 3：多提供商支持 ===")

    text = "这是一段测试文本，用于比较不同 TTS 提供商的效果。"

    # 测试 AstraTTS（如果可用）
    print("\n🎯 测试 AstraTTS 提供商...")
    try:
        astra_service = create_service(
            provider_name="astra",
            provider_config={
                "api_url": "http://127.0.0.1:5000",
                "default_avatar_id": "chenxing",
                "chunk_size": 4096,
                "timeout": 60,
            },
            voice_config=VoiceConfig(speed=1.0, languages=["zh", "en"]),
        )
        player = create_player()
        async with astra_service, player:
            print("   🔊 使用 AstraTTS 播放...")
            await player.play_stream(astra_service.synthesize(TTSRequest(text=text)))
            print("   ✅ AstraTTS 播放完成")
    except TTSError as e:
        print(f"   ❌ AstraTTS 不可用: {e}")

    # 测试 EdgeTTS（需要网络连接）
    print("\n🌐 测试 EdgeTTS 提供商...")
    try:
        edge_service = create_service(
            provider_name="edge",
            provider_config={
                "voice": "zh-CN-XiaoxiaoNeural",
                "rate": "+0%",
                "volume": "+0%",
                "pitch": "+0Hz",
                "timeout": 60,
            },
        )
        player = create_player()
        async with edge_service, player:
            print("   🔊 使用 EdgeTTS 播放...")
            await player.play_stream(edge_service.synthesize(TTSRequest(text=text)))
            print("   ✅ EdgeTTS 播放完成")
    except TTSError as e:
        print(f"   ❌ EdgeTTS 不可用: {e}")

    print("✅ 多提供商测试完成")


# ══════════════════════════════════════════════════════════════════════════════
# 示例 4：音色配置
# ══════════════════════════════════════════════════════════════════════════════

async def example_voice_configuration() -> None:
    """示例 4：音色配置 - 演示不同语速的效果。"""
    print("\n=== 示例 4：音色配置 ===")

    base_text = "这是音色配置测试。"
    speeds = [0.5, 1.0, 1.5, 2.0]

    try:
        service, player = create_from_config()
        async with service, player:
            for speed in speeds:
                print(f"\n🎵 测试语速: {speed}x")
                voice_config = VoiceConfig(speed=speed, languages=["zh", "en"])
                request = voice_config.apply_to_request(
                    TTSRequest(text=f"{base_text}当前语速为{speed}倍。")
                )
                await player.play_stream(service.synthesize(request))
                print(f"   ✅ 语速 {speed}x 播放完成")
                await asyncio.sleep(0.5)
    except TTSError as e:
        get_default_handler().handle(e)
        return

    print("✅ 音色配置测试完成")


# ══════════════════════════════════════════════════════════════════════════════
# 主函数
# ══════════════════════════════════════════════════════════════════════════════

async def main() -> None:
    """运行所有示例。"""
    print("🎵 TTS 模块功能演示")
    print("=" * 60)
    print("支持的 TTS 提供商:")
    print("  • AstraTTS: 高质量本地 TTS 服务")
    print("  • EdgeTTS: 微软云端 TTS 服务（免费）")
    print("=" * 60)

    # 示例 1：基础播放
    await example_basic_playback()

    # 示例 2：细粒度控制
    # await example_fine_grained_control()

    # 示例 3：多提供商支持
    # await example_multiple_providers()

    # 示例 4：音色配置
    # await example_voice_configuration()

    print("\n" + "=" * 60)
    print("🎉 所有示例执行完成！")
    print("\n💡 提示:")
    print("  • 可以修改 data/config/main.yml 切换不同的 TTS 提供商")
    print("  • AstraTTS 需要本地服务运行在 http://127.0.0.1:5000")
    print("  • EdgeTTS 需要网络连接，但无需额外配置")


if __name__ == "__main__":
    asyncio.run(main())
