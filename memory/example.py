
"""记忆系统使用示例

演示 GRAG 知识图谱记忆系统的完整功能，包括：
- 对话记忆添加与五元组提取
- 知识图谱查询与RAG检索
- 任务管理与并发处理
- 图谱统计与维护操作

依赖服务：Neo4j @ bolt://localhost:7687
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 将项目根目录添加到 Python 路径
if __name__ == "__main__":
    project_root = Path(__file__).parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from core.exception import get_default_handler
from memory import (
    create_memory_service,
    get_memory_manager,
    get_service_status,
    store_quintuples,
    query_graph_by_keywords,
    get_graph_stats,
    clear_all_quintuples,
    start_task_manager,
    stop_task_manager,
    GRAGError,
)


async def example_basic_memory_usage() -> None:
    """示例 1：基础记忆功能 - 添加对话记忆并查询。

    演示最简单的使用方式：添加对话、自动提取五元组、查询记忆。
    """
    print("\n=== 示例 1：基础记忆功能 ===")

    try:
        # 获取记忆管理器
        memory_mgr = get_memory_manager()
        
        if not memory_mgr.enabled:
            print("⚠️  记忆系统未启用，请检查配置文件")
            return

        # 添加对话记忆
        user_input = "我叫张三，今年25岁，住在北京，喜欢编程和阅读。"
        ai_response = "很高兴认识你，张三！你的兴趣爱好很棒，编程和阅读都是很有意义的活动。"
        
        print(f"👤 用户: {user_input}")
        print(f"🤖 AI: {ai_response}")
        
        success = await memory_mgr.add_conversation_memory(
            user_input=user_input,
            ai_response=ai_response,
            session_id="demo_session_1"
        )
        
        if success:
            print("✅ 对话记忆已添加")
            
            # 等待五元组提取完成
            print("⏳ 等待五元组提取...")
            await asyncio.sleep(3)
            
            # 查询记忆
            question = "张三的基本信息是什么？"
            print(f"❓ 查询: {question}")
            
            answer = await memory_mgr.query_memory(question)
            if answer:
                print(f"💡 回答: {answer}")
            else:
                print("❌ 未找到相关记忆")
        else:
            print("❌ 添加对话记忆失败")
            
    except GRAGError as e:
        get_default_handler().handle(e)
        return

    print("✅ 基础记忆功能演示完成")


async def example_graph_operations() -> None:
    """示例 2：图谱操作 - 直接操作知识图谱。

    演示如何直接存储和查询五元组，不依赖对话记忆。
    """
    print("\n=== 示例 2：图谱操作 ===")

    try:
        # 准备测试五元组
        test_quintuples = [
            ("李四", "人物", "工作于", "阿里巴巴", "组织"),
            ("李四", "人物", "居住在", "杭州", "地点"),
            ("李四", "人物", "擅长", "Python编程", "技能"),
            ("阿里巴巴", "组织", "位于", "杭州", "地点"),
            ("Python编程", "技能", "属于", "软件开发", "概念"),
        ]
        
        print("📝 存储测试五元组...")
        for quintuple in test_quintuples:
            print(f"   {quintuple[0]}({quintuple[1]}) —[{quintuple[2]}]→ {quintuple[3]}({quintuple[4]})")
        
        # 存储五元组
        success = store_quintuples(
            test_quintuples,
            source_text="测试数据：李四的基本信息",
            session_id="demo_session_2"
        )
        
        if success:
            print("✅ 五元组存储成功")
            
            # 查询图谱
            keywords = ["李四", "阿里巴巴", "Python"]
            print(f"🔍 关键词查询: {keywords}")
            
            results = query_graph_by_keywords(keywords, limit=10)
            if results:
                print(f"📊 查询结果 ({len(results)} 条):")
                for i, (h, h_type, rel, t, t_type) in enumerate(results, 1):
                    print(f"   {i}. {h}({h_type}) —[{rel}]→ {t}({t_type})")
            else:
                print("❌ 未找到匹配的关系")
        else:
            print("❌ 五元组存储失败")
            
    except GRAGError as e:
        get_default_handler().handle(e)
        return

    print("✅ 图谱操作演示完成")


async def example_task_management() -> None:
    """示例 3：任务管理 - 演示并发任务处理。

    展示如何使用任务管理器进行并发五元组提取。
    """
    print("\n=== 示例 3：任务管理 ===")

    try:
        # 启动任务管理器
        await start_task_manager()
        print("🚀 任务管理器已启动")
        
        # 获取任务管理器
        from memory.task_manager import get_task_manager
        task_mgr = get_task_manager()
        
        # 准备多个文本进行并发提取
        texts = [
            "王五是一名软件工程师，在腾讯工作，负责微信开发。",
            "赵六毕业于清华大学，专业是计算机科学，现在在字节跳动实习。",
            "钱七喜欢旅游，去过日本、韩国和泰国，最喜欢的城市是京都。",
        ]
        
        print(f"📋 提交 {len(texts)} 个提取任务...")
        task_ids = []
        
        # 提交任务
        for i, text in enumerate(texts, 1):
            task_id = await task_mgr.add_task(text)
            task_ids.append(task_id)
            print(f"   任务 {i}: {task_id} - {text[:30]}...")
        
        # 等待任务完成
        print("⏳ 等待任务完成...")
        results = []
        
        for task_id in task_ids:
            result, error = await task_mgr.get_task_result(task_id, timeout=30)
            if result:
                results.extend(result)
                print(f"✅ 任务 {task_id[:12]}... 完成，提取 {len(result)} 个五元组")
            else:
                print(f"❌ 任务 {task_id[:12]}... 失败: {error}")
        
        # 显示统计信息
        stats = task_mgr.get_stats()
        print(f"📊 任务统计:")
        print(f"   总任务数: {stats['total_tasks']}")
        print(f"   已完成: {stats['completed_tasks']}")
        print(f"   失败: {stats['failed_tasks']}")
        print(f"   工作协程: {stats['max_workers']}")
        
        if results:
            print(f"🎯 总共提取到 {len(results)} 个五元组")
            
    except GRAGError as e:
        get_default_handler().handle(e)
        return

    print("✅ 任务管理演示完成")


async def example_rag_query() -> None:
    """示例 4：RAG 查询 - 演示知识检索与回答生成。

    展示如何使用 RAG 引擎进行智能问答。
    """
    print("\n=== 示例 4：RAG 查询 ===")

    try:
        memory_mgr = get_memory_manager()
        
        if not memory_mgr.enabled:
            print("⚠️  记忆系统未启用")
            return
        
        # 添加一些上下文对话
        conversations = [
            ("我在学习机器学习，最近在研究深度学习算法。", "深度学习确实是机器学习的重要分支，有什么具体问题吗？"),
            ("我对卷积神经网络特别感兴趣，想了解它在图像识别中的应用。", "CNN在图像识别中应用广泛，从基础的分类到复杂的目标检测都有涉及。"),
            ("我还想学习自然语言处理，听说Transformer架构很重要。", "是的，Transformer彻底改变了NLP领域，BERT、GPT等都基于这个架构。"),
        ]
        
        print("📚 添加学习对话记忆...")
        for i, (user_msg, ai_msg) in enumerate(conversations, 1):
            await memory_mgr.add_conversation_memory(
                user_input=user_msg,
                ai_response=ai_msg,
                session_id=f"learning_session_{i}"
            )
            print(f"   对话 {i} 已添加")
        
        # 等待提取完成
        print("⏳ 等待五元组提取...")
        await asyncio.sleep(5)
        
        # 进行 RAG 查询
        questions = [
            "我在学习什么技术？",
            "深度学习和机器学习是什么关系？",
            "Transformer架构有什么重要性？",
            "我对什么算法特别感兴趣？",
        ]
        
        print("🤔 开始 RAG 查询...")
        for i, question in enumerate(questions, 1):
            print(f"\n❓ 问题 {i}: {question}")
            
            answer = await memory_mgr.query_memory(question)
            if answer:
                print(f"💡 回答: {answer}")
            else:
                print("❌ 未找到相关信息")
                
    except GRAGError as e:
        get_default_handler().handle(e)
        return

    print("\n✅ RAG 查询演示完成")


async def example_memory_statistics() -> None:
    """示例 5：记忆统计 - 展示系统状态和统计信息。

    演示如何获取和分析记忆系统的运行状态。
    """
    print("\n=== 示例 5：记忆统计 ===")

    try:
        # 获取服务状态
        print("📊 记忆服务状态:")
        service_status = get_service_status()
        
        for key, value in service_status.items():
            if isinstance(value, dict):
                print(f"   {key}:")
                for sub_key, sub_value in value.items():
                    print(f"     {sub_key}: {sub_value}")
            else:
                print(f"   {key}: {value}")
        
        # 获取图谱统计
        print("\n🕸️  图谱统计:")
        graph_stats = get_graph_stats()
        
        for key, value in graph_stats.items():
            if key == "entity_type_distribution" and isinstance(value, dict):
                print(f"   {key}:")
                for label, count in value.items():
                    print(f"     {label}: {count}")
            else:
                print(f"   {key}: {value}")
        
        # 获取记忆管理器统计
        memory_mgr = get_memory_manager()
        print("\n🧠 记忆管理器统计:")
        memory_stats = memory_mgr.get_memory_stats()
        
        for key, value in memory_stats.items():
            if isinstance(value, dict):
                print(f"   {key}:")
                for sub_key, sub_value in value.items():
                    print(f"     {sub_key}: {sub_value}")
            else:
                print(f"   {key}: {value}")
                
    except GRAGError as e:
        get_default_handler().handle(e)
        return

    print("✅ 记忆统计演示完成")


async def example_memory_maintenance() -> None:
    """示例 6：记忆维护 - 演示清理和维护操作。

    展示如何进行记忆系统的维护操作，包括清理和重置。
    """
    print("\n=== 示例 6：记忆维护 ===")

    try:
        memory_mgr = get_memory_manager()
        
        if not memory_mgr.enabled:
            print("⚠️  记忆系统未启用")
            return
        
        # 显示清理前的统计
        print("🔍 清理前的图谱状态:")
        stats_before = get_graph_stats()
        print(f"   节点数: {stats_before.get('entity_count', 0)}")
        print(f"   关系数: {stats_before.get('relation_count', 0)}")
        
        # 询问用户是否要清理（在实际使用中）
        print("\n⚠️  注意：以下操作将清空所有记忆数据！")
        print("   在生产环境中，请谨慎执行此操作。")
        
        # 演示清理操作（注释掉以避免意外清理）
        # print("🧹 开始清理记忆...")
        # success = await memory_mgr.clear_memory()
        # 
        # if success:
        #     print("✅ 记忆清理完成")
        #     
        #     # 显示清理后的统计
        #     print("🔍 清理后的图谱状态:")
        #     stats_after = get_graph_stats()
        #     print(f"   节点数: {stats_after.get('entity_count', 0)}")
        #     print(f"   关系数: {stats_after.get('relation_count', 0)}")
        # else:
        #     print("❌ 记忆清理失败")
        
        print("ℹ️  清理操作已注释，如需执行请取消注释相关代码")
        
        # 演示任务清理
        from memory.task_manager import get_task_manager
        task_mgr = get_task_manager()
        
        print("\n🧹 清理已完成的任务...")
        cleaned_count = await task_mgr.clear_completed_tasks(max_age_hours=0)  # 清理所有已完成任务
        print(f"✅ 清理了 {cleaned_count} 个已完成任务")
        
    except GRAGError as e:
        get_default_handler().handle(e)
        return

    print("✅ 记忆维护演示完成")


async def main() -> None:
    """运行所有示例。

    依次执行各个功能示例，展示记忆系统的完整能力。
    可以注释掉不需要的示例以单独测试某个功能。
    """
    print("🧠 GRAG 记忆系统功能演示")
    print("=" * 60)
    
    try:
        # 检查系统状态
        service_status = get_service_status()
        if not service_status.get("enabled", False):
            print("❌ 记忆系统未启用，请检查以下配置：")
            print("   1. data/config/main.yml 中 cosmos.service.grag.enabled = true")
            print("   2. Neo4j 服务正在运行")
            print("   3. Neo4j 连接配置正确")
            return
        
        print("✅ 记忆系统已启用，开始演示...")
        
        # 示例 1：基础记忆功能
        await example_basic_memory_usage()
        
        # 示例 2：图谱操作
        await example_graph_operations()
        
        # 示例 3：任务管理
        await example_task_management()
        
        # 示例 4：RAG 查询
        await example_rag_query()
        
        # 示例 5：记忆统计
        await example_memory_statistics()
        
        # 示例 6：记忆维护
        await example_memory_maintenance()
        
    except KeyboardInterrupt:
        print("\n⏹️  演示被用户中断")
    except Exception as e:
        print(f"\n❌ 演示过程中发生错误: {e}")
        get_default_handler().handle(e)
    finally:
        # 清理资源
        try:
            await stop_task_manager()
            print("\n🛑 任务管理器已停止")
        except Exception as e:
            print(f"⚠️  停止任务管理器时出错: {e}")
    
    print("\n" + "=" * 60)
    print("🎉 所有示例执行完成！")
    print("\n💡 使用提示：")
    print("   - 确保 Neo4j 服务正在运行")
    print("   - 检查 data/config/main.yml 中的 GRAG 配置")
    print("   - 根据需要调整配置参数")
    print("   - 在生产环境中谨慎使用清理功能")


if __name__ == "__main__":
    asyncio.run(main())