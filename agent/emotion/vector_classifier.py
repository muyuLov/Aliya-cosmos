"""EmbeddingMessageClassifier — 向量情绪分类器（集成 core.vector）

用语义向量将用户消息分类为 VAD 情绪：
- 情绪语料从 data/emotion_corpus.json 加载（每类情绪若干样本），首次使用时批量向量化入库（幂等）
- 精确命中（规范化文本命中语料）不调用 Embedding API，零延迟
- Top-K 最近邻按情绪投票（相似度加权），naturalVAD 取邻居加权均值
- 查询 LRU 缓存，重复消息不重复向量化
- 向量模块未配置 / 未初始化 / API 异常 / 无有效邻居时，返回 neutral 兜底意图

情绪语料向量存于进程级共享的内存 VectorStore（与记忆系统共享的向量库隔离），
一次预热全部分类器受益；进程退出后清空，每次启动时按需重新入库。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from core.logger import get_logger
from core.vector.exceptions import VectorError
from agent.emotion.vad import EmotionIntent, emotionVADPresets

logger = get_logger(__name__)

# ── 情绪语料加载（data/emotion_corpus.json） ────────────────────────────────

# 语料文件路径：agent/emotion/vector_classifier.py → 项目根 data/emotion_corpus.json
_CORPUS_PATH = Path(__file__).parent.parent.parent / "data" / "emotion_corpus.json"

# 语料元数据标注：(variant, intensity)
# data/emotion_corpus.json 每条样本仅含文本与情绪名，variant / intensity 在此统一标注。
_EMOTION_META: dict[str, tuple[str, float]] = {
    "neutral": ("neutral_ack", 0.35),
    "calm": ("neutral_ack", 0.4),
    "happy": ("bright_smile", 0.8),
    "excited": ("sparkle", 0.85),
    "shy": ("bashful", 0.7),
    "affectionate": ("warm", 0.75),
    "curious": ("tilt", 0.65),
    "confused": ("confused", 0.6),
    "tired": ("drained", 0.7),
    "sad": ("downcast", 0.75),
    "anxiety": ("nervous", 0.8),
    "anger": ("annoyed", 0.75),
    "concerned": ("soft_concern", 0.65),
    "surprised": ("startled", 0.75),
}

# 每类情绪默认最多向量化入库的样本数：语料覆盖与初始化成本的权衡。
# 全量语料仍在 data/emotion_corpus.json 中，调大该值可提升近邻检索覆盖（代价是入库耗时）。
_MAX_SAMPLES_PER_EMOTION = 30


def _load_corpus_examples(max_samples: int = _MAX_SAMPLES_PER_EMOTION) -> list[tuple[str, str, str, float, list[str]]]:
    """从 data/emotion_corpus.json 加载情绪语料。

    转换为 (text, emotion, variant, intensity, contextTags) 元组列表；
    max_samples: 每类情绪最多取多少条（0=全量）；
    文件缺失 / 解析失败 / 语料为空时返回空列表（分类走 neutral 兜底），并记录警告。
    """
    try:
        data = json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning(
            "[EmotionVector] 读取情绪语料失败（%s），情绪分类将走 neutral 兜底: %s",
            _CORPUS_PATH, exc,
        )
        return []
    limit = max_samples if max_samples and max_samples > 0 else None
    examples: list[tuple[str, str, str, float, list[str]]] = []
    for emotion, texts in data.get("emotions", {}).items():
        if emotion not in emotionVADPresets:
            logger.warning("[EmotionVector] 语料含未知情绪 %r，已跳过", emotion)
            continue
        variant, intensity = _EMOTION_META.get(emotion, ("", 0.7))
        for text in texts[:limit]:
            if isinstance(text, str) and text.strip():
                examples.append((text.strip(), emotion, variant, intensity, []))
    if not examples:
        logger.warning("[EmotionVector] 情绪语料为空，情绪分类将走 neutral 兜底")
    return examples


# 默认语料（模块级缓存，避免每次实例化重复读盘）
_DEFAULT_EXAMPLES = _load_corpus_examples()

# ── 进程级共享情绪向量库 ────────────────────────────────────────────────────
# 情绪语料向量库为进程级单例：所有分类器实例共享，一次预热全部受益；
# 仍与记忆系统向量库隔离，重启清空、按需重新入库。

_shared_store: Any | None = None
_shared_store_lock = threading.Lock()
_INIT_LOCK = asyncio.Lock()  # 跨分类器实例互斥入库（预热与首次分类并发触发时只入库一次）


def _get_shared_store() -> Any | None:
    """获取进程级共享情绪向量库（懒加载）。向量未启用 / 配置不可用时返回 None。

    每次调用检查全局向量配置：配置禁用时即使已创建也返回 None（对应 neutral 兜底），
    配合 core.vector 的配置变更监听，热重载后自动失效。
    """
    global _shared_store
    try:
        from core.vector.config import VectorConfig, get_vector_config
        from core.vector.embedding import EmbeddingFactory
        from core.vector.store import VectorStore

        config = get_vector_config()
        if not config.enabled:
            logger.debug("[EmotionVector] 向量模块未启用，neutral 兜底")
            return None
        if _shared_store is None:
            with _shared_store_lock:
                if _shared_store is None:
                    # 语义分类专用配置：检索参数由 classify 显式传参，此处仅用于构造
                    store_config = VectorConfig(
                        enabled=True,
                        similarity_threshold=0.65,
                        top_k=5,
                        embedding=config.embedding,
                    )
                    _shared_store = VectorStore(
                        embedding=EmbeddingFactory.create(store_config),
                        config=store_config,
                    )
        return _shared_store
    except VectorError as exc:
        logger.debug("[EmotionVector] 向量配置不可用，neutral 兜底: %s", exc)
    except Exception as exc:  # 防御：任何初始化异常都不应阻塞对话
        logger.warning("[EmotionVector] 初始化异常，neutral 兜底: %s", exc)
    return None


def _normalize(text: str) -> str:
    """规范化匹配键：全角转半角、小写、去空白与标点。"""
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:
            code = 0x20
        elif 0xFF01 <= code <= 0xFF5E:
            code -= 0xFEE0
        ch = chr(code).lower()
        if ch.isalnum():  # 含中英文与数字（isalnum 对 CJK 返回 True）
            out.append(ch)
    return "".join(out)


def _stable_id(text: str) -> str:
    """语料条目的稳定 ID（基于文本哈希，语料增删不影响已有条目）。"""
    return "emotion:" + hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


@dataclass
class _Example:
    """语料样本（text 为触发文本，intent 为对应情绪意图）。"""

    text: str
    intent: EmotionIntent


class EmbeddingMessageClassifier:
    """向量情绪分类器（向量优先，不可用时 neutral 兜底）。

    Usage::

        clf = EmbeddingMessageClassifier()      # 自动探测 core.vector 配置
        ok = await clf.initialize()             # 幂等入库语料
        intent = await clf.classify("我今天太开心了")
    """

    def __init__(
        self,
        top_k: int = 5,
        threshold: float = 0.65,
        cache_size: int = 256,
        examples: Sequence[tuple[str, str, str, float, list[str]]] | None = None,
        store: Any | None = None,
        max_samples_per_emotion: int = _MAX_SAMPLES_PER_EMOTION,
    ) -> None:
        """构建向量情绪分类器。

        Args:
            top_k: 参与投票的最近邻居数。
            threshold: 相似度阈值（低于该值的邻居不参与投票）。
            cache_size: 查询 LRU 容量，0 关闭。
            examples: 语料覆盖（测试注入用），缺省从 data/emotion_corpus.json 加载。
            store: 注入 VectorStore（测试用）；缺省懒加载进程级共享情绪向量库。
            max_samples_per_emotion: 每类情绪最多向量化入库的样本数（0=全量）。
        """
        self._top_k = top_k
        self._threshold = threshold
        self._cache_size = cache_size
        if examples is not None:
            source = examples
        elif max_samples_per_emotion == _MAX_SAMPLES_PER_EMOTION:
            source = _DEFAULT_EXAMPLES
        else:
            source = _load_corpus_examples(max_samples_per_emotion)
        self._examples: list[_Example] = [
            _Example(text=t, intent=EmotionIntent(
                emotion=e, variant=v, intensity=i, contextTags=list(tags),
            ))
            for t, e, v, i, tags in source
        ]
        # 精确命中表：规范化文本 → 意图
        self._exact_hits: dict[str, EmotionIntent] = {
            _normalize(ex.text): ex.intent for ex in self._examples
        }
        self._cache: OrderedDict[str, EmotionIntent] = OrderedDict()

        self._store: Any | None = store
        self._enabled: bool = store is not None  # 注入 store 视为已启用
        self._ready: bool = False  # 语料是否已入库

        if store is None:
            self._store = _get_shared_store()
            self._enabled = self._store is not None

    # ── 兜底 ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _neutral_intent(message: str) -> EmotionIntent:
        """向量不可用 / 无有效邻居时的 neutral 兜底意图。"""
        return EmotionIntent(
            emotion="neutral",
            variant="neutral_ack",
            intensity=0.35,
            contextTags=["normal_chat"],
            sourceMessage=message,
        )

    # ── 初始化 ───────────────────────────────────────────────────────────────

    @property
    def configured(self) -> bool:
        """向量配置是否就绪（独立于语料入库状态；未就绪时 classify 走 neutral 兜底）。"""
        return self._enabled

    @property
    def enabled(self) -> bool:
        """向量路径是否可用（配置就绪 + 语料已入库）。"""
        return self._enabled and self._ready

    @property
    def initialized(self) -> bool:
        """语料是否已入库（未入库前 classify 直接走 neutral 兜底）。"""
        return self._ready

    async def initialize(self) -> bool:
        """幂等入库情绪语料。失败返回 False（后续 classify 走 neutral 兜底）。

        仅补库缺失语料，并用 add_many 一次性批量向量化，减少 Embedding API 调用。
        内部加锁：预热与首次分类并发触发时只执行一次入库。
        """
        if self._ready or not self._enabled or self._store is None:
            return self.enabled
        async with _INIT_LOCK:
            if self._ready or not self._enabled or self._store is None:
                return self.enabled
            try:
                missing: list[dict] = []
                for ex in self._examples:
                    iid = _stable_id(ex.text)
                    if self._store.get(iid) is None:
                        missing.append({
                            "text": ex.text,
                            "metadata": {
                                "kind": "emotion",
                                "emotion": ex.intent.emotion,
                                "variant": ex.intent.variant or "",
                                "intensity": ex.intent.intensity,
                                "contextTags": ex.intent.contextTags,
                            },
                            "item_id": iid,
                        })
                if missing:
                    await self._store.add_many(missing)
                self._ready = True
                logger.info(
                    "[EmotionVector] 情绪语料已入库 | count=%d | dim=%d",
                    len(self._examples), self._store.dimension,
                )
            except Exception as exc:
                logger.warning("[EmotionVector] 语料入库失败，neutral 兜底: %s", exc)
                self._ready = False
        return self.enabled

    # ── 分类 ────────────────────────────────────────────────────────────────

    async def classify(self, message: str) -> EmotionIntent:
        """将消息分类为情绪意图。

        精确命中 → 查询缓存 → 向量检索投票；不可用时 neutral 兜底。
        """
        key = _normalize(message)
        exact = self._exact_hits.get(key)
        if exact is not None:
            return exact
            
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        if not self.enabled or self._store is None:
            return self._neutral_intent(message)

        try:
            results = await self._store.search_async(
                message, top_k=self._top_k, threshold=self._threshold,
            )
        except Exception as exc:
            logger.warning("[EmotionVector] 检索失败，neutral 兜底: %s", exc)
            return self._neutral_intent(message)

        intent = self._vote(results, message)
        if self._cache_size > 0:
            self._cache_set(key, intent)
        return intent

    # ── 投票 ────────────────────────────────────────────────────────────────

    def _similarity_weight(self, similarity: float) -> float:
        """将相似度映射为投票权重。

        阈值以下为 0（不参与投票）；阈值以上按比例放大到 (0, 1]，
        使"刚过阈值"的邻居权重趋近 0、"接近匹配"的邻居权重趋近 1。
        """
        if similarity <= self._threshold:
            return 0.0
        return max(1e-6, (similarity - self._threshold) / max(1e-6, 1 - self._threshold))

    def _vote(self, results: Sequence[Any], message: str) -> EmotionIntent:
        """Top-K 邻居按情绪投票。

        算法（与 classifier-embedding 一致）：
        - 过阈值邻居以归一化权重（_similarity_weight）投票
        - 权重和最高的情绪获胜；intensity 取获胜样本标注强度的加权均值
        - naturalVAD 取所有过阈值邻居的 VAD 加权均值
        - contextTags 合并获胜样本的标签（去重）
        """
        if not results:
            return self._neutral_intent(message)

        scored: list[dict] = []
        for r in results:
            meta = getattr(r, "metadata", {}) or {}
            emotion = meta.get("emotion")
            if emotion not in emotionVADPresets:
                continue
            weight = self._similarity_weight(max(float(getattr(r, "score", 0.0)), 0.0))
            if weight <= 0:
                continue
            scored.append({
                "emotion": emotion,
                "variant": str(meta.get("variant") or "") or None,
                "intensity": float(meta.get("intensity", 0.5)),
                "tags": list(meta.get("contextTags") or []),
                "weight": weight,
            })

        if not scored:
            return self._neutral_intent(message)

        # 按情绪汇总权重，选出获胜情绪及其全部样本
        emotion_scores: dict[str, float] = {}
        for item in scored:
            emotion_scores[item["emotion"]] = emotion_scores.get(item["emotion"], 0.0) + item["weight"]
        dominant = max(emotion_scores, key=lambda k: emotion_scores[k])
        winners = [item for item in scored if item["emotion"] == dominant]

        # intensity = 获胜样本标注强度的加权均值
        win_weight = sum(item["weight"] for item in winners)
        intensity = sum(item["intensity"] * item["weight"] for item in winners) / win_weight if win_weight > 0 else winners[0]["intensity"]
        intensity = max(0.0, min(intensity, 1.0))

        # naturalVAD = 全部过阈值邻居预设向量的 VAD 加权均值
        weight_sum = sum(item["weight"] for item in scored)
        vad_acc = {"valence": 0.0, "arousal": 0.0, "dominance": 0.0}
        for item in scored:
            vad = emotionVADPresets[item["emotion"]]
            vad_acc["valence"] += vad.valence * item["weight"]
            vad_acc["arousal"] += vad.arousal * item["weight"]
            vad_acc["dominance"] += vad.dominance * item["weight"]
        natural_vad = {k: v / weight_sum for k, v in vad_acc.items()}

        # contextTags 合并获胜样本的标签（去重）
        tags: list[str] = []
        for item in winners:
            for tag in item["tags"]:
                if tag not in tags:
                    tags.append(tag)

        return EmotionIntent(
            emotion=dominant,
            variant=winners[0]["variant"],
            naturalVAD=natural_vad,
            intensity=round(intensity, 3),
            contextTags=tags,
            sourceMessage=message,
        )

    # ── 查询 LRU ────────────────────────────────────────────────────────────

    def _cache_get(self, key: str) -> EmotionIntent | None:
        """查询 LRU 缓存；命中时移到队尾（保持最近使用顺序）。"""
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def _cache_set(self, key: str, intent: EmotionIntent) -> None:
        """写入 LRU 缓存；超过容量时淘汰队首（最久未使用）条目。"""
        self._cache[key] = intent
        self._cache.move_to_end(key)
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def clear_query_cache(self) -> None:
        """清空查询缓存。"""
        self._cache.clear()

    async def aclose(self) -> None:
        """释放向量库底层资源（如 Embedding API 客户端连接池），并重置状态。

        幂等；分类器不可再用（后续 classify 走 neutral 兜底，除非重新 initialize）。
        进程级共享向量库由其他分类器复用，关闭单个分类器不释放它（由进程退出统一清理）。
        """
        store, self._store = self._store, None
        if store is not None and store is not _shared_store:
            aclose = getattr(store, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception as exc:
                    logger.warning("[EmotionVector] 释放向量库资源异常: %s", exc)
        self._ready = False
        self._enabled = False
        self._cache.clear()


async def prewarm_emotion_corpus() -> bool:
    """启动预热：确保情绪语料已向量化入库（幂等，进程级共享库）。

    供应用启动时（无需任何 WS 连接）调用，避免首次对话时同步等待向量化。
    返回预热后向量路径是否可用。
    """
    try:
        from agent.config import agent_config_from_yaml
        clf = EmbeddingMessageClassifier(max_samples_per_emotion=agent_config_from_yaml().emotion_max_samples)
    except Exception:
        clf = EmbeddingMessageClassifier()
    if clf.configured and not clf.initialized:
        return await clf.initialize()
    return clf.enabled


__all__ = ["EmbeddingMessageClassifier", "prewarm_emotion_corpus"]
