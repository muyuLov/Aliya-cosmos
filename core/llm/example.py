"""LLM 模块使用示例

演示 LLM 对话服务的各种使用方式，包括：
- 上下文管理器（推荐）
- 同步/异步调用
- 流式对话
- 补丁注入（情绪、技能、工具、记忆）
- 多会话管理
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
from core.llm import ContextCache, ConversationService, create_from_config
from core.llm.exceptions import LLMRequestError


# ══════════════════════════════════════════════════════════════════════════════
# 示例 1：上下文管理器（推荐方式）
# ══════════════════════════════════════════════════════════════════════════════

async def example_context_manager() -> None:
    """示例 1：使用上下文管理器自动管理资源（推荐）。
    
    上下文管理器会在退出时自动调用 aclose()，释放底层提供商资源
    （如 HTTP 连接、线程池等），确保资源不泄漏。
    """
    print("\n=== 示例 1：上下文管理器（推荐） ===")
    
    try:
        # 使用 async with 自动管理资源生命周期
        async with create_from_config() as service:
            # 对话 1
            reply1 = await service.asend("你好，请用一句话介绍自己")
            print(f"回复 1: {reply1}")
            
            # 对话 2（有上下文）
            reply2 = await service.asend("你刚才说了什么？")
            print(f"回复 2: {reply2}")
            
        # 退出上下文后，service 已自动释放资源
        print("✅ 资源已自动释放")
        
    except LLMRequestError as e:
        get_default_handler().handle(e)


# ══════════════════════════════════════════════════════════════════════════════
# 示例 2：流式对话
# ══════════════════════════════════════════════════════════════════════════════

async def example_stream_chat() -> None:
    """示例 2：流式对话，逐 token 输出。
    
    适用场景：
    - 实时 TTS 语音合成
    - 前端 SSE 推送
    - 用户体验优化（降低首字延迟）
    """
    print("\n=== 示例 2：流式对话 ===")
    
    try:
        async with create_from_config() as service:
            print("📝 用户: 讲一个三句话的故事")
            print("🤖 助手: ", end="", flush=True)
            
            full_reply: list[str] = []
            async for token in service.astream_send("讲一个三句话的故事"):
                print(token, end="", flush=True)
                full_reply.append(token)
            
            print()  # 换行
            print(f"✅ 完整回复长度: {len(''.join(full_reply))} 字符")
            
    except LLMRequestError as e:
        get_default_handler().handle(e)


# ══════════════════════════════════════════════════════════════════════════════
# 示例 3：补丁注入
# ══════════════════════════════════════════════════════════════════════════════

async def example_patch_injection() -> None:
    """示例 3：动态注入情绪、技能、工具、记忆等补丁。
    
    补丁在当前对话完成后自动清除，不残留到下一轮。
    适用场景：
    - 情感引擎动态调整情绪
    - Agent 注入可用工具列表
    - GRAG 记忆检索结果注入
    - 技能系统动态加载
    """
    print("\n=== 示例 3：补丁注入 ===")
    
    try:
        async with create_from_config() as service:
            # 第一轮：注入情绪补丁
            await service.set_emotion_patch("[当前情绪: 兴奋、充满活力]")
            reply1 = await service.asend("今天天气真好")
            print(f"带情绪补丁的回复: {reply1}")
            
            # 第二轮：注入多种上下文
            await service.set_emotion_patch("[当前情绪: 平静、专注]")
            await service.set_context_injection(
                skills="- 技能：代码助手\n- 技能：学习导师",
                tools="- 工具：搜索引擎\n- 工具：计算器",
                memory="[记忆：用户喜欢简洁的回答，不喜欢冗长的解释]"
            )
            reply2 = await service.asend("帮我计算 123 + 456")
            print(f"带完整上下文的回复: {reply2}")
            
            # 第三轮：无补丁（上一轮的补丁已自动清除）
            reply3 = await service.asend("你现在是什么情绪？")
            print(f"无补丁的回复: {reply3}")
            
    except LLMRequestError as e:
        get_default_handler().handle(e)


# ══════════════════════════════════════════════════════════════════════════════
# 示例 4：多会话管理
# ══════════════════════════════════════════════════════════════════════════════

async def example_multi_conversation() -> None:
    """示例 4：多会话隔离管理。
    
    通过不同的 conversation_id 实现会话隔离，
    每个会话有独立的历史记录和上下文。
    """
    print("\n=== 示例 4：多会话管理 ===")
    
    # 创建共享缓存
    cache = ContextCache(ttl=3600)
    
    try:
        # 会话 1：技术讨论
        async with create_from_config(
            conversation_id="tech_chat",
            cache=cache,
            system_prompt="你是一个技术专家。"
        ) as service1:
            reply1 = await service1.asend("什么是异步编程？")
            print(f"会话1（技术）: {reply1[:80]}...")
        
        # 会话 2：日常聊天
        async with create_from_config(
            conversation_id="casual_chat",
            cache=cache,
            system_prompt="你是一个友好的聊天伙伴。"
        ) as service2:
            reply2 = await service2.asend("今天天气怎么样？")
            print(f"会话2（日常）: {reply2[:80]}...")
        
        # 重新进入会话 1（有上下文）
        async with create_from_config(
            conversation_id="tech_chat",
            cache=cache,
            system_prompt="你是一个技术专家。"
        ) as service1:
            reply3 = await service1.asend("能举个例子吗？")
            print(f"会话1（续）: {reply3[:80]}...")
            
            # 查看会话历史
            history = await service1.get_history()
            print(f"\n会话1的历史记录: {len(history)} 条消息")
            for msg in history:
                print(f"  {msg.role}: {msg.content[:50]}...")
                
    except LLMRequestError as e:
        get_default_handler().handle(e)


# ══════════════════════════════════════════════════════════════════════════════
# 示例 5：同步调用（脚本/REPL 场景）
# ══════════════════════════════════════════════════════════════════════════════

def example_sync_call() -> None:
    """示例 5：同步调用方式（适用于脚本/REPL）。
    
    内部通过 asyncio.run() 驱动异步路径，
    不推荐在已有事件循环的异步应用中使用。
    """
    print("\n=== 示例 5：同步调用 ===")
    
    try:
        # 注意：不能在上下文管理器中使用同步调用
        service = create_from_config()
        
        try:
            # 同步发送消息
            reply = service.send("用一句话介绍 Python")
            print(f"同步回复: {reply}")
        finally:
            # 确保资源始终被释放
            asyncio.run(service.aclose())
        
    except LLMRequestError as e:
        get_default_handler().handle(e)


# ══════════════════════════════════════════════════════════════════════════════
# 示例 6：手动资源管理
# ══════════════════════════════════════════════════════════════════════════════

async def example_manual_cleanup() -> None:
    """示例 6：手动资源管理（不推荐）。
    
    如果不使用上下文管理器，必须手动调用 aclose()。
    推荐使用 async with 自动管理资源。
    """
    print("\n=== 示例 6：手动资源管理（不推荐） ===")
    
    service = None
    try:
        service = create_from_config()
        reply = await service.asend("你好")
        print(f"回复: {reply}")
        
    except LLMRequestError as e:
        get_default_handler().handle(e)
        
    finally:
        # 必须手动释放资源
        if service is not None:
            await service.aclose()
            print("✅ 资源已手动释放")


# ══════════════════════════════════════════════════════════════════════════════
# 示例 7：错误处理与重试
# ══════════════════════════════════════════════════════════════════════════════

async def example_error_handling() -> None:
    """示例 7：错误处理与自动重试。
    
    ConversationService 内置指数退避重试机制，
    默认最多重试 3 次（1s / 2s / 4s 间隔）。
    """
    print("\n=== 示例 7：错误处理与重试 ===")
    
    try:
        async with create_from_config() as service:
            # 正常调用（内置重试）
            reply = await service.asend("你好", max_retries=3)
            print(f"回复: {reply}")
            
            # 自定义重试次数
            reply = await service.asend("再见", max_retries=1)
            print(f"回复: {reply}")
            
    except LLMRequestError as e:
        # 所有重试失败后抛出异常
        print(f"❌ 请求失败: {e}")
        get_default_handler().handle(e)


# ══════════════════════════════════════════════════════════════════════════════
# 示例 8：历史管理
# ══════════════════════════════════════════════════════════════════════════════

async def example_history_management() -> None:
    """示例 8：消息历史管理。
    
    演示如何查看、清空、手动追加消息历史。
    """
    print("\n=== 示例 8：历史管理 ===")
    
    try:
        async with create_from_config() as service:
            # 对话 1
            await service.asend("我叫张三")
            # 对话 2
            await service.asend("我今年25岁")
            # 对话 3
            reply = await service.asend("我叫什么名字？几岁？")
            print(f"回复: {reply}")
            
            # 查看历史
            history = await service.get_history()
            print(f"\n历史记录: {len(history)} 条")
            for msg in history:
                print(f"  {msg.role}: {msg.content}")
            
            # 手动追加消息（用于 Agent 推理循环）
            await service.append_message("system", "[系统提示：请保持简洁]")
            
            # 清空历史
            await service.clear_history()
            print("\n✅ 历史已清空")
            
            # 清空后的对话（无上下文）
            reply = await service.asend("我叫什么名字？")
            print(f"清空后的回复: {reply}")
            
    except LLMRequestError as e:
        get_default_handler().handle(e)


# ══════════════════════════════════════════════════════════════════════════════
# 主函数
# ══════════════════════════════════════════════════════════════════════════════

async def main() -> None:
    """运行所有示例。"""
    print("🤖 LLM 模块功能演示")
    print("=" * 80)
    
    # 示例 1：上下文管理器（推荐）
    await example_context_manager()
    
    # 示例 2：流式对话
    await example_stream_chat()
    
    # 示例 3：补丁注入
    await example_patch_injection()
    
    # 示例 4：多会话管理
    await example_multi_conversation()
    
    
    print("\n" + "=" * 80)
    print("🎉 所有示例执行完成！")
    print("\n💡 最佳实践:")
    print("  • 推荐使用 async with 自动管理资源")
    print("  • 异步应用中使用 asend() 而非 send()")
    print("  • 流式对话用 astream_send() 降低首字延迟")
    print("  • 使用共享缓存实现多会话管理")
    print("  • 补丁机制用于动态上下文注入")


if __name__ == "__main__":
    asyncio.run(main())
