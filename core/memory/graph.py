"""Neo4j 图谱操作模块

Schema 设计（按天独立实体节点，含角色）：
  节点：
    (:Entity)  按天独立实体实例
        属性: name(str), entity_type(str), aliases(str),
              day_date(str), timeline(str),
              created_at(float), updated_at(float)
        约束: (name, day_date, timeline) 组合唯一约束
              （每天每条时间链独立节点，同一角色如 Aliya 在不同天/链是不同节点）
        索引: entity_type 索引（加速类型过滤）

    (:Day)
        属性: date(str), timeline(str), name(str), created_at(float), updated_at(float)
        约束: (date, timeline) 组合唯一约束
        说明: name = date，Neo4j Browser 以此作为节点显示 label；每条时间链上每天一个独立 Day 节点

  关系：
    (e1)-[r:PREDICATE]->(e2)，PREDICATE 为五元组谓语（如 工作于、居住在）
        两端均为按天独立实体 (:Entity)
        属性: source_text(str), session_id(str), confidence(float), day_date(str),
              timeline(str), created_at(float), updated_at(float), occurrence(int)
        说明: day_date/timeline 在关系属性上，作为按天归属键；
              同一对实体不同天 = 不同关系实例，保留按天查询能力

    (:Day)-[:ON_DAY]->(:Entity)
        Day 节点指向当天被提及的所有按天实体（每个按天实体仅 ON_DAY 归属其唯一 Day）

    (:Day)-[:NEXT_DAY]->(:Day)
        同一时间链内相邻两天的链式串联

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
import threading
import time
from typing import TYPE_CHECKING, Any, List, Optional, Tuple, TypeAlias

from core.logger import get_logger

from core.memory.config import get_grag_config
from core.memory.exceptions import (
    GraphConnectionError,
)

if TYPE_CHECKING:
    from py2neo import Graph as _GraphClass

    _GRAPH_TYPE: TypeAlias = _GraphClass
else:
    _GRAPH_TYPE: Any = None

logger = get_logger(__name__)

# 五元组类型别名：(主体, 主体类型, 谓语, 宾语, 宾语类型)
QuintupleType = Tuple[str, str, str, str, str]

# 关系类型合法性正则（白名单：中文字符、ASCII 字母数字、下划线、连字符）
# 采用白名单确保 f-string 拼入 Cypher 时不会引入语法破坏字符
# 覆盖 BMP 内的常用 CJK 范围（基本区 + 扩展 A），Strict 模式可扩展至增补平面
_REL_TYPE_PATTERN = re.compile(
    r"^[a-zA-Z0-9_\u4e00-\u9fff\u3400-\u4dbf\U00020000-\U0002A6DF-]+$"
)
# 关系类型最大长度限制（防止 LLM 生成异常长字符串）
_REL_TYPE_MAX_LEN = 64

# 五层层次化记忆属性（挂载到 Entity 节点的可选属性，前缀 memory_ 避免与现有属性冲突）
_MEMORY_ATTR_DEFAULTS = {
    "layers": "",            # 命中的记忆层（分号分隔）
    "importance": 0.0,       # 情景记忆重要性
    "confidence": 0.0,       # 语义记忆置信度
    "success_rate": 0.0,     # 程序记忆成功率
    "attention_weight": 0.0, # 工作记忆注意力权重
    "access_count": 0,       # 元记忆访问次数
    "heat": 0.0,             # 元记忆热度
}


def _to_float(value: Any) -> float:
    """把图查询返回值安全转为 float（Record/scalar/None 均可）。"""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value)) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    """把图查询返回值安全转为 int（Record/scalar/None 均可）。"""
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value)) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _build_memory_props(attrs: Optional[dict], now: float) -> dict:
    """把五层记忆属性字典转为挂载到 Entity 节点的 map（键带 memory_ 前缀）。

    仅保留非空键，配合 Cypher 的 ``SET n += $props`` 使用：无记忆信息时
    返回空 map，不向节点写入任何 memory_* 属性。
    """
    if not attrs:
        return {}
    props: dict = {}
    layers = attrs.get("layers") or ""
    if layers:
        props["memory_layers"] = layers
    for key, node_key in (
        ("importance", "memory_importance"),
        ("confidence", "memory_confidence"),
        ("success_rate", "memory_success_rate"),
        ("attention_weight", "memory_attention_weight"),
        ("heat", "memory_heat"),
    ):
        value = attrs.get(key) or 0.0
        if value > 0.0:
            props[node_key] = float(value)
    access_count = attrs.get("access_count") or 0
    if access_count > 0:
        props["memory_access_count"] = int(access_count)
    if props:
        props["memory_updated_at"] = now
    return props

# 时间链年份偏移：Aliya(aliya) 时间链相对用户正常时间向后 1000 年
# （千年时空设定的体现：COSMOS 身处当下，Aliya 在千年之后）
_TIMELINE_OFFSET_YEARS = 1000


def _shift_timeline_date(day_date: str, timeline: str) -> str:
    """按时间链语义调整日期（幂等）。

    user 时间链使用传入的正常时间；aliya 时间链在正常时间基础上
    向后偏移 _TIMELINE_OFFSET_YEARS 年。无法解析的日期原样返回。

    幂等性：若传入日期已处于 aliya 偏移时间线（年份 >= 偏移阈值，
    即调用方已预偏移），则不再重复偏移，避免二次 +1000 造成时间链重复。

    Args:
        day_date: 用户正常时间日期字符串，如 "2026-07-11"
        timeline: 时间链标识（"user" / "aliya"）

    Returns:
        调整后的日期字符串（仅 aliya 时间链发生变化）
    """
    if not day_date or timeline.lower() != "aliya":
        return day_date
    try:
        from datetime import datetime
        d = datetime.strptime(day_date, "%Y-%m-%d")
        if d.year >= _TIMELINE_OFFSET_YEARS * 3:
            # 已处于 aliya 偏移时间线（正常年份 << 3000），跳过幂等
            return day_date
        try:
            shifted = d.replace(year=d.year + _TIMELINE_OFFSET_YEARS)
        except ValueError:
            # 闰年 2/29 加 1000 年后非闰年，回退到 2-28
            shifted = d.replace(year=d.year + _TIMELINE_OFFSET_YEARS, month=2, day=28)
        return shifted.strftime("%Y-%m-%d")
    except ValueError:
        return day_date


# 尝试导入 py2neo（运行时回退；类型注解使用 TYPE_CHECKING 块中的 _GRAPH_TYPE）
try:
    from py2neo import Graph
    from py2neo.errors import ServiceUnavailable

    PY2NEO_AVAILABLE = True
except ImportError:
    # py2neo 未安装时的回退类型，必须为独立异常类而非 Exception 基类，
    # 否则 except ServiceUnavailable 会错误匹配所有非 ConnectionError 异常
    class _ServiceUnavailableStub(Exception):
        pass
    ServiceUnavailable = _ServiceUnavailableStub
    Graph = None          # type: ignore[assignment,misc]
    PY2NEO_AVAILABLE = False


class GraphStore:
    """Neo4j 图谱存储，封装连接状态、冷却重试逻辑及所有 CRUD 操作。

    每个实例持有独立的连接状态，支持在测试中用 mock 实例替换。
    """

    def __init__(self, reconnect_cooldown: float = 60.0) -> None:
        self._graph: Optional[_GRAPH_TYPE] = None
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

                graph_cls = Graph
                if graph_cls is None:
                    raise GraphConnectionError("py2neo 未安装，图谱功能已禁用")
                self._graph = graph_cls(
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
        categories: Optional[List[str]] = None,
        memory_attrs_by_entity: Optional[dict] = None,
    ) -> bool:
        """存储五元组到 Neo4j（同步）

        节点统一使用 Entity 标签，类型信息通过 entity_type 属性存储。
        通过 MERGE 保证幂等性，关系存在时累加 occurrence。
        使用 UNWIND 批量写入：每个关系类型一次 tx.run()，消除 N+1 网络往返。
        
        若提供 day_date 和 timeline，自动创建/获取 Day 节点并关联实体。
        category 写入关系的 category 属性，支持按类别查询与过滤。

        memory_attrs_by_entity: 实体名 → 五层层次化记忆属性 dict 的映射，
            命中实体的五层信息（层/重要性/置信度/成功率/注意力/访问数/热度）
            以 memory_* 属性挂载到 Entity 节点（复用现有节点体系）。
        """
        if not new_quintuples:
            return True

        # 复合时间链兜底拆分：避免 r.timeline 存成 "aliya|user" 这类组合串。
        # 每条链独立写入（各自计算 day_date 偏移），与 memory_manager 拆分语义一致。
        if timeline and "|" in timeline:
            tls = [t.strip() for t in timeline.split("|") if t.strip()]
            return all(
                self.store_quintuples(
                    new_quintuples, source_text, session_id,
                    confidence, day_date, tl, categories,
                    memory_attrs_by_entity,
                )
                for tl in tls
            )

        try:
            g = self._get_graph()
        except GraphConnectionError as e:
            logger.warning(str(e))
            return False

        if g is None:
            return False

        now = time.time()
        # aliya 时间链落库日期为正常时间 + 1000 年，关系与 Day 节点统一使用偏移后日期
        store_day_date = _shift_timeline_date(day_date, timeline) if day_date else ""
        src = source_text or ""
        success_count = 0
        cats = categories or []

        # 实体名 → 挂载到节点的 memory_* 属性 map
        memory_props_by_entity = {
            name: _build_memory_props(attrs, now)
            for name, attrs in (memory_attrs_by_entity or {}).items()
        }

        # 按关系类型分组，每组通过 UNWIND 一次性批量写入
        groups: dict[str, list] = {}
        for idx, (head, head_type, rel, tail, tail_type) in enumerate(new_quintuples):
            if not head or not tail or not rel:
                logger.warning("跳过无效五元组: %s", (head, head_type, rel, tail, tail_type))
                continue
            if not _REL_TYPE_PATTERN.match(rel):
                logger.warning("非法关系类型，跳过: %s", rel)
                continue
            if len(rel) > _REL_TYPE_MAX_LEN:
                logger.warning("关系类型过长 (%d > %d)，跳过: %s", len(rel), _REL_TYPE_MAX_LEN, rel)
                continue
            cat = cats[idx] if idx < len(cats) else ""
            # item 结构: (head, head_type, tail, tail_type, cat, head_memory_props, tail_memory_props)
            groups.setdefault(rel, []).append(
                (
                    head, head_type, tail, tail_type, cat,
                    memory_props_by_entity.get(head, {}),
                    memory_props_by_entity.get(tail, {}),
                )
            )

        if not groups:
            # 五元组全被过滤（如非法关系类型）时，仍确保该天时间链节点存在
            if store_day_date and timeline:
                self._ensure_day_node(g, store_day_date, timeline, now)
                self._link_next_day(g, store_day_date, timeline, now)
            return True

        tx = g.begin()
        try:
            for rel, items in groups.items():
                # 关系类型名通过 f-string 拼入 Cypher，因为 Neo4j 不支持参数化关系类型。
                # rel 已通过 _REL_TYPE_PATTERN 白名单校验，确保不含 Cypher 语法破坏字符。
                cypher = f"""
                UNWIND $items AS item
                MERGE (h:Entity {{name: item[0], day_date: $day_date, timeline: $timeline}})
                  ON CREATE SET h.entity_type = item[1],
                                h.aliases     = item[0],
                                h.created_at  = $now,
                                h.updated_at  = $now,
                                h            += item[5]
                  ON MATCH  SET h.updated_at  = $now,
                                h.aliases     = CASE
                                  WHEN h.aliases IS NULL OR h.aliases = ''
                                    THEN item[0]
                                  WHEN h.aliases CONTAINS item[0]
                                    THEN h.aliases
                                  ELSE h.aliases + ';' + item[0]
                                END,
                                h            += item[5]

                MERGE (t:Entity {{name: item[2], day_date: $day_date, timeline: $timeline}})
                  ON CREATE SET t.entity_type = item[3],
                                t.aliases     = item[2],
                                t.created_at  = $now,
                                t.updated_at  = $now,
                                t            += item[6]
                  ON MATCH  SET t.updated_at  = $now,
                                t.aliases     = CASE
                                  WHEN t.aliases IS NULL OR t.aliases = ''
                                    THEN item[2]
                                  WHEN t.aliases CONTAINS item[2]
                                    THEN t.aliases
                                  ELSE t.aliases + ';' + item[2]
                                END,
                                t            += item[6]

                WITH h, t, item
                MERGE (h)-[r:{rel} {{day_date: $day_date, timeline: $timeline}}]->(t)
                  ON CREATE SET r.source_text = $source_text,
                                r.session_id  = $session_id,
                                r.confidence  = $confidence,
                                r.category    = item[4],
                                r.created_at  = $now,
                                r.updated_at  = $now,
                                r.occurrence  = 1
                  ON MATCH  SET r.updated_at  = $now,
                                r.occurrence  = r.occurrence + 1,
                                r.category    = coalesce(r.category, item[4])
                """
                tx.run(
                    cypher,
                    items=items,
                    source_text=src,
                    session_id=session_id,
                    confidence=confidence,
                    now=now,
                    day_date=store_day_date,
                    timeline=timeline,
                )
                success_count += len(items)
            tx.commit()
        except Exception:
            tx.rollback()
            raise

        # 若提供了 day_date 和 timeline，将涉及实体关联到 Day 节点
        if store_day_date and timeline:
            self._link_entities_to_day(g, new_quintuples, store_day_date, timeline, now)
            # 串联时间链
            self._link_next_day(g, store_day_date, timeline, now)

        logger.info("成功存储 %d/%d 个五元组到 Neo4j", success_count, len(new_quintuples))
        return success_count > 0

    # ── Day 节点操作（时间链）──────────────────────────────────────────────

    def touch_day(self, day_date: str, timeline: str) -> bool:
        """确保某天的时间链节点存在（无五元组时也创建 Day 节点并串联）。

        对话提取结果为 0 个五元组（如闲聊被过滤、LLM 返回空数组）时，
        五元组落库会被跳过；但日期本身代表对话发生过，时间链应保持连续。
        此方法在无五元组场景下仍创建 Day 节点并串联 NEXT_DAY，防止时间链断链。

        Args:
            day_date: 用户正常时间日期（aliya 链内部自动偏移 +1000 年）
            timeline: 时间链标识，支持 "aliya|user" 复合

        Returns:
            Day 节点是否已确保存在（图谱未连接返回 False）
        """
        if not day_date or not timeline:
            return False
        # 复合时间链拆分
        if "|" in timeline:
            tls = [t.strip() for t in timeline.split("|") if t.strip()]
            return all(self.touch_day(day_date, tl) for tl in tls)
        try:
            g = self._get_graph()
        except GraphConnectionError as e:
            logger.warning(str(e))
            return False
        if g is None:
            return False

        now = time.time()
        store_day_date = _shift_timeline_date(day_date, timeline)
        try:
            self._ensure_day_node(g, store_day_date, timeline, now)
            self._link_next_day(g, store_day_date, timeline, now)
            logger.debug("确保 Day 节点与时间链: (%s/%s)", timeline, store_day_date)
            return True
        except Exception as e:
            logger.warning("确保 Day 节点失败: %s", e)
            return False

    @staticmethod
    def _ensure_day_node(g: _GRAPH_TYPE, day_date: str, timeline: str, now: float) -> None:
        """确保 Day 节点存在（幂等创建）

        name 属性设为 date 值，Neo4j Browser 优先以 name 作为节点 label 显示，
        使图谱中每个 Day 节点直接显示日期字符串。

        Args:
            g:         Neo4j 图谱连接
            day_date:  日期字符串，如 "2026-06-01"
            timeline:  时间链标识，如 "user" 或 "aliya"
            now:       当前时间戳
        """
        g.run(
            """
            MERGE (d:Day {date: $date, timeline: $timeline})
              ON CREATE SET d.name       = $date,
                            d.created_at = $now,
                            d.updated_at = $now
              ON MATCH  SET d.name       = $date,
                            d.updated_at = $now
            """,
            date=day_date,
            timeline=timeline,
            now=now,
        )

    def _link_entities_to_day(
        self,
        g: _GRAPH_TYPE,
        quintuples: List[QuintupleType],
        day_date: str,
        timeline: str,
        now: float,
    ) -> None:
        """将当天五元组涉及的按天实体关联到 Day 节点

        实体按 (name, day_date, timeline) 复合键每天独立，每个按天实体
        ON_DAY 归属其唯一 Day。索引当天五元组涉及的所有实体名，将 Day 节点
        通过 ON_DAY 连到这些按天实体（按天精确匹配，不跨天串接）。
        按天实体的 entity_type 已在 store_quintuples 中写入，无需在此重复。

        Args:
            g:          Neo4j 图谱连接
            quintuples: 已存储的五元组列表
            day_date:   日期字符串（已按时间链偏移，如 aliya +1000 年）
            timeline:   时间链标识（如 "aliya", "user"）
            now:        当前时间戳
        """
        # 确保 Day 节点存在
        self._ensure_day_node(g, day_date, timeline, now)

        # 收集当天五元组涉及的所有实体名（头/尾，去重）
        entity_names: set[str] = set()
        for head, _h_type, _rel, tail, _t_type in quintuples:
            if head:
                entity_names.add(head)
            if tail:
                entity_names.add(tail)

        if not entity_names:
            logger.debug("时间链 %s 的五元组无实体，跳过 ON_DAY 关联", timeline)
            return

        entity_list = list(entity_names)
        try:
            g.run(
                """
                UNWIND $names AS name
                MATCH (e:Entity {name: name, day_date: $date, timeline: $timeline})
                MATCH (d:Day {date: $date, timeline: $timeline})
                MERGE (d)-[:ON_DAY]->(e)
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
    def _find_neighbors(g: _GRAPH_TYPE, day_date: str, timeline: str) -> tuple[str | None, str | None]:
        """单次查询同一时间链上当前 day_date 的紧邻前/后一天

        Args:
            g:         Neo4j 图谱连接
            day_date:  当前日期字符串
            timeline:  时间链标识

        Returns:
            (prev_date, next_date) 元组，不存在时为 None
        """
        records = g.run(
            """
            // 前驱：小于当前日期的最大值
            OPTIONAL MATCH (prev:Day)
            WHERE prev.timeline = $timeline AND prev.date < $date
            WITH max(prev.date) AS prev_date

            // 后继：大于当前日期的最小值
            OPTIONAL MATCH (next:Day)
            WHERE next.timeline = $timeline AND next.date > $date
            WITH prev_date, min(next.date) AS next_date

            RETURN prev_date, next_date
            """,
            date=day_date,
            timeline=timeline,
        ).data()
        if records:
            r = records[0]
            prev_date = r.get("prev_date")
            next_date = r.get("next_date")
            return (str(prev_date) if prev_date is not None else None,
                    str(next_date) if next_date is not None else None)
        return None, None

    def _link_next_day(
        self, g: _GRAPH_TYPE, day_date: str, timeline: str, now: float
    ) -> None:
        """串联时间链：将当前天与其紧邻前/后一天用 NEXT_DAY 关系连接

        无论落库顺序如何，都保证每条时间链上相邻两天形成
        prev -[:NEXT_DAY]-> curr -[:NEXT_DAY]-> next 的链式结构。
        因为五元组提取是并发异步完成的，同一时间链不同日期可能乱序写入，
        仅做「向后连」会在中间天晚到时产生断链或冗余边。这里双向重建：
          - 重建 prev 的出边，使其唯一指向 curr（紧邻后继）；
          - 重建 curr 的出边，使其唯一指向 next（紧邻后继）。
        这样乱序落库也能保持时间链连续。

        Args:
            g:         Neo4j 图谱连接
            day_date:  当前日期字符串
            timeline:  时间链标识
            now:       当前时间戳
        """
        prev_date, next_date = self._find_neighbors(g, day_date, timeline)

        # 重建 prev 的出边：prev 必须唯一指向 curr（紧邻后继）
        if prev_date:
            try:
                g.run(
                    """
                    MATCH (prev:Day {date: $prev_date, timeline: $timeline})
                    OPTIONAL MATCH (prev)-[old:NEXT_DAY]->()
                    DELETE old
                    WITH prev
                    MATCH (curr:Day {date: $date, timeline: $timeline})
                    MERGE (prev)-[:NEXT_DAY]->(curr)
                      ON CREATE SET prev.updated_at = $now, curr.updated_at = $now
                    """,
                    prev_date=prev_date,
                    date=day_date,
                    timeline=timeline,
                    now=now,
                )
                logger.debug(
                    "时间链串联: (%s/%s) --> (%s/%s)",
                    timeline, prev_date, timeline, day_date,
                )
            except Exception as exc:
                logger.warning("时间链串联失败: %s", exc)

        # 重建 curr 的出边：curr 必须唯一指向 next（紧邻后继）
        try:
            g.run(
                """
                MATCH (curr:Day {date: $date, timeline: $timeline})
                OPTIONAL MATCH (curr)-[old:NEXT_DAY]->()
                DELETE old
                """,
                date=day_date,
                timeline=timeline,
            )
            if next_date:
                g.run(
                    """
                    MATCH (curr:Day {date: $date, timeline: $timeline})
                    MATCH (nxt:Day {date: $next_date, timeline: $timeline})
                    MERGE (curr)-[:NEXT_DAY]->(nxt)
                      ON CREATE SET curr.updated_at = $now, nxt.updated_at = $now
                    """,
                    date=day_date,
                    next_date=next_date,
                    timeline=timeline,
                    now=now,
                )
                logger.debug(
                    "时间链串联: (%s/%s) --> (%s/%s)",
                    timeline, day_date, timeline, next_date,
                )
        except Exception as exc:
            logger.warning("时间链串联失败: %s", exc)

    def query_graph_by_keywords(
        self,
        keywords: List[str],
        limit: int = 5,
        similarity_threshold: float = 0.0,
        timeline: str = "",
        include_source: bool = False,
    ) -> List[QuintupleType]:
        """根据关键词查询图谱（同步）

        使用全文索引 + name IN 精确过滤，不依赖 CONTAINS 模糊匹配。
        当 similarity_threshold > 0 时，对结果进行文本相似度过滤。
        timeline 非空时仅返回该时间链的关系。

        同一角色（如 Aliya）在不同天/链为独立实体，故按
        (head, relation, tail) 聚合跨天同名事实，仅保留最近日期的实例，
        结果按日期降序返回，避免跨天重复碎片。

        include_source 为 True 时返回 6 元素元组
        (head, head_type, rel, tail, tail_type, source_text)，
        否则返回 5 元素元组（向后兼容）。
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
                    all_names.add(str(r["name"]))
        except Exception as e:
            logger.error("全文索引合并查询失败: %s", e)

        if not all_names:
            return []

        # 单次 MATCH 查询获取所有相关关系
        try:
            records = g.run(  # type: ignore[attr-defined]
                """
                MATCH (e1:Entity)-[r]->(e2:Entity)
                WHERE (e1.name IN $names OR e2.name IN $names)
                  AND ($timeline = "" OR r.timeline = $timeline)
                RETURN e1.name AS head, e1.entity_type AS head_type,
                       type(r) AS relation,
                       e2.name AS tail, e2.entity_type AS tail_type,
                       r.day_date AS day_date,
                       r.source_text AS source_text
                ORDER BY r.day_date DESC, r.updated_at DESC
                LIMIT $limit
                """,
                names=list(all_names),
                timeline=timeline,
                limit=limit * max(len(keywords), 1),
            ).data()
        except Exception as e:
            logger.error("批次图谱查询失败: %s", e)
            return []

        # 按 (head, relation, tail) 聚合跨天同名事实，保留最近日期实例
        best: dict = {}
        for record in records:
            head = str(record["head"] or "")
            tail = str(record["tail"] or "")
            relation = str(record["relation"] or "")
            if not head or not tail or not relation:
                continue

            if similarity_threshold > 0:
                best_sim = 0.0
                for kw in keywords:
                    head_sim = difflib.SequenceMatcher(None, head.lower(), kw.lower()).ratio()
                    tail_sim = difflib.SequenceMatcher(None, tail.lower(), kw.lower()).ratio()
                    best_sim = max(best_sim, head_sim, tail_sim)
                if best_sim < similarity_threshold:
                    continue

            key = (head, relation, tail)
            day_date = record.get("day_date") or ""
            # 同名事实跨天会重复，仅保留日期最近（day_date 最大）的实例
            if key not in best or day_date > best[key][0]:
                source = record.get("source_text") or ""
                best[key] = (day_date, (
                    head,
                    record["head_type"] or "",
                    relation,
                    tail,
                    record["tail_type"] or "",
                    source,
                ))

        # 按最近日期降序返回；include_source=False 时去掉第 6 位来源文本（保持 5 元素契约）
        ordered = sorted(best.values(), key=lambda x: x[0], reverse=True)
        if include_source:
            return [v[1] for v in ordered]
        return [v[1][:5] for v in ordered]

    def query_by_category(
        self,
        category: str,
        keywords: Optional[List[str]] = None,
        limit: int = 20,
        timeline: str = "",
    ) -> List[QuintupleType]:
        """按五元组类别查询图谱（同步）

        根据 category 属性过滤 PREDICATE 关系，可结合关键词进一步缩小范围。
        category 取值见 QuintupleCategory（人际/身份/地点/事件/偏好/属性/认知/归属）。

        Args:
            category: 类别标识
            keywords: 可选关键词列表，用于过滤相关实体
            limit:    返回数量上限
            timeline: 时间链过滤

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

        valid_keywords = [kw for kw in (keywords or []) if kw]
        names: list = []

        if valid_keywords:
            lucene_query = " OR ".join(f'"{kw}"' for kw in valid_keywords)
            try:
                ft_records = g.run(  # type: ignore[attr-defined]
                    "CALL db.index.fulltext.queryNodes('entity_fulltext', $query) "
                    "YIELD node RETURN node.name AS name LIMIT $limit",
                    query=lucene_query,
                    limit=limit,
                ).data()
                names = [str(r["name"]) for r in ft_records if r.get("name")]
            except Exception as e:
                logger.error("全文索引查询失败: %s", e)

        try:
            if names:
                records = g.run(  # type: ignore[attr-defined]
                    """
                    MATCH (e1:Entity)-[r]->(e2:Entity)
                    WHERE r.category = $category
                      AND ($timeline = "" OR r.timeline = $timeline)
                      AND (e1.name IN $names OR e2.name IN $names)
                    RETURN e1.name AS head, e1.entity_type AS head_type,
                           type(r) AS relation,
                           e2.name AS tail, e2.entity_type AS tail_type,
                           r.day_date AS day_date
                    ORDER BY r.day_date DESC, r.updated_at DESC
                    LIMIT $limit
                    """,
                    category=category,
                    timeline=timeline,
                    names=names,
                    limit=limit,
                ).data()
            else:
                records = g.run(  # type: ignore[attr-defined]
                    """
                    MATCH (e1:Entity)-[r]->(e2:Entity)
                    WHERE r.category = $category
                      AND ($timeline = "" OR r.timeline = $timeline)
                    RETURN e1.name AS head, e1.entity_type AS head_type,
                           type(r) AS relation,
                           e2.name AS tail, e2.entity_type AS tail_type,
                           r.day_date AS day_date
                    ORDER BY r.day_date DESC, r.updated_at DESC
                    LIMIT $limit
                    """,
                    category=category,
                    timeline=timeline,
                    limit=limit,
                ).data()
        except Exception as e:
            logger.error("按类别查询失败: %s", e)
            return []

        # 按 (head, relation, tail) 聚合去重
        best: dict = {}
        for record in records:
            head = str(record["head"] or "")
            tail = str(record["tail"] or "")
            relation = str(record["relation"] or "")
            if not head or not tail or not relation:
                continue
            key = (head, relation, tail)
            day_date = record.get("day_date") or ""
            if key not in best or day_date > best[key][0]:
                best[key] = (day_date, (
                    head, record["head_type"] or "", relation,
                    tail, record["tail_type"] or "",
                ))

        return [v[1] for v in sorted(best.values(), key=lambda x: x[0], reverse=True)]

    async def query_by_category_async(
        self,
        category: str,
        keywords: Optional[List[str]] = None,
        limit: int = 20,
        timeline: str = "",
    ) -> List[QuintupleType]:
        """按五元组类别查询图谱（异步）"""
        return await asyncio.to_thread(
            self.query_by_category, category, keywords, limit, timeline
        )

    def query_quintuples_by_day(
        self,
        timeline: str = "",
        start_date: str = "",
        end_date: str = "",
        limit: int = 100,
        offset: int = 0,
        include_source: bool = False,
    ) -> List[QuintupleType]:
        """按时间链和日期范围查询五元组

        Args:
            timeline:   时间链标识（"user" / "aliya"），为空时查询所有时间链
            start_date: 起始日期（含），如 "2026-06-01"，为空时不限
            end_date:   截止日期（含），如 "2026-06-30"，为空时不限
            limit:      返回数量上限
            offset:     跳过条数
            include_source: 为 True 时返回 6 元素元组（含来源文本 source_text）

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
            # aliya 时间链落库日期已 +1000 年，查询同样偏移以保持一致
            if timeline.lower() == "aliya":
                if start_date:
                    start_date = _shift_timeline_date(start_date, timeline)
                if end_date:
                    end_date = _shift_timeline_date(end_date, timeline)
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
                WHERE {where_clause}
                MATCH (d)-[:ON_DAY]->(h:Entity)
                MATCH (h)-[r]->(t:Entity)
                RETURN DISTINCT h.name AS head, h.entity_type AS head_type,
                       type(r) AS relation,
                       t.name AS tail, t.entity_type AS tail_type,
                       r.source_text AS source_text,
                       r.day_date AS day_date,
                       r.created_at AS created_at
                ORDER BY day_date DESC, created_at DESC
                SKIP $offset LIMIT $limit
                """,
                **params,
            ).data()
            rows = []
            for rec in records:
                quint = (
                    str(rec["head"] or ""),
                    str(rec["head_type"] or ""),
                    str(rec["relation"] or ""),
                    str(rec["tail"] or ""),
                    str(rec["tail_type"] or ""),
                )
                if include_source:
                    quint = quint + (str(rec.get("source_text") or ""),)
                rows.append(quint)
            return rows
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
            # aliya 时间链落库日期已 +1000 年，查询同样偏移以保持一致
            if timeline.lower() == "aliya":
                if start_date:
                    start_date = _shift_timeline_date(start_date, timeline)
                if end_date:
                    end_date = _shift_timeline_date(end_date, timeline)
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
                WHERE {where_clause}
                OPTIONAL MATCH (d)-[:ON_DAY]->(e:Entity)
                WITH d, count(DISTINCT e) AS entity_count
                OPTIONAL MATCH (d)-[:ON_DAY]->(e2:Entity)-[r]->()
                WITH d, entity_count, count(DISTINCT r) AS quintuple_count
                RETURN d.date AS date, d.timeline AS timeline,
                       d.created_at AS created_at,
                       entity_count,
                       quintuple_count
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
                    "quintuple_count": r["quintuple_count"],
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
                    str(rec["head"] or ""),
                    str(rec["head_type"] or ""),
                    str(rec["relation"] or ""),
                    str(rec["tail"] or ""),
                    str(rec["tail_type"] or ""),
                )
                for rec in records
            ]
        except Exception as e:
            logger.error("获取所有五元组失败: %s", e)
            return []

    def cleanup_orphan_entities(self) -> int:
        """清理孤立 Entity 节点（无任何关系：入边/出边/[:ON_DAY]）。

        历史脏数据或写入被中断、外部 Cypher DELETE r 后未级联清理等场景
        会留下孤立 Entity 节点（图中只显示名称、无任何连线）。这些节点
        会污染检索结果且无意义，应主动清理。

        Returns:
            被清理的孤立节点数；图谱未连接时返回 0。
        """
        try:
            g = self._get_graph()
        except GraphConnectionError:
            logger.warning("Neo4j 未连接，无法清理孤立节点")
            return 0
        if g is None:
            return 0

        try:
            # 0 计数先判断是否有孤儿
            count_rec = g.run(  # type: ignore[attr-defined]
                """
                MATCH (e:Entity)
                WHERE NOT (e)--()
                RETURN count(e) AS cnt
                """
            ).data()
            orphan_count = _to_int(count_rec[0]["cnt"]) if count_rec else 0
            if orphan_count == 0:
                return 0
            g.run(  # type: ignore[attr-defined]
                "MATCH (e:Entity) WHERE NOT (e)--() DETACH DELETE e"
            )
            logger.info("已清理 %d 个孤立 Entity 节点", orphan_count)
            return orphan_count
        except Exception as e:
            logger.error("清理孤立 Entity 节点失败: %s", e)
            return 0

    async def cleanup_orphan_entities_async(self) -> int:
        """清理孤立 Entity 节点（异步）"""
        return await asyncio.to_thread(self.cleanup_orphan_entities)

    async def touch_day_async(self, day_date: str, timeline: str) -> bool:
        """确保某天的时间链节点存在（异步）"""
        return await asyncio.to_thread(self.touch_day, day_date, timeline)

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

    def delete_day(self, timeline: str, day_date: str) -> bool:
        """删除指定时间链、指定日期的独立记忆单元（单 Day 清理）。

        实体按 (name, day_date, timeline) 复合键每天独立，
        删除 Day 时一并清理其归属的按天实体副本（含角色实体）：
          1. 该 Day 归属的五元组关系实例（按关系属性 day_date/timeline 匹配）；
          2. 该 Day 归属的实体副本（DETACH 同时移除其 ON_DAY 等连线）；
        aliya 时间链的 day_date 按 +1000 年偏移处理，与落库一致。

        Args:
            timeline: 时间链标识（"user"/"aliya"），为空时删除该落库日期下所有链
            day_date: 用户正常时间日期（aliya 链内部自动偏移）

        Returns:
            是否删除成功
        """
        try:
            g = self._get_graph()
        except GraphConnectionError:
            logger.warning("Neo4j 未连接，无法删除 Day")
            return False
        if g is None:
            return False

        # aliya 时间链落库日期为正常时间 + 1000 年
        store_day = day_date
        if timeline and timeline.lower() == "aliya":
            store_day = _shift_timeline_date(day_date, timeline)

        try:
            # 1) 删除该 Day 归属的关系实例（按关系属性 day_date/timeline 匹配）
            g.run(
                """
                MATCH ()-[r]->()
                WHERE r.day_date = $store_day
                  AND ($timeline = "" OR r.timeline = $timeline)
                DELETE r
                """,
                store_day=store_day,
                timeline=timeline,
            )
            # 2) 删除该 Day 归属的按天实体副本（含角色）；DETACH 一并移除 ON_DAY 等连线
            g.run(
                """
                MATCH (e:Entity {day_date: $store_day})
                WHERE $timeline = "" OR e.timeline = $timeline
                DETACH DELETE e
                """,
                store_day=store_day,
                timeline=timeline,
            )
            # 3) 删除 Day 节点本身（实体副本已在上一步移除）
            g.run(
                """
                MATCH (d:Day {date: $store_day})
                WHERE $timeline = "" OR d.timeline = $timeline
                DETACH DELETE d
                """,
                store_day=store_day,
                timeline=timeline,
            )
            logger.info("已删除 Day 单元 (%s/%s) 及其关系实例与按天实体副本", store_day, timeline)
            return True
        except Exception as e:
            logger.error("删除 Day 单元失败: %s", e)
            return False

    # ── 节点遗忘操作（五层记忆属性）────────────────────────────────────────

    def decay_memory_nodes(
        self,
        now: float | None = None,
        short_half_life: float = 60 * 60 * 24,        # 1 天：重要性/注意力/热度
        long_half_life: float = 7 * 24 * 60 * 60,     # 7 天：置信度/成功率
    ) -> int:
        """按 Ebbinghaus 曲线批量衰减 Entity 节点上挂载的五层记忆属性。

        衰减因子 = 2^(-age/half_life)，age 取 ``memory_updated_at`` 距今时长
        （缺失时回退 ``updated_at``）。衰减后重置 ``memory_updated_at``，
        避免重复衰减同一时段（衰减基于"最后一次写入/衰减"的间隔）。

        Returns:
            已衰减的节点数；图谱未连接时返回 0。
        """
        try:
            g = self._get_graph()
        except GraphConnectionError:
            logger.debug("图谱未连接，跳过节点记忆衰减")
            return 0
        if g is None:
            return 0

        timestamp = now if now is not None else time.time()
        try:
            # 先统计待衰减节点数（避免依赖 py2neo stats 结构）
            count_rec = g.run(  # type: ignore[attr-defined]
                "MATCH (e:Entity) WHERE e.memory_layers IS NOT NULL RETURN count(e) AS cnt"
            ).data()
            count = _to_int(count_rec[0]["cnt"]) if count_rec else 0
            if count == 0:
                return 0
            # ln(2) ≈ 0.6931；2^(-age/hl) = exp(-ln(2) * age / hl)
            g.run(  # type: ignore[attr-defined]
                """
                MATCH (e:Entity)
                WHERE e.memory_layers IS NOT NULL
                WITH e,
                     ($now - coalesce(e.memory_updated_at, e.updated_at, $now)) AS age
                SET e.memory_importance      = e.memory_importance * exp(-0.6931 * age / $short_hl),
                    e.memory_attention_weight = e.memory_attention_weight * exp(-0.6931 * age / $short_hl),
                    e.memory_heat             = e.memory_heat * exp(-0.6931 * age / $short_hl),
                    e.memory_confidence       = e.memory_confidence * exp(-0.6931 * age / $long_hl),
                    e.memory_success_rate     = e.memory_success_rate * exp(-0.6931 * age / $long_hl),
                    e.memory_updated_at       = $now
                """,
                now=timestamp,
                short_hl=short_half_life,
                long_hl=long_half_life,
            )
            return count
        except Exception as e:
            logger.warning("节点记忆衰减失败: %s", e)
            return 0

    def prune_memory_nodes(
        self, threshold: float = 0.1, delete_entity: bool = False
    ) -> int:
        """清理"被遗忘"的图节点（五层记忆属性衰减后强度低于阈值）。

        节点强度取挂载属性中的最大值（importance/confidence/success_rate/
        attention_weight/heat）。默认仅移除节点的 ``memory_*`` 属性（实体节点
        作为知识图谱的一部分保留）；``delete_entity=True`` 时连实体节点一并
        DETACH DELETE（同时删除其所有关系）。

        Returns:
            被清理的节点数；图谱未连接时返回 0。
        """
        try:
            g = self._get_graph()
        except GraphConnectionError:
            logger.debug("图谱未连接，跳过被遗忘节点清理")
            return 0
        if g is None:
            return 0

        try:
            # 先选出强度低于阈值的节点（限制 1000 防止误删大批量）
            records = g.run(  # type: ignore[attr-defined]
                """
                MATCH (e:Entity)
                WHERE e.memory_layers IS NOT NULL
                WITH e,
                     CASE
                       WHEN coalesce(e.memory_heat, 0.0) >= coalesce(e.memory_importance, 0.0)
                         AND coalesce(e.memory_heat, 0.0) >= coalesce(e.memory_confidence, 0.0)
                         AND coalesce(e.memory_heat, 0.0) >= coalesce(e.memory_success_rate, 0.0)
                         AND coalesce(e.memory_heat, 0.0) >= coalesce(e.memory_attention_weight, 0.0)
                       THEN coalesce(e.memory_heat, 0.0)
                       WHEN coalesce(e.memory_importance, 0.0) >= coalesce(e.memory_confidence, 0.0)
                         AND coalesce(e.memory_importance, 0.0) >= coalesce(e.memory_success_rate, 0.0)
                         AND coalesce(e.memory_importance, 0.0) >= coalesce(e.memory_attention_weight, 0.0)
                       THEN coalesce(e.memory_importance, 0.0)
                       WHEN coalesce(e.memory_confidence, 0.0) >= coalesce(e.memory_success_rate, 0.0)
                         AND coalesce(e.memory_confidence, 0.0) >= coalesce(e.memory_attention_weight, 0.0)
                       THEN coalesce(e.memory_confidence, 0.0)
                       WHEN coalesce(e.memory_success_rate, 0.0) >= coalesce(e.memory_attention_weight, 0.0)
                       THEN coalesce(e.memory_success_rate, 0.0)
                       ELSE coalesce(e.memory_attention_weight, 0.0)
                     END AS strength
                WHERE strength < $threshold
                RETURN e.name AS name, e.day_date AS day_date, e.timeline AS timeline
                LIMIT 1000
                """,
                threshold=threshold,
            ).data()
            nodes = [
                {"name": str(r["name"]), "day_date": str(r["day_date"]), "timeline": str(r["timeline"])}
                for r in records
            ]
            if not nodes:
                return 0

            # UNWIND 批量处理，一次网络往返替代逐节点 N 次往返
            if delete_entity:
                g.run(  # type: ignore[attr-defined]
                    """
                    UNWIND $nodes AS n
                    MATCH (e:Entity {name: n.name, day_date: n.day_date, timeline: n.timeline})
                    DETACH DELETE e
                    """,
                    nodes=nodes,
                )
            else:
                g.run(  # type: ignore[attr-defined]
                    """
                    UNWIND $nodes AS n
                    MATCH (e:Entity {name: n.name, day_date: n.day_date, timeline: n.timeline})
                    REMOVE e.memory_layers,
                           e.memory_importance,
                           e.memory_confidence,
                           e.memory_success_rate,
                           e.memory_attention_weight,
                           e.memory_access_count,
                           e.memory_heat,
                           e.memory_updated_at
                    """,
                    nodes=nodes,
                )
            logger.info("已清理 %d 个被遗忘的记忆节点", len(nodes))
            return len(nodes)
        except Exception as e:
            logger.warning("被遗忘节点清理失败: %s", e)
            return 0

    def query_memory_nodes(
        self, limit: int = 20, min_strength: float = 0.0
    ) -> List[dict]:
        """按当前（衰减后）记忆强度查询挂载了五层记忆属性的 Entity 节点。

        Returns:
            ``[{"name", "layers", "importance", "confidence", ...}]`` 列表，
            按节点记忆强度降序；图谱未连接时返回空列表。
        """
        try:
            g = self._get_graph()
        except GraphConnectionError:
            logger.debug("图谱未连接，返回空记忆节点列表")
            return []
        if g is None:
            return []

        try:
            records = g.run(  # type: ignore[attr-defined]
                """
                MATCH (e:Entity)
                WHERE e.memory_layers IS NOT NULL
                  AND coalesce(e.memory_heat, 0.0) >= $min_strength
                RETURN e.name AS name, e.day_date AS day_date, e.timeline AS timeline,
                       e.memory_layers AS layers,
                       e.memory_importance AS importance,
                       e.memory_confidence AS confidence,
                       e.memory_success_rate AS success_rate,
                       e.memory_attention_weight AS attention_weight,
                       e.memory_access_count AS access_count,
                       e.memory_heat AS heat
                ORDER BY coalesce(e.memory_heat, 0.0) DESC,
                         coalesce(e.memory_importance, 0.0) DESC
                LIMIT $limit
                """,
                min_strength=min_strength,
                limit=max(limit, 1),
            ).data()
            return [
                {
                    "name": str(r.get("name") or ""),
                    "day_date": str(r.get("day_date") or ""),
                    "timeline": str(r.get("timeline") or ""),
                    "layers": str(r.get("layers") or ""),
                    "importance": _to_float(r.get("importance")),
                    "confidence": _to_float(r.get("confidence")),
                    "success_rate": _to_float(r.get("success_rate")),
                    "attention_weight": _to_float(r.get("attention_weight")),
                    "access_count": _to_int(r.get("access_count")),
                    "heat": _to_float(r.get("heat")),
                }
                for r in records
            ]
        except Exception as e:
            logger.warning("记忆节点查询失败: %s", e)
            return []

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
        categories: Optional[List[str]] = None,
        memory_attrs_by_entity: Optional[dict] = None,
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
            categories,
            memory_attrs_by_entity,
        )

    async def query_graph_by_keywords_async(
        self,
        keywords: List[str],
        limit: int = 5,
        similarity_threshold: float = 0.0,
        timeline: str = "",
        include_source: bool = False,
    ) -> List[QuintupleType]:
        """根据关键词查询图谱（异步，不阻塞事件循环）"""
        return await asyncio.to_thread(
            self.query_graph_by_keywords,
            keywords, limit, similarity_threshold, timeline, include_source,
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
        include_source: bool = False,
    ) -> List[QuintupleType]:
        return await asyncio.to_thread(
            self.query_quintuples_by_day,
            timeline, start_date, end_date, limit, offset, include_source,
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

    async def delete_day_async(self, timeline: str, day_date: str) -> bool:
        """删除指定时间链、指定日期的独立记忆单元（单 Day 清理，异步）"""
        return await asyncio.to_thread(self.delete_day, timeline, day_date)

    async def decay_memory_nodes_async(
        self,
        now: float | None = None,
        short_half_life: float = 60 * 60 * 24,
        long_half_life: float = 7 * 24 * 60 * 60,
    ) -> int:
        """批量衰减节点五层记忆属性（异步）"""
        return await asyncio.to_thread(
            self.decay_memory_nodes, now, short_half_life, long_half_life
        )

    async def prune_memory_nodes_async(
        self, threshold: float = 0.1, delete_entity: bool = False
    ) -> int:
        """清理被遗忘的记忆节点（异步）"""
        return await asyncio.to_thread(self.prune_memory_nodes, threshold, delete_entity)

    async def query_memory_nodes_async(
        self, limit: int = 20, min_strength: float = 0.0
    ) -> List[dict]:
        """查询挂载五层记忆属性的节点（异步）"""
        return await asyncio.to_thread(self.query_memory_nodes, limit, min_strength)


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


# ── 模块级公开 API（单例代理） ─────────────────────────────────────────────

def _ensure_indexes(g: _GRAPH_TYPE) -> None:
    """确保 Neo4j 索引和约束存在（首次连接时建立）"""
    if g is None:
        raise GraphConnectionError("图谱连接不可用，无法创建索引")

    # 实体按 (name, day_date, timeline) 复合唯一：每天每条时间链独立节点（含角色）
    statements = [
        "CREATE CONSTRAINT entity_day_unique IF NOT EXISTS "
        "FOR (e:Entity) REQUIRE (e.name, e.day_date, e.timeline) IS UNIQUE",
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
    memory_attrs_by_entity: Optional[dict] = None,
) -> bool:
    return get_graph_store().store_quintuples(
        new_quintuples, source_text, session_id, confidence, day_date, timeline,
        memory_attrs_by_entity=memory_attrs_by_entity,
    )


def query_graph_by_keywords(
    keywords: List[str],
    limit: int = 5,
    similarity_threshold: float = 0.0,
    timeline: str = "",
    include_source: bool = False,
) -> List[QuintupleType]:
    return get_graph_store().query_graph_by_keywords(
        keywords, limit, similarity_threshold, timeline, include_source
    )


def get_all_quintuples(limit: int = 1000, offset: int = 0) -> List[QuintupleType]:
    return get_graph_store().get_all_quintuples(limit, offset)


def clear_all_quintuples() -> bool:
    return get_graph_store().clear_all_quintuples()


def delete_day(timeline: str, day_date: str) -> bool:
    return get_graph_store().delete_day(timeline, day_date)


async def delete_day_async(timeline: str, day_date: str) -> bool:
    return await get_graph_store().delete_day_async(timeline, day_date)


def cleanup_orphan_entities() -> int:
    """清理孤立 Entity 节点（同步）"""
    return get_graph_store().cleanup_orphan_entities()

def touch_day(day_date: str, timeline: str) -> bool:
    """确保某天的时间链节点存在（同步）"""
    return get_graph_store().touch_day(day_date, timeline)

async def touch_day_async(day_date: str, timeline: str) -> bool:
    """确保某天的时间链节点存在（异步）"""
    return await get_graph_store().touch_day_async(day_date, timeline)


async def cleanup_orphan_entities_async() -> int:
    """清理孤立 Entity 节点（异步）"""
    return await get_graph_store().cleanup_orphan_entities_async()


def decay_memory_nodes(
    now: float | None = None,
    short_half_life: float = 60 * 60 * 24,
    long_half_life: float = 7 * 24 * 60 * 60,
) -> int:
    """批量衰减 Entity 节点上挂载的五层记忆属性（同步）"""
    return get_graph_store().decay_memory_nodes(now, short_half_life, long_half_life)


async def decay_memory_nodes_async(
    now: float | None = None,
    short_half_life: float = 60 * 60 * 24,
    long_half_life: float = 7 * 24 * 60 * 60,
) -> int:
    """批量衰减 Entity 节点上挂载的五层记忆属性（异步）"""
    return await get_graph_store().decay_memory_nodes_async(now, short_half_life, long_half_life)


def prune_memory_nodes(threshold: float = 0.1, delete_entity: bool = False) -> int:
    """清理被遗忘的记忆节点（同步）"""
    return get_graph_store().prune_memory_nodes(threshold, delete_entity)


async def prune_memory_nodes_async(
    threshold: float = 0.1, delete_entity: bool = False
) -> int:
    """清理被遗忘的记忆节点（异步）"""
    return await get_graph_store().prune_memory_nodes_async(threshold, delete_entity)


def query_memory_nodes(limit: int = 20, min_strength: float = 0.0) -> List[dict]:
    """查询挂载五层记忆属性的节点（同步）"""
    return get_graph_store().query_memory_nodes(limit, min_strength)


async def query_memory_nodes_async(
    limit: int = 20, min_strength: float = 0.0
) -> List[dict]:
    """查询挂载五层记忆属性的节点（异步）"""
    return await get_graph_store().query_memory_nodes_async(limit, min_strength)


def get_graph_stats() -> dict:
    return get_graph_store().get_graph_stats()


def query_quintuples_by_day(
    timeline: str = "",
    start_date: str = "",
    end_date: str = "",
    limit: int = 100,
    offset: int = 0,
    include_source: bool = False,
) -> List[QuintupleType]:
    return get_graph_store().query_quintuples_by_day(
        timeline, start_date, end_date, limit, offset, include_source
    )


async def store_quintuples_async(
    new_quintuples: List[QuintupleType],
    source_text: str = "",
    session_id: str = "",
    confidence: float = 1.0,
    day_date: str = "",
    timeline: str = "",
    categories: Optional[List[str]] = None,
    memory_attrs_by_entity: Optional[dict] = None,
) -> bool:
    return await get_graph_store().store_quintuples_async(
        new_quintuples, source_text, session_id, confidence, day_date, timeline,
        categories, memory_attrs_by_entity,
    )


async def query_graph_by_keywords_async(
    keywords: List[str],
    limit: int = 5,
    similarity_threshold: float = 0.0,
    timeline: str = "",
    include_source: bool = False,
) -> List[QuintupleType]:
    return await get_graph_store().query_graph_by_keywords_async(
        keywords, limit, similarity_threshold, timeline, include_source
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
    include_source: bool = False,
) -> List[QuintupleType]:
    return await get_graph_store().query_quintuples_by_day_async(
        timeline, start_date, end_date, limit, offset, include_source,
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


def query_by_category(
    category: str,
    keywords: Optional[List[str]] = None,
    limit: int = 20,
    timeline: str = "",
) -> List[QuintupleType]:
    """按五元组类别查询图谱（同步）；category 取值见 QuintupleCategory"""
    return get_graph_store().query_by_category(category, keywords, limit, timeline)


async def query_by_category_async(
    category: str,
    keywords: Optional[List[str]] = None,
    limit: int = 20,
    timeline: str = "",
) -> List[QuintupleType]:
    """按五元组类别查询图谱（异步）；category 取值见 QuintupleCategory"""
    return await get_graph_store().query_by_category_async(category, keywords, limit, timeline)


__all__ = [
    "GraphStore",
    "get_graph_store",
    "set_graph_store",
    "reset_graph_store",
    "store_quintuples",
    "query_graph_by_keywords",
    "query_by_category",
    "query_quintuples_by_day_async",
    "get_day_nodes_async",
    "get_all_quintuples",
    "clear_all_quintuples",
    "delete_day",
    "get_graph_stats",
    "store_quintuples_async",
    "query_graph_by_keywords_async",
    "query_by_category_async",
    "get_all_quintuples_async",
    "clear_all_quintuples_async",
    "get_graph_stats_async",
    "delete_day_async",
    "cleanup_orphan_entities",
    "cleanup_orphan_entities_async",
    "touch_day",
    "touch_day_async",
    # 节点遗忘操作
    "decay_memory_nodes",
    "decay_memory_nodes_async",
    "prune_memory_nodes",
    "prune_memory_nodes_async",
    "query_memory_nodes",
    "query_memory_nodes_async",
]
