"""Neo4j 图谱操作模块

Schema 设计（v3）：
  节点：(:Entity)
        属性: name(str), entity_type(str), aliases(str),
              created_at(float), updated_at(float)
        约束: name 唯一约束（保证实体不重复）
        索引: entity_type 索引（加速类型过滤）

  关系：(e1)-[r:REL_TYPE]->(e2)
        属性: source_text(str), session_id(str), confidence(float),
              created_at(float), updated_at(float), occurrence(int)

特性：
  - 连接失败后 60 秒冷却期，冷却结束后允许自动重试
  - 所有 DB 操作提供 async 版本（asyncio.to_thread 包装）
  - 关系 MERGE 时自动累加 occurrence 并更新 updated_at
  - aliases 字段自动累积同一实体的多种称呼（分号分隔）
  - 不再依赖 APOC 插件，节点类型以 entity_type 属性存储
  - GraphStore 类封装全部状态，支持测试隔离（set_graph_store / reset_graph_store）
"""

from __future__ import annotations

import asyncio
import difflib
import re
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

# 关系类型合法性正则（仅允许中英文、数字、下划线、连字符）
_REL_TYPE_PATTERN = re.compile(r"^[\w\u4e00-\u9fff-]+$")

# 尝试导入 py2neo
try:
    from py2neo import Graph, Node, Relationship
    from py2neo.errors import ServiceUnavailable

    PY2NEO_AVAILABLE = True
except ImportError:
    Graph = None          # type: ignore[assignment,misc]
    Node = None           # type: ignore[assignment,misc]
    Relationship = None   # type: ignore[assignment,misc]
    ServiceUnavailable = Exception  # type: ignore[assignment,misc]
    PY2NEO_AVAILABLE = False


class GraphStore:
    """Neo4j 图谱存储，封装连接状态、冷却重试逻辑及所有 CRUD 操作。

    每个实例持有独立的连接状态，支持在测试中用 mock 实例替换。
    """

    def __init__(self, reconnect_cooldown: float = 60.0) -> None:
        self._graph: Optional[object] = None
        self._connection_failed: bool = False
        self._last_failure_time: float = 0.0
        self._reconnect_cooldown: float = reconnect_cooldown

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

    def _get_graph(self) -> Optional[object]:
        """获取 Neo4j 图谱连接（延迟加载 + 冷却重试）

        连接失败后等待 self._reconnect_cooldown 秒才允许重试，
        避免每次查询都触发无效连接尝试。
        """
        # 冷却期内不重试
        if self._connection_failed:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed < self._reconnect_cooldown:
                return None
            # 冷却结束，重置状态允许重试
            logger.info("Neo4j 连接冷却结束（%.0fs），尝试重连...", elapsed)
            self._connection_failed = False
            self._graph = None

        if self._graph is not None:
            return self._graph

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

    # ── 同步操作 ──────────────────────────────────────────────────────────

    def store_quintuples(
        self,
        new_quintuples: List[QuintupleType],
        source_text: str = "",
        session_id: str = "",
        confidence: float = 1.0,
    ) -> bool:
        """存储五元组到 Neo4j（同步）

        节点统一使用 Entity 标签，类型信息通过 entity_type 属性存储。
        通过 MERGE 保证幂等性，关系存在时累加 occurrence。
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
        src = (source_text or "")[:200]
        success_count = 0

        cypher = """
        MERGE (h:Entity {name: $head})
          ON CREATE SET h.entity_type = $head_type,
                        h.aliases     = $head,
                        h.created_at  = $now,
                        h.updated_at  = $now
          ON MATCH  SET h.updated_at  = $now,
                        h.aliases     = CASE
                          WHEN h.aliases IS NULL OR h.aliases = ''
                            THEN $head
                          WHEN h.aliases CONTAINS $head
                            THEN h.aliases
                          ELSE h.aliases + ';' + $head
                        END

        MERGE (t:Entity {name: $tail})
          ON CREATE SET t.entity_type = $tail_type,
                        t.aliases     = $tail,
                        t.created_at  = $now,
                        t.updated_at  = $now
          ON MATCH  SET t.updated_at  = $now,
                        t.aliases     = CASE
                          WHEN t.aliases IS NULL OR t.aliases = ''
                            THEN $tail
                          WHEN t.aliases CONTAINS $tail
                            THEN t.aliases
                          ELSE t.aliases + ';' + $tail
                        END

        WITH h, t
        MERGE (h)-[r:RELATED]->(t)
          ON CREATE SET r.source_text = $source_text,
                        r.session_id  = $session_id,
                        r.confidence  = $confidence,
                        r.created_at  = $now,
                        r.updated_at  = $now,
                        r.occurrence  = 1,
                        r.relation_type = $rel_type
          ON MATCH  SET r.updated_at  = $now,
                        r.occurrence  = r.occurrence + 1
        """

        for head, head_type, rel, tail, tail_type in new_quintuples:
            if not head or not tail or not rel:
                logger.warning("跳过无效五元组: %s", (head, head_type, rel, tail, tail_type))
                continue

            if not _REL_TYPE_PATTERN.match(rel):
                logger.warning("非法关系类型，跳过: %s", rel)
                continue

            try:
                g.run(
                    cypher,
                    head=head,
                    head_type=head_type,
                    tail=tail,
                    tail_type=tail_type,
                    rel_type=rel,
                    source_text=src,
                    session_id=session_id,
                    confidence=confidence,
                    now=now,
                )
                success_count += 1
            except Exception as e:
                logger.error("存储五元组失败: %s-[%s]->%s: %s", head, rel, tail, e)

        logger.info("成功存储 %d/%d 个五元组到 Neo4j", success_count, len(new_quintuples))
        return success_count > 0

    def query_graph_by_keywords(
        self,
        keywords: List[str],
        limit: int = 5,
        similarity_threshold: float = 0.0,
    ) -> List[QuintupleType]:
        """根据关键词查询图谱（同步）

        对每个关键词进行全字段模糊匹配（节点 name/entity_type、关系类型）。
        当 similarity_threshold > 0 时，对结果进行文本相似度过滤。
        """
        try:
            graph = self._get_graph()
        except GraphConnectionError:
            logger.debug("图谱未连接，返回空结果")
            return []

        if graph is None:
            return []

        seen: set = set()
        results: List[QuintupleType] = []

        for kw in keywords:
            if not kw:
                continue

            query = """
            MATCH (e1:Entity)-[r]->(e2:Entity)
            WHERE e1.name CONTAINS $keyword
               OR e2.name CONTAINS $keyword
               OR e1.entity_type CONTAINS $keyword
               OR e2.entity_type CONTAINS $keyword
            RETURN e1.name AS head, e1.entity_type AS head_type,
                   type(r) AS relation,
                   e2.name AS tail, e2.entity_type AS tail_type
            ORDER BY r.occurrence DESC, r.updated_at DESC
            LIMIT $limit
            """

            try:
                records = graph.run(query, keyword=kw, limit=limit).data()  # type: ignore[attr-defined]
                for record in records:
                    head = record["head"]
                    tail = record["tail"]
                    relation = record["relation"]
                    key = (head, relation, tail)
                    if key in seen:
                        continue

                    if similarity_threshold > 0:
                        head_sim = difflib.SequenceMatcher(None, head.lower(), kw.lower()).ratio()
                        tail_sim = difflib.SequenceMatcher(None, tail.lower(), kw.lower()).ratio()
                        best_sim = max(head_sim, tail_sim)
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
            except Exception as e:
                logger.error("查询图谱失败 (关键词: %s): %s", kw, e)

        return results

    def get_all_quintuples(self) -> List[QuintupleType]:
        """获取所有五元组（同步）"""
        try:
            graph = self._get_graph()
        except GraphConnectionError:
            logger.warning("Neo4j 图谱未连接，返回空列表")
            return []

        if graph is None:
            return []

        try:
            query = """
            MATCH (e1:Entity)-[r]->(e2:Entity)
            RETURN e1.name AS head, e1.entity_type AS head_type,
                   type(r) AS relation,
                   e2.name AS tail, e2.entity_type AS tail_type
            ORDER BY r.created_at DESC
            """
            records = graph.run(query).data()  # type: ignore[attr-defined]
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
        """清空所有五元组（同步）"""
        try:
            graph = self._get_graph()
        except GraphConnectionError:
            logger.error("Neo4j 图谱未连接，无法清空")
            return False

        if graph is None:
            return False

        try:
            graph.run("MATCH (n) DETACH DELETE n")  # type: ignore[attr-defined]
            logger.info("已清空 Neo4j 图谱")
            return True
        except Exception as e:
            logger.error("清空五元组失败: %s", e)
            return False

    def get_graph_stats(self) -> dict:
        """获取图谱统计信息（同步）"""
        try:
            self._get_graph()
            connected = True
        except GraphConnectionError:
            connected = False

        stats: dict = {
            "neo4j_connected": connected,
            "neo4j_connection_failed": self._connection_failed,
            "reconnect_cooldown_remaining": self.cooldown_remaining,
            "entity_count": 0,
            "relation_count": 0,
        }

        if connected:
            graph = self._get_graph()
            if graph is None:
                return stats
            try:
                entity_count = graph.run(  # type: ignore[attr-defined]
                    "MATCH (n:Entity) RETURN count(n) AS count"
                ).data()
                rel_count = graph.run(  # type: ignore[attr-defined]
                    "MATCH ()-[r]->() RETURN count(r) AS count"
                ).data()
                stats["entity_count"] = entity_count[0]["count"] if entity_count else 0
                stats["relation_count"] = rel_count[0]["count"] if rel_count else 0

                type_counts = graph.run(  # type: ignore[attr-defined]
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
    ) -> bool:
        """存储五元组到 Neo4j（异步，不阻塞事件循环）"""
        return await asyncio.to_thread(
            self.store_quintuples,
            new_quintuples,
            source_text,
            session_id,
            confidence,
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

    async def get_all_quintuples_async(self) -> List[QuintupleType]:
        """获取所有五元组（异步）"""
        return await asyncio.to_thread(self.get_all_quintuples)

    async def get_graph_stats_async(self) -> dict:
        """获取图谱统计信息（异步）"""
        return await asyncio.to_thread(self.get_graph_stats)

    async def clear_all_quintuples_async(self) -> bool:
        """清空所有五元组（异步）"""
        return await asyncio.to_thread(self.clear_all_quintuples)


# ── 模块级单例管理 ──────────────────────────────────────────────────────────

_graph_store: Optional[GraphStore] = None


def get_graph_store() -> GraphStore:
    """获取模块级 GraphStore 单例（懒加载）"""
    global _graph_store
    if _graph_store is None:
        _graph_store = GraphStore()
    return _graph_store


def set_graph_store(store: GraphStore) -> None:
    """替换当前 GraphStore 实例（用于测试注入 mock 对象）"""
    global _graph_store
    _graph_store = store


def reset_graph_store() -> None:
    """重置 GraphStore 为全新实例（测试 teardown 使用）"""
    global _graph_store
    _graph_store = None


# ── 模块级函数（向后兼容代理） ─────────────────────────────────────────────

def _ensure_indexes(g: object) -> None:
    """确保 Neo4j 索引和约束存在（首次连接时建立）"""
    if g is None:
        raise GraphConnectionError("图谱连接不可用，无法创建索引")

    statements = [
        "CREATE CONSTRAINT entity_name_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.name IS UNIQUE",
        "CREATE INDEX entity_type_idx IF NOT EXISTS FOR (e:Entity) ON (e.entity_type)",
    ]
    for stmt in statements:
        try:
            g.run(stmt)  # type: ignore[attr-defined]
        except Exception as e:
            logger.debug("创建索引/约束时出现警告（可忽略）: %s", e)
    logger.debug("Neo4j 索引和约束已检查/创建")


def store_quintuples(
    new_quintuples: List[QuintupleType],
    source_text: str = "",
    session_id: str = "",
    confidence: float = 1.0,
) -> bool:
    return get_graph_store().store_quintuples(
        new_quintuples, source_text, session_id, confidence
    )


def query_graph_by_keywords(
    keywords: List[str],
    limit: int = 5,
    similarity_threshold: float = 0.0,
) -> List[QuintupleType]:
    return get_graph_store().query_graph_by_keywords(
        keywords, limit, similarity_threshold
    )


def get_all_quintuples() -> List[QuintupleType]:
    return get_graph_store().get_all_quintuples()


def clear_all_quintuples() -> bool:
    return get_graph_store().clear_all_quintuples()


def get_graph_stats() -> dict:
    return get_graph_store().get_graph_stats()


async def store_quintuples_async(
    new_quintuples: List[QuintupleType],
    source_text: str = "",
    session_id: str = "",
    confidence: float = 1.0,
) -> bool:
    return await get_graph_store().store_quintuples_async(
        new_quintuples, source_text, session_id, confidence
    )


async def query_graph_by_keywords_async(
    keywords: List[str],
    limit: int = 5,
    similarity_threshold: float = 0.0,
) -> List[QuintupleType]:
    return await get_graph_store().query_graph_by_keywords_async(
        keywords, limit, similarity_threshold
    )


async def get_all_quintuples_async() -> List[QuintupleType]:
    return await get_graph_store().get_all_quintuples_async()


async def get_graph_stats_async() -> dict:
    return await get_graph_store().get_graph_stats_async()


async def clear_all_quintuples_async() -> bool:
    return await get_graph_store().clear_all_quintuples_async()
