"""Neo4j 图谱操作模块

Schema 设计（v4）：
  节点：
    (:Entity)
        属性: name(str), entity_type(str), aliases(str),
              created_at(float), updated_at(float)
        约束: name 唯一约束（保证实体不重复）
        索引: entity_type 索引（加速类型过滤）

    (:Day)
        属性: date(str), timeline(str), created_at(float), updated_at(float)
        约束: (date, timeline) 组合唯一约束
        说明: 每条时间链上每天一个独立 Day 节点

  关系：
    (e1)-[r:PREDICATE]->(e2)，PREDICATE 为五元组谓语（如 工作于、居住在）
        属性: source_text(str), session_id(str), confidence(float),
              created_at(float), updated_at(float), occurrence(int)

    (:Entity)-[:ON_DAY]->(:Day)
        Entity 在某天被提及，关联到当天的 Day 节点

    (:Day)-[:NEXT_DAY]->(:Day)
        同一时间链内相邻两天的链式串联

特性：
  - 连接失败后 60 秒冷却期，冷却结束后允许自动重试
  - 所有 DB 操作提供 async 版本（asyncio.to_thread 包装）
  - 关系 MERGE 时自动累加 occurrence 并更新 updated_at
  - aliases 字段自动累积同一实体的多种称呼（分号分隔）
  - 不再依赖 APOC 插件，节点类型以 entity_type 属性存储
  - GraphStore 类封装全部状态，支持测试隔离（set_graph_store / reset_graph_store）
  - Day 节点按 (date, timeline) 组合唯一，同一天内实体/关系自动去重合并
"""

from __future__ import annotations

import asyncio
import difflib
import re
import threading
import time
from typing import List, Optional, Tuple

from core.logger import get_logger

from memory.config import get_grag_config
from memory.exceptions import (
    GraphConnectionError,
    GraphQueryError,
    GraphWriteError,
)

logger = get_logger(__name__)

# 五元组类型别名：(主体, 主体类型, 谓语, 宾语, 宾语类型)
QuintupleType = Tuple[str, str, str, str, str]

# 关系类型合法性正则（仅允许中文字符、ASCII字母数字、下划线、连字符）
# 显式白名单替代 \w，避免 Unicode 宽泛匹配绕过
_REL_TYPE_PATTERN = re.compile(
    r"^[a-zA-Z0-9_\u4e00-\u9fff\u3400-\u4dbf-]+$"
)
# 关系类型最大长度限制（防止 LLM 生成异常长字符串）
_REL_TYPE_MAX_LEN = 64

# 尝试导入 py2neo
try:
    from py2neo import Graph, Node, Relationship, Transaction
    from py2neo.errors import ServiceUnavailable

    PY2NEO_AVAILABLE = True
    _GRAPH_TYPE = Graph
    _TRANSACTION_TYPE = Transaction
except ImportError:
    Graph = None          # type: ignore[assignment,misc]
    Node = None           # type: ignore[assignment,misc]
    Relationship = None   # type: ignore[assignment,misc]
    Transaction = None    # type: ignore[assignment,misc]
    ServiceUnavailable = Exception  # type: ignore[assignment,misc]
    PY2NEO_AVAILABLE = False
    _GRAPH_TYPE = object  # type: ignore[assignment,misc]
    _TRANSACTION_TYPE = object  # type: ignore[assignment,misc]


class GraphStore:
    """Neo4j 图谱存储，封装连接状态、冷却重试逻辑及所有 CRUD 操作。

    每个实例持有独立的连接状态，支持在测试中用 mock 实例替换。
    """

    def __init__(self, reconnect_cooldown: float = 60.0) -> None:
        self._graph: Optional[object] = None
        self._connection_failed: bool = False
        self._last_failure_time: float = 0.0
        self._reconnect_cooldown: float = reconnect_cooldown
        self._connect_lock = threading.Lock()

    # ── 连接状态查询 ──────────────────────────────────────────────────────

    @property
    def connection_failed(self) -> bool:
        return self._connection_failed

    @property
    def cooldown_remaining(self) -> float:
        if not self._connection_failed:
            return 0.0
        return max(0.0, self._reconnect_cooldown - (time.monotonic() - self._last_failure_time))

    # ── 内部连接管理 ──────────────────────────────────────────────────────

    def _get_graph(self) -> Optional[_GRAPH_TYPE]:
        """获取 Neo4j 图谱连接（延迟加载 + 冷却重试）

        连接失败后等待 self._reconnect_cooldown 秒才允许重试，
        避免每次查询都触发无效连接尝试。

        使用 threading.Lock 保护连接创建临界区，防止多个
        asyncio.to_thread 并发调用时创建重复连接。
        """
        # 快速路径：连接已存在且有效
        if self._graph is not None:
            return self._graph

        with self._connect_lock:
            # 双重检查：锁内再次确认
            if self._graph is not None:
                return self._graph

            # 冷却期内不重试
            if self._connection_failed:
                elapsed = time.monotonic() - self._last_failure_time
                if elapsed < self._reconnect_cooldown:
                    return None
                # 冷却结束，重置状态允许重试
                logger.info("Neo4j 连接冷却结束（%.0fs），尝试重连...", elapsed)
                self._connection_failed = False
                self._graph = None

            if not PY2NEO_AVAILABLE:
                raise GraphConnectionError("py2neo 未安装，图谱功能已禁用")

            try:
                cfg = get_grag_config()
                if not cfg.enabled:
                    raise GraphConnectionError("GRAG 记忆系统未启用")
                if not cfg.neo4j.password:
                    raise GraphConnectionError("Neo4j 密码未配置")

                self._graph = Graph(
                    cfg.neo4j.uri,
                    auth=(cfg.neo4j.user, cfg.neo4j.password),
                    name=cfg.neo4j.database,
                    max_connections=cfg.neo4j.max_connections,
                )
                # 验证连接（触发实际握手）
                self._graph.service.kernel_version  # type: ignore[attr-defined]
                logger.info("成功连接到 Neo4j: %s", cfg.neo4j.uri)
                _ensure_indexes(self._graph)
                return self._graph

            except GraphConnectionError:
                raise
            except ServiceUnavailable:
                logger.warning("Neo4j 连接失败 (ServiceUnavailable)，进入冷却期")
                self._graph = None
                self._connection_failed = True
                self._last_failure_time = time.monotonic()
                return None
            except Exception as e:
                logger.warning("Neo4j 连接失败: %s，进入冷却期", e)
                self._graph = None
                self._connection_failed = True
                self._last_failure_time = time.monotonic()
                return None

    def dispose(self) -> None:
        """释放 Neo4j 连接资源，供应用退出前调用。

        调用后 _get_graph 会重新尝试连接（惰性重连）。
        """
        with self._connect_lock:
            if self._graph is not None:
                logger.info("释放 Neo4j 连接")
                self._graph = None
            self._connection_failed = False
            self._last_failure_time = 0.0

    # ── 同步操作 ──────────────────────────────────────────────────────────

    def store_quintuples(
        self,
        new_quintuples: List[QuintupleType],
        source_text: str = "",
        session_id: str = "",
        confidence: float = 1.0,
        day_date: str = "",
        timeline: str = "",
    ) -> bool:
        """存储五元组到 Neo4j（同步）

        节点统一使用 Entity 标签，类型信息通过 entity_type 属性存储。
        通过 MERGE 保证幂等性，关系存在时累加 occurrence。
        使用 UNWIND 批量写入：每个关系类型一次 tx.run()，消除 N+1 网络往返。
        
        若提供 day_date 和 timeline，自动创建/获取 Day 节点并关联实体。
        """
        if not new_quintuples:
            return True

        try:
            g = self._get_graph()
        except GraphConnectionError as e:
            logger.warning(str(e))
            return False

        if g is None:
            return False

        now = time.time()
        src = source_text or ""
        success_count = 0

        # 按关系类型分组，每组通过 UNWIND 一次性批量写入
        groups: dict[str, list] = {}
        for head, head_type, rel, tail, tail_type in new_quintuples:
            if not head or not tail or not rel:
                logger.warning("跳过无效五元组: %s", (head, head_type, rel, tail, tail_type))
                continue
            if not _REL_TYPE_PATTERN.match(rel):
                logger.warning("非法关系类型，跳过: %s", rel)
                continue
            if len(rel) > _REL_TYPE_MAX_LEN:
                logger.warning("关系类型过长 (%d > %d)，跳过: %s", len(rel), _REL_TYPE_MAX_LEN, rel)
                continue
            groups.setdefault(rel, []).append((head, head_type, tail, tail_type))

        if not groups:
            return True

        tx = g.begin()
        try:
            for rel, items in groups.items():
                cypher = f"""
                UNWIND $items AS item
                MERGE (h:Entity {{name: item[0]}})
                  ON CREATE SET h.entity_type = item[1],
                                h.aliases     = item[0],
                                h.created_at  = $now,
                                h.updated_at  = $now
                  ON MATCH  SET h.updated_at  = $now,
                                h.aliases     = CASE
                                  WHEN h.aliases IS NULL OR h.aliases = ''
                                    THEN item[0]
                                  WHEN h.aliases CONTAINS item[0]
                                    THEN h.aliases
                                  ELSE h.aliases + ';' + item[0]
                                END

                MERGE (t:Entity {{name: item[2]}})
                  ON CREATE SET t.entity_type = item[3],
                                t.aliases     = item[2],
                                t.created_at  = $now,
                                t.updated_at  = $now
                  ON MATCH  SET t.updated_at  = $now,
                                t.aliases     = CASE
                                  WHEN t.aliases IS NULL OR t.aliases = ''
                                    THEN item[2]
                                  WHEN t.aliases CONTAINS item[2]
                                    THEN t.aliases
                                  ELSE t.aliases + ';' + item[2]
                                END

                WITH h, t
                MERGE (h)-[r:{rel}]->(t)
                  ON CREATE SET r.source_text = $source_text,
                                r.session_id  = $session_id,
                                r.confidence  = $confidence,
                                r.created_at  = $now,
                                r.updated_at  = $now,
                                r.occurrence  = 1
                  ON MATCH  SET r.updated_at  = $now,
                                r.occurrence  = r.occurrence + 1
                """
                tx.run(
                    cypher,
                    items=items,
                    source_text=src,
                    session_id=session_id,
                    confidence=confidence,
                    now=now,
                )
                success_count += len(items)
            tx.commit()
        except Exception:
            tx.rollback()
            raise

        # 若提供了 day_date 和 timeline，将涉及实体关联到 Day 节点
        if day_date and timeline:
            self._link_entities_to_day(g, new_quintuples, day_date, timeline, now)
            # 串联时间链
            self._link_next_day(g, day_date, timeline, now)

        logger.info("成功存储 %d/%d 个五元组到 Neo4j", success_count, len(new_quintuples))
        return success_count > 0

    # ── Day 节点操作（时间链）──────────────────────────────────────────────

    @staticmethod
    def _ensure_day_node(g: object, day_date: str, timeline: str, now: float) -> None:
        """确保 Day 节点存在（幂等创建）

        Args:
            g:         Neo4j 图谱连接
            day_date:  日期字符串，如 "2026-06-01"
            timeline:  时间链标识，如 "user" 或 "aliya"
            now:       当前时间戳
        """
        g.run(
            """
            MERGE (d:Day {date: $date, timeline: $timeline})
              ON CREATE SET d.created_at = $now, d.updated_at = $now
              ON MATCH  SET d.updated_at = $now
            """,
            date=day_date,
            timeline=timeline,
            now=now,
        )

    def _link_entities_to_day(
        self,
        g: object,
        quintuples: List[QuintupleType],
        day_date: str,
        timeline: str,
        now: float,
    ) -> None:
        """将五元组中涉及的实体关联到当天的 Day 节点

        收集所有 Entity name（去重），批量建立 ON_DAY 关系。
        同一天内多次提及同一实体只保留一条 ON_DAY 关系（MERGE 幂等）。

        Args:
            g:          Neo4j 图谱连接
            quintuples: 已存储的五元组列表
            day_date:   日期字符串
            timeline:   时间链标识
            now:        当前时间戳
        """
        # 确保 Day 节点存在
        self._ensure_day_node(g, day_date, timeline, now)

        # 收集所有涉及的 Entity 名称（去重）
        entity_names: set[str] = set()
        for head, _ht, _rel, tail, _tt in quintuples:
            if head:
                entity_names.add(head)
            if tail:
                entity_names.add(tail)

        if not entity_names:
            return

        entity_list = list(entity_names)
        try:
            g.run(
                """
                UNWIND $names AS name
                MATCH (e:Entity {name: name})
                MATCH (d:Day {date: $date, timeline: $timeline})
                MERGE (e)-[:ON_DAY]->(d)
                """,
                names=entity_list,
                date=day_date,
                timeline=timeline,
            )
            logger.debug(
                "已将 %d 个实体关联到 Day 节点 (%s/%s)",
                len(entity_list), timeline, day_date,
            )
        except Exception as exc:
            logger.warning("实体关联 Day 节点失败: %s", exc)

    @staticmethod
    def _find_prev_day(g: object, day_date: str, timeline: str) -> str | None:
        """查找同一时间链上当前 day_date 之前最近的那天

        Args:
            g:         Neo4j 图谱连接
            day_date:  当前日期字符串，如 "2026-06-02"
            timeline:  时间链标识

        Returns:
            前一天日期字符串，没有则返回 None
        """
        records = g.run(
            """
            MATCH (d:Day)
            WHERE d.timeline = $timeline AND d.date < $date
            RETURN d.date AS prev_date
            ORDER BY d.date DESC
            LIMIT 1
            """,
            date=day_date,
            timeline=timeline,
        ).data()
        if records:
            return records[0].get("prev_date")
        return None

    def _link_next_day(
        self, g: object, day_date: str, timeline: str, now: float
    ) -> None:
        """串联时间链：将前一天与当前天用 NEXT_DAY 关系连接

        查找同时间链上前一天，建立 (prev_day)-[:NEXT_DAY]->(current_day)。
        如果 NEXT_DAY 关系已存在则跳过（幂等）。

        Args:
            g:         Neo4j 图谱连接
            day_date:  当前日期字符串
            timeline:  时间链标识
            now:       当前时间戳
        """
        prev_date = self._find_prev_day(g, day_date, timeline)
        if not prev_date:
            return

        try:
            g.run(
                """
                MATCH (prev:Day {date: $prev_date, timeline: $timeline})
                MATCH (curr:Day {date: $date, timeline: $timeline})
                MERGE (prev)-[:NEXT_DAY]->(curr)
                  ON CREATE SET prev.updated_at = $now, curr.updated_at = $now
                """,
                prev_date=prev_date,
                date=day_date,
                timeline=timeline,
                now=now,
            )
            logger.debug("时间链串联: (%s/%s) --> (%s/%s)", timeline, prev_date, timeline, day_date)
        except Exception as exc:
            logger.warning("时间链串联失败: %s", exc)

    def query_graph_by_keywords(
        self,
        keywords: List[str],
        limit: int = 5,
        similarity_threshold: float = 0.0,
    ) -> List[QuintupleType]:
        """根据关键词查询图谱（同步）

        使用全文索引 + name IN 精确过滤，不依赖 CONTAINS 模糊匹配。
        当 similarity_threshold > 0 时，对结果进行文本相似度过滤。
        优化：先批量收集所有关键词匹配的实体名，再做一次 MATCH 查询。
        """
        try:
            g = self._get_graph()
        except GraphConnectionError:
            logger.debug("图谱未连接，返回空结果")
            return []

        if g is None:
            return []

        # 合并所有关键词为单次全文索引查询（减少网络往返 N→1）
        all_names: set[str] = set()
        valid_keywords = [kw for kw in keywords if kw]
        if not valid_keywords:
            return []

        # 构建 Lucene OR 查询（每个关键词用引号包裹以做精确匹配）
        lucene_query = " OR ".join(f'"{kw}"' for kw in valid_keywords)
        try:
            ft_records = g.run(  # type: ignore[attr-defined]
                "CALL db.index.fulltext.queryNodes('entity_fulltext', $query) "
                "YIELD node RETURN node.name AS name LIMIT $limit",
                query=lucene_query,
                limit=limit * len(valid_keywords),
            ).data()
            for r in ft_records:
                if r.get("name"):
                    all_names.add(r["name"])
        except Exception as e:
            logger.error("全文索引合并查询失败: %s", e)

        if not all_names:
            return []

        # 单次 MATCH 查询获取所有相关关系
        try:
            records = g.run(  # type: ignore[attr-defined]
                """
                MATCH (e1:Entity)-[r]->(e2:Entity)
                WHERE e1.name IN $names OR e2.name IN $names
                RETURN e1.name AS head, e1.entity_type AS head_type,
                       type(r) AS relation,
                       e2.name AS tail, e2.entity_type AS tail_type
                ORDER BY r.occurrence DESC, r.updated_at DESC
                LIMIT $limit
                """,
                names=list(all_names),
                limit=limit * max(len(keywords), 1),
            ).data()
        except Exception as e:
            logger.error("批次图谱查询失败: %s", e)
            return []

        # 去重 + 相似度过滤
        seen: set = set()
        results: List[QuintupleType] = []

        for record in records:
            head = record["head"]
            tail = record["tail"]
            relation = record["relation"]
            if not head or not tail or not relation:
                continue

            key = (head, relation, tail)
            if key in seen:
                continue

            if similarity_threshold > 0:
                best_sim = 0.0
                for kw in keywords:
                    head_sim = difflib.SequenceMatcher(None, head.lower(), kw.lower()).ratio()
                    tail_sim = difflib.SequenceMatcher(None, tail.lower(), kw.lower()).ratio()
                    best_sim = max(best_sim, head_sim, tail_sim)
                if best_sim < similarity_threshold:
                    continue

            seen.add(key)
            results.append(
                (
                    head,
                    record["head_type"] or "",
                    relation,
                    tail,
                    record["tail_type"] or "",
                )
            )

        return results

    def query_quintuples_by_day(
        self,
        timeline: str = "",
        start_date: str = "",
        end_date: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> List[QuintupleType]:
        """按时间链和日期范围查询五元组

        Args:
            timeline:   时间链标识（"user" / "aliya"），为空时查询所有时间链
            start_date: 起始日期（含），如 "2026-06-01"，为空时不限
            end_date:   截止日期（含），如 "2026-06-30"，为空时不限
            limit:      返回数量上限
            offset:     跳过条数

        Returns:
            五元组列表
        """
        try:
            g = self._get_graph()
        except GraphConnectionError:
            logger.debug("图谱未连接，返回空结果")
            return []

        if g is None:
            return []

        filters: list[str] = []
        params: dict = {"limit": max(limit, 1), "offset": max(offset, 0)}

        if timeline:
            filters.append("d.timeline = $timeline")
            params["timeline"] = timeline
        if start_date:
            filters.append("d.date >= $start_date")
            params["start_date"] = start_date
        if end_date:
            filters.append("d.date <= $end_date")
            params["end_date"] = end_date

        where_clause = " AND ".join(filters) if filters else "TRUE"

        try:
            records = g.run(
                f"""
                MATCH (d:Day)<-[:ON_DAY]-(e1:Entity)-[r]->(e2:Entity)
                WHERE {where_clause}
                RETURN DISTINCT e1.name AS head, e1.entity_type AS head_type,
                       type(r) AS relation,
                       e2.name AS tail, e2.entity_type AS tail_type,
                       d.date AS day_date, d.timeline AS timeline
                ORDER BY d.date DESC, r.created_at DESC
                SKIP $offset LIMIT $limit
                """,
                **params,
            ).data()
            return [
                (
                    rec["head"],
                    rec["head_type"] or "",
                    rec["relation"],
                    rec["tail"],
                    rec["tail_type"] or "",
                )
                for rec in records
            ]
        except Exception as e:
            logger.error("按日期查询五元组失败: %s", e)
            return []

    def get_day_nodes(
        self,
        timeline: str = "",
        start_date: str = "",
        end_date: str = "",
        limit: int = 100,
    ) -> List[dict]:
        """获取 Day 节点列表

        Args:
            timeline:   时间链标识，为空时查询所有
            start_date: 起始日期（含），为空时不限
            end_date:   截止日期（含），为空时不限
            limit:      返回数量上限

        Returns:
            [{"date": str, "timeline": str, "entity_count": int, ...}]
        """
        try:
            g = self._get_graph()
        except GraphConnectionError:
            return []

        if g is None:
            return []

        filters: list[str] = []
        params: dict = {"limit": max(limit, 1)}

        if timeline:
            filters.append("d.timeline = $timeline")
            params["timeline"] = timeline
        if start_date:
            filters.append("d.date >= $start_date")
            params["start_date"] = start_date
        if end_date:
            filters.append("d.date <= $end_date")
            params["end_date"] = end_date

        where_clause = " AND ".join(filters) if filters else "TRUE"

        try:
            records = g.run(
                f"""
                MATCH (d:Day)
                OPTIONAL MATCH (e:Entity)-[:ON_DAY]->(d)
                WHERE {where_clause}
                RETURN d.date AS date, d.timeline AS timeline,
                       d.created_at AS created_at,
                       count(DISTINCT e) AS entity_count
                ORDER BY d.date DESC
                LIMIT $limit
                """,
                **params,
            ).data()
            return [
                {
                    "date": r["date"],
                    "timeline": r["timeline"],
                    "created_at": r["created_at"],
                    "entity_count": r["entity_count"],
                }
                for r in records
            ]
        except Exception as e:
            logger.error("获取 Day 节点列表失败: %s", e)
            return []

    def get_all_quintuples(self, limit: int = 1000, offset: int = 0) -> List[QuintupleType]:
        """获取所有五元组（同步，支持分页）

        Args:
            limit:  返回数量上限，设为 0 表示不限制
            offset: 跳过条数
        """
        try:
            graph = self._get_graph()
        except GraphConnectionError:
            logger.warning("Neo4j 图谱未连接，返回空列表")
            return []

        if graph is None:
            return []

        try:
            if limit > 0:
                query = """
                MATCH (e1:Entity)-[r]->(e2:Entity)
                RETURN e1.name AS head, e1.entity_type AS head_type,
                       type(r) AS relation,
                       e2.name AS tail, e2.entity_type AS tail_type
                ORDER BY r.created_at DESC
                SKIP $offset LIMIT $limit
                """
            else:
                query = """
                MATCH (e1:Entity)-[r]->(e2:Entity)
                RETURN e1.name AS head, e1.entity_type AS head_type,
                       type(r) AS relation,
                       e2.name AS tail, e2.entity_type AS tail_type
                ORDER BY r.created_at DESC
                """
            records = graph.run(query, limit=limit or 1000, offset=offset).data()  # type: ignore[attr-defined]
            return [
                (
                    rec["head"],
                    rec["head_type"] or "",
                    rec["relation"],
                    rec["tail"],
                    rec["tail_type"] or "",
                )
                for rec in records
            ]
        except Exception as e:
            logger.error("获取所有五元组失败: %s", e)
            return []

    def clear_all_quintuples(self) -> bool:
        """清空所有五元组（同步，同时清除 Day 节点）"""
        try:
            graph = self._get_graph()
        except GraphConnectionError:
            logger.error("Neo4j 图谱未连接，无法清空")
            return False

        if graph is None:
            return False

        try:
            graph.run("MATCH (n:Entity) DETACH DELETE n")  # type: ignore[attr-defined]
            graph.run("MATCH (d:Day) DETACH DELETE d")     # type: ignore[attr-defined]
            logger.info("已清空 Neo4j 图谱（Entity + Day 节点）")
            return True
        except Exception as e:
            logger.error("清空五元组失败: %s", e)
            return False

    def get_graph_stats(self) -> dict:
        """获取图谱统计信息（同步，含 Day 节点统计）"""
        try:
            g = self._get_graph()
            connected = g is not None
        except GraphConnectionError:
            connected = False
            g = None

        stats: dict = {
            "neo4j_connected": connected,
            "neo4j_connection_failed": self._connection_failed,
            "reconnect_cooldown_remaining": self.cooldown_remaining,
            "entity_count": 0,
            "relation_count": 0,
            "day_count": 0,
        }

        if g is not None:
            try:
                entity_count = g.run(  # type: ignore[attr-defined]
                    "MATCH (n:Entity) RETURN count(n) AS count"
                ).data()
                rel_count = g.run(  # type: ignore[attr-defined]
                    "MATCH ()-[r]->() RETURN count(r) AS count"
                ).data()
                day_count = g.run(  # type: ignore[attr-defined]
                    "MATCH (d:Day) RETURN count(d) AS count"
                ).data()
                stats["entity_count"] = entity_count[0]["count"] if entity_count else 0
                stats["relation_count"] = rel_count[0]["count"] if rel_count else 0
                stats["day_count"] = day_count[0]["count"] if day_count else 0

                type_counts = g.run(  # type: ignore[attr-defined]
                    """
                    MATCH (e:Entity)
                    RETURN e.entity_type AS etype, count(e) AS cnt
                    """
                ).data()
                type_summary: dict = {}
                for row in type_counts:
                    etype = row["etype"] or "Unknown"
                    type_summary[etype] = row["cnt"]
                stats["entity_type_distribution"] = type_summary

                # 时间链统计
                timeline_counts = g.run(  # type: ignore[attr-defined]
                    """
                    MATCH (d:Day)
                    RETURN d.timeline AS timeline, count(d) AS cnt
                    ORDER BY d.timeline
                    """
                ).data()
                timeline_summary: dict = {}
                for row in timeline_counts:
                    tl = row["timeline"] or "Unknown"
                    timeline_summary[tl] = row["cnt"]
                stats["day_timeline_distribution"] = timeline_summary

            except Exception:
                pass

        return stats

    # ── 异步接口 ──────────────────────────────────────────────────────────

    async def store_quintuples_async(
        self,
        new_quintuples: List[QuintupleType],
        source_text: str = "",
        session_id: str = "",
        confidence: float = 1.0,
        day_date: str = "",
        timeline: str = "",
    ) -> bool:
        """存储五元组到 Neo4j（异步，不阻塞事件循环）"""
        return await asyncio.to_thread(
            self.store_quintuples,
            new_quintuples,
            source_text,
            session_id,
            confidence,
            day_date,
            timeline,
        )

    async def query_graph_by_keywords_async(
        self,
        keywords: List[str],
        limit: int = 5,
        similarity_threshold: float = 0.0,
    ) -> List[QuintupleType]:
        """根据关键词查询图谱（异步，不阻塞事件循环）"""
        return await asyncio.to_thread(
            self.query_graph_by_keywords, keywords, limit, similarity_threshold
        )

    async def get_all_quintuples_async(
        self, limit: int = 1000, offset: int = 0
    ) -> List[QuintupleType]:
        """获取所有五元组（异步，支持分页）"""
        return await asyncio.to_thread(self.get_all_quintuples, limit, offset)

    async def get_graph_stats_async(self) -> dict:
        """获取图谱统计信息（异步）"""
        return await asyncio.to_thread(self.get_graph_stats)

    async def query_quintuples_by_day_async(
        self,
        timeline: str = "",
        start_date: str = "",
        end_date: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> List[QuintupleType]:
        return await asyncio.to_thread(
            self.query_quintuples_by_day,
            timeline, start_date, end_date, limit, offset,
        )

    async def get_day_nodes_async(
        self,
        timeline: str = "",
        start_date: str = "",
        end_date: str = "",
        limit: int = 100,
    ) -> List[dict]:
        return await asyncio.to_thread(
            self.get_day_nodes,
            timeline, start_date, end_date, limit,
        )

    async def clear_all_quintuples_async(self) -> bool:
        """清空所有五元组（异步）"""
        return await asyncio.to_thread(self.clear_all_quintuples)


# ── 模块级单例管理 ──────────────────────────────────────────────────────────

_graph_store: Optional[GraphStore] = None
_graph_store_lock = threading.Lock()


def get_graph_store() -> GraphStore:
    """获取模块级 GraphStore 单例（线程安全懒加载）"""
    global _graph_store
    if _graph_store is None:
        with _graph_store_lock:
            if _graph_store is None:
                _graph_store = GraphStore()
    return _graph_store


def set_graph_store(store: GraphStore) -> None:
    """替换当前 GraphStore 实例（用于测试注入 mock 对象）"""
    global _graph_store
    with _graph_store_lock:
        _graph_store = store


def reset_graph_store() -> None:
    """重置 GraphStore 为全新实例（测试 teardown 使用）"""
    global _graph_store
    with _graph_store_lock:
        _graph_store = None


# ── 模块级函数（向后兼容代理） ─────────────────────────────────────────────

def _ensure_indexes(g: object) -> None:
    """确保 Neo4j 索引和约束存在（首次连接时建立）"""
    if g is None:
        raise GraphConnectionError("图谱连接不可用，无法创建索引")

    statements = [
        "CREATE CONSTRAINT entity_name_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.name IS UNIQUE",
        "CREATE INDEX entity_type_idx IF NOT EXISTS FOR (e:Entity) ON (e.entity_type)",
        # Day 节点唯一约束：(date, timeline) 组合
        "CREATE CONSTRAINT day_date_timeline_unique IF NOT EXISTS FOR (d:Day) REQUIRE (d.date, d.timeline) IS UNIQUE",
        # Day 节点复合索引加速范围查询
        "CREATE INDEX day_date_timeline_idx IF NOT EXISTS FOR (d:Day) ON (d.date, d.timeline)",
    ]
    for stmt in statements:
        try:
            g.run(stmt)  # type: ignore[attr-defined]
        except Exception as e:
            logger.debug("创建索引/约束时出现警告（可忽略）: %s", e)

    # 全文索引（可选，用于关键词查询加速；低版本 Neo4j 静默跳过）
    try:
        g.run(  # type: ignore[attr-defined]
            "CREATE FULLTEXT INDEX entity_fulltext IF NOT EXISTS "
            "FOR (e:Entity) ON EACH [e.name, e.entity_type]"
        )
    except Exception as e:
        logger.debug("创建全文索引时出现警告（可忽略）: %s", e)

    logger.debug("Neo4j 索引和约束已检查/创建")


def store_quintuples(
    new_quintuples: List[QuintupleType],
    source_text: str = "",
    session_id: str = "",
    confidence: float = 1.0,
    day_date: str = "",
    timeline: str = "",
) -> bool:
    return get_graph_store().store_quintuples(
        new_quintuples, source_text, session_id, confidence, day_date, timeline
    )


def query_graph_by_keywords(
    keywords: List[str],
    limit: int = 5,
    similarity_threshold: float = 0.0,
) -> List[QuintupleType]:
    return get_graph_store().query_graph_by_keywords(
        keywords, limit, similarity_threshold
    )


def get_all_quintuples(limit: int = 1000, offset: int = 0) -> List[QuintupleType]:
    return get_graph_store().get_all_quintuples(limit, offset)


def clear_all_quintuples() -> bool:
    return get_graph_store().clear_all_quintuples()


def get_graph_stats() -> dict:
    return get_graph_store().get_graph_stats()


async def store_quintuples_async(
    new_quintuples: List[QuintupleType],
    source_text: str = "",
    session_id: str = "",
    confidence: float = 1.0,
    day_date: str = "",
    timeline: str = "",
) -> bool:
    return await get_graph_store().store_quintuples_async(
        new_quintuples, source_text, session_id, confidence, day_date, timeline
    )


async def query_graph_by_keywords_async(
    keywords: List[str],
    limit: int = 5,
    similarity_threshold: float = 0.0,
) -> List[QuintupleType]:
    return await get_graph_store().query_graph_by_keywords_async(
        keywords, limit, similarity_threshold
    )


async def get_all_quintuples_async(
    limit: int = 1000, offset: int = 0,
) -> List[QuintupleType]:
    return await get_graph_store().get_all_quintuples_async(limit, offset)


async def get_graph_stats_async() -> dict:
    return await get_graph_store().get_graph_stats_async()


async def query_quintuples_by_day_async(
    timeline: str = "",
    start_date: str = "",
    end_date: str = "",
    limit: int = 100,
    offset: int = 0,
) -> List[QuintupleType]:
    return await get_graph_store().query_quintuples_by_day_async(
        timeline, start_date, end_date, limit, offset,
    )


async def get_day_nodes_async(
    timeline: str = "",
    start_date: str = "",
    end_date: str = "",
    limit: int = 100,
) -> List[dict]:
    return await get_graph_store().get_day_nodes_async(
        timeline, start_date, end_date, limit,
    )


async def clear_all_quintuples_async() -> bool:
    return await get_graph_store().clear_all_quintuples_async()


__all__ = [
    "GraphStore",
    "get_graph_store",
    "set_graph_store",
    "reset_graph_store",
    "store_quintuples",
    "query_graph_by_keywords",
    "query_quintuples_by_day_async",
    "get_day_nodes_async",
    "get_all_quintuples",
    "clear_all_quintuples",
    "get_graph_stats",
    "store_quintuples_async",
    "query_graph_by_keywords_async",
    "get_all_quintuples_async",
    "clear_all_quintuples_async",
    "get_graph_stats_async",
]
