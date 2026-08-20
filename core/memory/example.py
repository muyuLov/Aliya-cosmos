"""memory 模块使用示例
- 对话记忆的添加与五元组提取
- 知识图谱查询与 RAG 检索
- 跨对话的记忆关联（Aliya、Kane、Ryoko、泰瑞斯公司）
- 任务管理与并发处理
- 图谱统计与维护操作

依赖服务：Neo4j @ bolt://localhost:7687
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if __name__ == "__main__":
    project_root = Path(__file__).parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from core.exception import get_default_handler
from core.logger import setup as setup_logger
from core.memory import (
    get_memory_manager,
    get_service_status,
    store_quintuples,
    query_graph_by_keywords,
    get_graph_stats,
    get_day_nodes_async,
    query_quintuples_by_day_async,
    query_memory_nodes,
    start_task_manager,
    stop_task_manager,
    GRAGError,
)


async def example_basic_conversation() -> None:
    """示例 1：添加基础对话记忆，模拟 COSMOS 与 Aliya 在海獭号中的初次交流。

    Aliya 向 COSMOS 介绍她对深空探索的看法，
    系统从中提取人物与职业、地点、情感等五元组关系。
    """
    print("\n=== 示例 1：基础对话记忆（海獭号·深空漫谈）===")

    try:
        mgr = get_memory_manager()

        if not mgr.enabled:
            print("[WARN] 记忆系统未启用，请检查配置文件")
            return

        # ── 第一轮：COSMOS 好奇深空，Aliya 谈科研冒险 ─────────────────────
        user_input = "反正也是干等着，你不如给我讲讲你们那个时代的事情，比如外星怪兽啥的"
        ai_response = (
            "你倒是还蛮感兴趣的嘛。有的时候还是挺羡慕你们那个时代的人，普遍对星空有着"
            "说不清的情结，也产生了很多精彩的幻想作品。其实对于大部分人来说，真实的星空"
            "多少还是有些枯燥无聊的。但是对于我们科研人员而言，只要是存在未知的星球，"
            "都有着很大的探索价值。像你说的这种怪兽也是有可能存在的，所以每次前往未探明"
            "的深空时，都是兴奋且激动的，以至于能几乎忘记深空的危险。"
        )

        print(f"COSMOS: {user_input}")
        print(f"Aliya : {ai_response[:60]}...")

        success = await mgr.add_conversation_memory(
            user_input=user_input,
            ai_response=ai_response,
            session_id="haita_session_001",
            day_date="2025-07-11",
            timeline="aliya|user",
        )
        print(f"[{'OK' if success else 'FAIL'}] 第一轮对话记忆已{'添加' if success else '添加失败'}")

        # ── 第二轮：COSMOS 类比冒险者，Aliya 提到航海时代 ─────────────────
        user_input2 = "所以你们相当于冒险者吗？明明是科研人员，却做着相当危险的工作"
        ai_response2 = (
            "也可以这样说吧。有点像在大航海时代探索地球的水手们吧，所以宇航员的原意也就是"
            "星际水手。伤亡率确实很高，也就诞生了我们这种……"
        )

        print(f"\nCOSMOS: {user_input2}")
        print(f"Aliya : {ai_response2}")

        success2 = await mgr.add_conversation_memory(
            user_input=user_input2,
            ai_response=ai_response2,
            session_id="haita_session_001",
            day_date="2025-07-11",
            timeline="aliya|user",
        )
        print(f"[{'OK' if success2 else 'FAIL'}] 第二轮对话记忆已{'添加' if success2 else '添加失败'}")

        # 等待异步五元组提取完成
        print("\n等待五元组提取...")
        mgr2 = get_memory_manager()
        for _ in range(60):  # 最多等 60 秒
            await asyncio.sleep(1)
            if mgr2.get_memory_stats().get("inflight_count", 0) == 0:
                break
        else:
            print("[WARN] 等待超时，提取任务可能仍在进行中")

        # ── 查询：COSMOS 回忆 Aliya 的职业 ───────────────────────────────
        question = "Aliya 是做什么工作的？"
        print(f"\n查询: {question}")
        answer = await mgr.query_memory(question)
        print(f"回答: {answer}" if answer else "[WARN] 未从图谱检索到相关信息")

    except GRAGError as e:
        get_default_handler().handle(e)

    print("[OK] 示例 1 完成")


async def example_graph_operations() -> None:
    """示例 4：直接写入五元组，构建 Aliya 的人际关系图谱。

    将 Aliya、Kane、Ryoko 与泰瑞斯公司的关系直接存入 Neo4j，
    无需经过 LLM 提取，适合已结构化的角色背景数据。
    """
    print("\n=== 示例 4：图谱写入（泰瑞斯公司人物关系）===")

    try:
        # Aliya 的已知关系：来自游戏对话和背景设定
        character_quintuples = [
            # ── Aliya ────────────────────────────────────────────────────
            ("Aliya",     "人物", "就职于",   "泰瑞斯公司",   "组织"),
            ("Aliya",     "人物", "职业是",   "宇航员",       "职业"),
            ("Aliya",     "人物", "驾驶",     "海獭号",       "物品"),
            ("Aliya",     "人物", "前往",     "深空",         "地点"),
            ("Aliya",     "人物", "擅长",     "深空探索",     "技能"),
            # ── Kane ─────────────────────────────────────────────────────
            ("Kane",      "人物", "就职于",   "泰瑞斯公司",   "组织"),
            ("Kane",      "人物", "同事是",   "Aliya",        "人物"),
            ("Kane",      "人物", "提议",     "溜出公司玩",   "事件"),
            # ── Ryoko ────────────────────────────────────────────────────
            ("Ryoko",     "人物", "就职于",   "泰瑞斯公司",   "组织"),
            ("Ryoko",     "人物", "同事是",   "Aliya",        "人物"),
            ("Ryoko",     "人物", "同事是",   "Kane",         "人物"),
            # ── 泰瑞斯公司 ────────────────────────────────────────────────
            ("泰瑞斯公司", "组织", "拥有",    "庭院",         "地点"),
            ("泰瑞斯公司", "组织", "从事",    "深空科研",     "领域"),
        ]

        print(f"写入 {len(character_quintuples)} 条人物关系五元组...")
        for h, ht, r, t, tt in character_quintuples:
            print(f"  {h}({ht}) —[{r}]→ {t}({tt})")

        success = store_quintuples(
            character_quintuples,
            source_text="彼方的她-Aliya 角色背景：泰瑞斯公司人物关系",
            session_id="character_setup",
        )
        print(f"\n[{'OK' if success else 'FAIL'}] 五元组写入{'成功' if success else '失败'}")

        # ── 关键词查询：检索 Kane 的相关关系 ────────────────────────────
        print("\n关键词查询: ['Kane', 'Aliya', '泰瑞斯公司']")
        results = query_graph_by_keywords(["Kane", "Aliya", "泰瑞斯公司"], limit=8)

        if results:
            print(f"查询结果（{len(results)} 条）:")
            for i, (h, ht, rel, t, tt) in enumerate(results, 1):
                print(f"  {i}. {h}({ht}) —[{rel}]→ {t}({tt})")
        else:
            print("[WARN] 未查询到匹配关系")

    except GRAGError as e:
        get_default_handler().handle(e)

    print("[OK] 示例 4 完成")


async def example_task_management() -> None:
    """示例 5：并发任务提取，模拟 COSMOS 一次性收到多段 Aliya 对话的场景。

    三段对话同时提交到任务队列，由多个 worker 协程并发提取五元组，
    还原 Aliya 讲述 Kane 事迹、分享大头照、谈及归家愿望的场景。
    """
    print("\n=== 示例 5：并发任务管理（批量提取 Aliya 对话片段）===")

    try:
        await start_task_manager()
        from core.memory.task_manager import get_task_manager
        task_mgr = get_task_manager()

        # 三段来自游戏内的真实对话片段
        conversation_texts = [
            (
                "Kane 跟老实可不沾边，几乎每个月的通报批评名单上面都有他。"
                "之前还在食堂当众顶撞主管，都不给对方台阶下的。"
                "不过依然因为技术水平太好了没有受到什么实质性的惩罚。"
                "他对我们倒挺好的，属于「窝外横」。"
            ),
            (
                "这是当时我们一起从公司的「庭院」里溜出去玩拍的大头照。"
                "你绝对想不到这个计划是 Kane 提出来的，"
                "你别看他那个样子，其实相当叛逆。"
            ),
            (
                "有点像在大航海时代探索地球的水手们吧，所以宇航员的原意也就是星际水手。"
                "伤亡率确实很高。每次前往未探明的深空时，都是兴奋且激动的，"
                "以至于能几乎忘记深空的危险。"
            ),
        ]

        print(f"提交 {len(conversation_texts)} 个提取任务...")
        task_ids = []
        labels = ["Kane 的性格", "庭院逃跑事件", "深空探索感悟"]

        for label, text in zip(labels, conversation_texts):
            task_id = await task_mgr.add_task(
                text,
                source_text=text,
                session_id="haita_session_001",
                day_date="2025-07-11",
                timeline="aliya|user",
            )
            task_ids.append(task_id)
            print(f"  [{label}] 任务ID: {task_id[:16]}...")

        print("\n等待所有任务完成...")
        for task_id, label in zip(task_ids, labels):
            result, error, _categories = await task_mgr.get_task_result(task_id, timeout=30)
            if result:
                print(f"  [OK] [{label}] 提取到 {len(result)} 个五元组")
                for h, ht, r, t, tt in result[:2]:  # 只打印前两条
                    print(f"       {h}({ht}) —[{r}]→ {t}({tt})")
            else:
                print(f"  [FAIL] [{label}] 失败: {error}")

        stats = task_mgr.get_stats()
        print(f"\n任务统计: 完成 {stats['completed_tasks']} / 失败 {stats['failed_tasks']} / worker数 {stats['max_workers']}")

    except GRAGError as e:
        get_default_handler().handle(e)

    print("[OK] 示例 5 完成")


async def example_rag_query() -> None:
    """示例 6：RAG 查询，模拟 COSMOS 在后续对话中依靠记忆回忆 Aliya 说过的话。

    先向记忆系统写入多段对话，再以问题形式触发 RAG 检索链路：
    关键词提取 → 图谱查询 → LLM 生成回答。
    """
    print("\n=== 示例 6：RAG 查询（COSMOS 回忆 Aliya 讲述的事情）===")

    try:
        mgr = get_memory_manager()

        if not mgr.enabled:
            print("[WARN] 记忆系统未启用")
            return

        # 写入 Aliya 讲述 Kane 以及归家愿望的对话
        conversations = [
            (
                "Kane 对我们倒挺好的，属于「窝外横」，在公司里经常被通报批评。",
                "看来 Kane 是个很有个性的人，但对朋友很忠诚。",
            ),
            (
                "好想再一次和他们一起逃出去玩啊……",
                "等你们平安回到泰瑞斯公司，一定还有机会的。",
            ),
            (
                "借你吉言，我一定会把 Kane 带回去的。",
                "我相信你，Aliya，你们一定能平安回家。",
            ),
        ]

        print("写入 Aliya 关于 Kane 和归家愿望的对话...")
        for i, (user_msg, ai_msg) in enumerate(conversations, 1):
            await mgr.add_conversation_memory(
                user_input=user_msg,
                ai_response=ai_msg,
                session_id="haita_session_002",
                day_date="2025-07-11",
                timeline="aliya|user",
            )
            print(f"  对话 {i} 已写入")

        print("等待五元组提取完成...")
        for _ in range(60):
            await asyncio.sleep(1)
            if mgr.get_memory_stats().get("inflight_count", 0) == 0:
                break
        else:
            print("[WARN] 等待超时，提取任务可能仍在进行中")

        # COSMOS 通过 RAG 回忆 Aliya 说过的话
        questions = [
            "Aliya 怎么评价 Kane 这个人？",
            "Aliya 最想做的事情是什么？",
            "Aliya 和 Kane 是什么关系？",
        ]

        print("\n开始 RAG 查询...")
        for question in questions:
            print(f"\n  COSMOS 想起: 「{question}」")
            answer = await mgr.query_memory(question)
            print(f"  记忆回响: {answer}" if answer else "  [WARN] 未检索到相关记忆")

    except GRAGError as e:
        get_default_handler().handle(e)

    print("\n[OK] 示例 6 完成")


async def example_memory_stats() -> None:
    """示例 3：查看记忆系统当前状态，确认 Aliya 相关知识已存入图谱。"""
    print("\n=== 示例 3：记忆统计（图谱当前状态）===")

    try:
        graph_stats = get_graph_stats()
        print(f"Neo4j 连接状态: {'已连接' if graph_stats.get('neo4j_connected') else '未连接'}")
        print(f"实体节点数:     {graph_stats.get('entity_count', 0)}")
        print(f"关系数:         {graph_stats.get('relation_count', 0)}")
        print(f"Day 节点数:     {graph_stats.get('day_count', 0)}")

        type_dist = graph_stats.get("entity_type_distribution", {})
        if type_dist:
            print("实体类型分布:")
            for etype, count in sorted(type_dist.items(), key=lambda x: -x[1]):
                print(f"  {etype}: {count}")

        timeline_dist = graph_stats.get("day_timeline_distribution", {})
        if timeline_dist:
            print("时间链分布:")
            for timeline, count in timeline_dist.items():
                print(f"  {timeline}: {count} 天")

        mgr = get_memory_manager()
        mem_stats = mgr.get_memory_stats()
        print(f"\n近期对话缓存条数: {mem_stats.get('context_length', 0)}")
        print(f"提取缓存命中项:   {mem_stats.get('cache_size', 0)}")
        print(f"进行中的提取任务: {mem_stats.get('inflight_count', 0)}")

    except GRAGError as e:
        get_default_handler().handle(e)

    print("[OK] 示例 3 完成")


async def example_memory_maintenance() -> None:
    """示例 7：记忆维护，演示任务清理操作。

    清理已完成的历史提取任务，释放内存占用。
    实际清空图谱的操作已注释，避免误删 Aliya 的记忆数据。
    """
    print("\n=== 示例 7：记忆维护（清理已完成任务）===")

    try:
        mgr = get_memory_manager()

        if not mgr.enabled:
            print("[WARN] 记忆系统未启用")
            return

        stats_before = get_graph_stats()
        print(f"当前图谱状态 — 实体: {stats_before.get('entity_count', 0)}, 关系: {stats_before.get('relation_count', 0)}")

        # 清理已完成的提取任务（不影响 Neo4j 中已存储的记忆）
        from core.memory.task_manager import get_task_manager
        task_mgr = get_task_manager()
        cleaned = await task_mgr.clear_completed_tasks(max_age_hours=0)
        print(f"[OK] 清理了 {cleaned} 个已完成的提取任务")

        # ── 以下操作会清空 Aliya 全部记忆，生产环境请谨慎执行 ──────────────
        # print("[WARN] 即将清空 Aliya 的全部记忆（Neo4j Entity + Day 节点）")
        # success = await mgr.clear_memory()
        # print(f"[{'OK' if success else 'FAIL'}] 记忆{'已清空' if success else '清空失败'}")

        print("ℹ  clear_memory() 已注释，如需执行请取消注释")

    except GRAGError as e:
        get_default_handler().handle(e)

    print("[OK] 示例 7 完成")


async def example_memory_retrieval() -> None:
    """示例 8：记忆检索展示（关键词 / 时间链 / 记忆节点三种视角）。

    演示如何把 Aliya 的记忆从 Neo4j 图谱中"取回来"：
      1. 关键词图谱查询：输入实体/话题关键词，返回匹配的五元组关系与来源文本；
      2. 按时间链/日期查询：按 user / aliya 链与日期范围召回某段时间的记忆；
      3. 记忆节点查询：列出挂载了五层记忆属性（层/重要性/置信度等）的实体节点。
    """
    print("\n=== 示例 8：记忆检索展示（关键词 / 时间链 / 记忆节点）===")

    try:
        mgr = get_memory_manager()
        if not mgr.enabled:
            print("[WARN] 记忆系统未启用")
            return

        # ── 1. 关键词图谱查询（含来源文本）──────────────────────────────
        print("\n▶ 检索一：关键词图谱查询")
        print("  输入关键词: ['Aliya', 'Kane', '深空']")
        keywords = ["Aliya", "Kane", "深空"]
        try:
            # 同步接口支持 include_source 返回 6 元素元组（含来源文本）
            results = query_graph_by_keywords(keywords, limit=6, include_source=True)
            if results:
                print(f"  命中 {len(results)} 条五元组关系:")
                for i, item in enumerate(results, 1):
                    h, ht, rel, t, tt = str(item[0]), str(item[1]), str(item[2]), str(item[3]), str(item[4])
                    suffix = f"  ↳ 来源: {str(item[5])[:40]}…" if len(item) >= 6 and item[5] else ""
                    print(f"    {i}. {h}({ht}) —[{rel}]→ {t}({tt}){suffix}")
            else:
                print("  [WARN] 未命中关键词检索")
        except Exception as e:
            print(f"  [FAIL] 关键词检索失败: {e}")

        # ── 2. 按时间链/日期查询（含来源文本）────────────────────────────
        print("\n▶ 检索二：按时间链/日期查询")
        print("  范围: user 时间链 · 2025-07-01 ~ 2025-07-31")
        try:
            day_results = await query_quintuples_by_day_async(
                timeline="user",
                start_date="2025-07-01",
                end_date="2025-07-31",
                limit=6,
                include_source=True,
            )
            if day_results:
                print(f"  命中 {len(day_results)} 条关系（按日期召回）:")
                for i, item in enumerate(day_results, 1):
                    h, ht, rel, t, tt = str(item[0]), str(item[1]), str(item[2]), str(item[3]), str(item[4])
                    suffix = f"  ↳ 来源: {str(item[5])[:40]}…" if len(item) >= 6 and item[5] else ""
                    print(f"    {i}. {h}({ht}) —[{rel}]→ {t}({tt}){suffix}")
            else:
                print("  [WARN] 该时间范围内无记忆")
        except Exception as e:
            print(f"  [FAIL] 时间链查询失败: {e}")

        # ── 3. 记忆节点查询（五层记忆属性）───────────────────────────────
        print("\n▶ 检索三：记忆节点查询（五层记忆属性）")
        try:
            nodes = query_memory_nodes(limit=6)
            if nodes:
                print(f"  命中 {len(nodes)} 个挂载记忆属性的实体节点:")
                for node in nodes:
                    layers = node.get("layers") or "-"
                    heat = node.get("heat", 0.0)
                    importance = node.get("importance", 0.0)
                    print(f"    - {node['name']} [层: {layers}] 热度:{heat:.2f} 重要性:{importance:.2f}")
            else:
                print("  [WARN] 尚无实体挂载五层记忆属性（需先在对话中写入记忆并巩固）")
        except Exception as e:
            print(f"  [FAIL] 记忆节点查询失败: {e}")

        # ── 4. RAG 问答展示（最终回答）───────────────────────────────────
        print("\n▶ 检索四：RAG 问答（关键词→图谱→回答）")
        question = "Aliya 和 Kane 是什么关系？"
        print(f"  问题: {question}")
        answer = await mgr.query_memory(question)
        print(f"  回答: {answer}" if answer else "  [WARN] 未检索到相关记忆")

    except GRAGError as e:
        get_default_handler().handle(e)

    print("\n[OK] 示例 8 完成")


async def example_multi_time() -> None:
    """示例 2：多日期时间链演示（千年时空对照）。

    在多个连续日期分别写入 user 与 aliya 两条时间链的记忆，
    验证 Aliya 时间链的落库日期为正常时间 +1000 年，
    并展示两条链各自按日期串联的 Day 节点分布。
    """
    print("\n=== 示例 2：多日期时间链（千年时空对照）===")

    try:
        mgr = get_memory_manager()

        if not mgr.enabled:
            print("[WARN] 记忆系统未启用，请检查配置文件")
            return

        # 三个连续日期，模拟 COSMOS 与 Aliya 跨越数日的交流
        multi_dialogues = [
            ("2025-07-11", "今天天气真好，你那边呢？", "我这边永远是星空与寂静，不过有你陪聊也不错。"),
            ("2025-07-12", "你们深空探索一般持续多久？", "一次任务往往要以年来计，孤独是宇航员的常态。"),
            ("2025-07-13", "如果有一天能见面就好了。", "千年后也许会有那样的机会，我会等。"),
        ]

        print("写入多日期对话（user + aliya 双时间链）...")
        for day_date, user_msg, ai_msg in multi_dialogues:
            print(f"  {day_date}: COSMOS「{user_msg}」")
            await mgr.add_conversation_memory(
                user_input=user_msg,
                ai_response=ai_msg,
                session_id="multi_time_session",
                day_date=day_date,
                timeline="aliya|user",
            )

        print("\n等待五元组提取完成...")
        for _ in range(60):
            await asyncio.sleep(1)
            if mgr.get_memory_stats().get("inflight_count", 0) == 0:
                break
        else:
            print("[WARN] 等待超时，提取任务可能仍在进行中")

        # user 时间链 Day 节点（应为正常日期）
        print("\nuser 时间链 Day 节点（正常时间）:")
        user_days = await get_day_nodes_async(timeline="user")
        for d in sorted(user_days, key=lambda x: x["date"]):
            print(f"  {d['date']}  | 实体数: {d['entity_count']} | 五元组数: {d.get('quintuple_count', 0)}")

        # aliya 时间链 Day 节点（应为正常日期 +1000 年）
        print("\naliya 时间链 Day 节点（正常时间 +1000 年）:")
        aliya_days = await get_day_nodes_async(timeline="aliya")
        for d in sorted(aliya_days, key=lambda x: x["date"]):
            print(f"  {d['date']}  | 实体数: {d['entity_count']} | 五元组数: {d.get('quintuple_count', 0)}")

        # 显式对照：同一天在两条链上的落库日期差
        print("\n时间链对照（同日落库日期差）:")
        for d in sorted(user_days, key=lambda x: x["date"]):
            aliya_match = next(
                (a["date"] for a in aliya_days if a["date"] == f"{int(d['date'][:4]) + 1000}{d['date'][4:]}"),
                "?",
            )
            print(f"  user {d['date']}  ↔  aliya {aliya_match}")

    except GRAGError as e:
        get_default_handler().handle(e)

    print("[OK] 示例 2 完成")


async def main() -> None:
    """运行全部示例。

    模拟 COSMOS 与 Aliya 在受损海獭号中建立记忆联结的完整过程：
    从初次交流、人物关系建立，到跨对话的记忆检索与回溯。
    """
    print("彼方的她-Aliya · GRAG 记忆系统演示")
    print("=" * 60)
    print("场景：受损的海獭号 · 千年时空连接 · COSMOS ↔ Aliya")
    print("=" * 60)

    try:
        service_status = get_service_status()
        if not service_status.get("enabled", False):
            print("\n[FAIL] 记忆系统未启用，请检查以下配置：")
            print("  1. data/config/main.yml → cosmos.service.grag.enabled: true")
            print("  2. Neo4j 服务正在运行（docker compose up -d）")
            print("  3. cosmos.service.grag.neo4j.password 已配置")
            return

        print("\n[OK] 记忆系统已启用，开始演示...\n")

        await example_basic_conversation()    # 示例 1：基础对话记忆
        await example_multi_time()           # 示例 2：多日期时间链对照
        await example_memory_stats()         # 示例 3：图谱统计
        # await example_graph_operations()     # 示例 4：直接写入人物关系五元组
        # await example_task_management()      # 示例 5：并发批量提取
        # await example_rag_query()            # 示例 6：跨对话 RAG 检索
        # await example_memory_maintenance()   # 示例 7：任务清理
        await example_memory_retrieval()     # 示例 8：记忆检索展示（关键词/时间链/记忆节点）

    except KeyboardInterrupt:
        print("\n演示被用户中断")
    except Exception as e:
        print(f"\n[FAIL] 演示过程中发生错误: {e}")
        get_default_handler().handle(e)
    finally:
        try:
            await stop_task_manager()
            print("\n[OK] 任务管理器已停止")
        except Exception as e:
            print(f"[WARN] 停止任务管理器时出错: {e}")

    print("\n" + "=" * 60)
    print("演示完成")
    print("  - Aliya 的记忆已存入 Neo4j 知识图谱")
    print("  - 下次对话可通过 query_memory() 触发 RAG 检索")
    print("  - 使用 docker compose up -d 确保 Neo4j 服务持续运行")


if __name__ == "__main__":
    setup_logger()   # 从 data/config/main.yml 加载日志配置（含 debug 级别）
    asyncio.run(main())
