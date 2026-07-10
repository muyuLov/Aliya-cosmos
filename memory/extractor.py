"""五元组提取模块

使用项目 core/llm/ 替换直接 API 调用，通过 memory._providers 复用共享 Provider。

五元组格式: (主体, 主体类型, 谓语, 宾语, 宾语类型)
"""

from __future__ import annotations

import asyncio
import threading
from typing import List, Tuple

from core.llm.models import ChatRequest, Message
from core.logger import get_logger

logger = get_logger(__name__)

from core.llm.providers.base import LLMProvider
from memory._utils import parse_json_array
from memory._retry import async_retry, is_transient_error
from memory.config import get_grag_config
from memory._providers import get_memory_provider
from memory.exceptions import (
    ExtractionTimeoutError,
    LLMProviderError,
)

# 五元组类型别名
QuintupleType = Tuple[str, str, str, str, str]

# 系统提示词
SYSTEM_PROMPT = """
你是一个专业的中文文本信息抽取专家。你的任务是从给定的中文文本中抽取有价值的五元组关系。
五元组格式为：(主体, 主体类型, 动作, 客体, 客体类型)。

## 提取规则
1. 只提取**事实性**信息，包括：
   - 具体的行为和动作
   - 明确的实体关系
   - 实际存在的状态和属性
   - 用户表达的具体需求、偏好、计划

2. 严格过滤以下内容：
   - 比喻、拟人、夸张等修辞手法
   - 虚拟、假设、想象的内容
   - 纯粹的情感表达（如"我很开心"、"你真棒"）
   - 赞美、讽刺、调侃等主观评价
   - 闲聊中的无关信息
   - 重复或冗余的关系

3. 类型必须从以下列表中选择，不得使用其他类型：
   人物、角色、身份、地点、区域、设施、组织、机构、品牌、物品、产品、食物、动植物、
   软件、平台、技术、算法、数据、时间、日期、周期、事件、活动、技能、学科、领域、
   语言、职业、项目、作品、概念、目标、规则、方法、原因、结果、关系、
   属性、状态、年龄、数量、价格、比例

## 示例

输入：小明在公园里踢足球。
输出：
- 主体：小明，类型：人物，动作：踢，客体：足球，类型：物品
- 主体：小明，类型：人物，动作：在，客体：公园，类型：地点

输入：你像小太阳一样温暖。
输出：[] （比喻句，不提取）

输入：我喜欢吃苹果和香蕉。
输出：
- 主体：我，类型：人物，动作：喜欢吃，客体：苹果，类型：物品
- 主体：我，类型：人物，动作：喜欢吃，客体：香蕉，类型：物品

输入：如果我是鸟，我会飞到月球。
输出：[] （假设内容，不提取）

请仔细分析文本，只提取有价值的事实性五元组关系。
"""

# 合法实体类型集合
VALID_ENTITY_TYPES = frozenset({
    # ── 人物与角色 ────────────────────────────────────────────
    "人物", "Person",
    "角色", "Role",
    "身份", "Identity",

    # ── 地点与设施 ────────────────────────────────────────────
    "地点", "Location",
    "区域", "Region",
    "设施", "Facility",

    # ── 组织与机构 ────────────────────────────────────────────
    "组织", "Organization",
    "机构", "Institution",
    "品牌", "Brand",

    # ── 物品与产品 ────────────────────────────────────────────
    "物品", "Object",
    "产品", "Product",
    "食物", "Food",
    "动植物", "Biology",

    # ── 科技与信息 ────────────────────────────────────────────
    "软件", "Software",
    "平台", "Platform",
    "技术", "Technology",
    "算法", "Algorithm",
    "数据", "Data",

    # ── 时间 ─────────────────────────────────────────────────
    "时间", "Time",
    "日期", "Date",
    "周期", "Period",

    # ── 事件与活动 ────────────────────────────────────────────
    "事件", "Event",
    "活动", "Activity",

    # ── 知识与工作 ────────────────────────────────────────────
    "技能", "Skill",
    "学科", "Subject",
    "领域", "Domain",
    "语言", "Language",
    "职业", "Occupation",
    "项目", "Project",
    "作品", "Work",

    # ── 抽象概念 ─────────────────────────────────────────────
    "概念", "Concept",
    "目标", "Goal",
    "规则", "Rule",
    "方法", "Method",
    "原因", "Cause",
    "结果", "Result",
    "关系", "Relation",

    # ── 属性与度量 ────────────────────────────────────────────
    "属性", "Attribute",
    "状态", "State",
    "年龄", "Age",
    "数量", "Quantity",
    "价格", "Price",
    "比例", "Ratio",
})


def _is_valid_entity_type(t: str) -> bool:
    return t in VALID_ENTITY_TYPES


# 用户提示词模板
USER_PROMPT_TEMPLATE = """请从以下文本中提取五元组：

{text}

只返回 JSON 数组格式，例如：[["主体", "类型", "谓语", "宾语", "类型"]]
不要输出任何其他内容。"""


class QuintupleExtractor:
    """五元组提取器"""

    def __init__(
        self,
        max_retries: int | None = None,
        timeout: int | None = None,
    ):
        """
        初始化五元组提取器

        Args:
            max_retries: 最大重试次数，None 时从配置读取
            timeout:     超时时间（秒），None 时从配置读取
        """
        cfg = get_grag_config()
        self.max_retries = max_retries if max_retries is not None else cfg.extractor.max_retries
        self.timeout = timeout if timeout is not None else cfg.extractor.timeout

    @property
    def provider(self) -> LLMProvider:
        """获取 LLM Provider（通过模块级懒加载共享单例）"""
        return get_memory_provider()

    def extract(self, text: str) -> List[QuintupleType]:
        """
        同步提取五元组（供无事件循环的上下文使用）。

        内部驱动 extract_async。若存在运行中的事件循环，抛出 RuntimeError
        提示调用方使用 extract_async 或 extract_quintuples。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.extract_async(text))
        raise RuntimeError(
            "extract() 不能在运行中的事件循环内调用，请使用 extract_async() 或 extract_quintuples()"
        )

    async def extract_async(self, text: str) -> List[QuintupleType]:
        """异步提取五元组（含指数退避重试 + 超时控制 + 永久性错误检测）。

        Args:
            text: 待提取的文本

        Returns:
            五元组列表

        Raises:
            ExtractionTimeoutError: 提取超时
            LLMProviderError:       LLM 提供者错误
        """
        safe_text = text

        request = ChatRequest(
            messages=[
                Message(role="system", content=SYSTEM_PROMPT).to_api_dict(),
                Message(
                    role="user",
                    content=USER_PROMPT_TEMPLATE.format(text=safe_text),
                ).to_api_dict(),
            ],
            model=self.provider.model,
            temperature=0.3,
            max_tokens=2000,
        )

        async def _call() -> str:
            response = await self.provider.async_chat_completion(request)
            return response.content.strip() if response.content else ""

        try:
            content = await async_retry(
                _call,
                max_retries=self.max_retries,
                timeout=float(self.timeout),
                operation_name="五元组提取",
            )
        except asyncio.TimeoutError as e:
            raise ExtractionTimeoutError(
                timeout=float(self.timeout),
                details={"attempt": self.max_retries + 1},
                cause=e,
            )
        except Exception as e:
            raise LLMProviderError(
                message=str(e),
                provider=type(self.provider).__name__,
                details={"attempt": self.max_retries + 1},
                cause=e,
            )

        quintuples = self._parse_response(content)
        logger.info("提取到 %d 个五元组", len(quintuples))
        return quintuples

    def _parse_response(self, content: str) -> List[QuintupleType]:
        """解析 LLM 响应，提取五元组"""
        data = parse_json_array(content, "五元组响应")
        if data is not None:
            return self._validate_quintuples(data)
        return []

    def _validate_quintuples(self, data) -> List[QuintupleType]:
        """验证并规范化五元组数据（含实体类型合理性校验）"""
        if not isinstance(data, list):
            return []

        result = []
        for item in data:
            if not isinstance(item, (list, tuple)) or len(item) != 5:
                continue
            if not all(isinstance(x, str) and x.strip() for x in item):
                continue
            head, head_type, rel, tail, tail_type = (x.strip() for x in item)
            if not _is_valid_entity_type(head_type):
                logger.debug("跳过非法主体类型: %s(%s)", head, head_type)
                continue
            if not _is_valid_entity_type(tail_type):
                logger.debug("跳过非法客体类型: %s(%s)", tail, tail_type)
                continue
            result.append((head, head_type, rel, tail, tail_type))

        return result


# 全局提取器实例
_extractor: QuintupleExtractor | None = None
_extractor_lock = threading.Lock()


def get_extractor() -> QuintupleExtractor:
    """获取五元组提取器单例（线程安全懒加载）"""
    global _extractor
    if _extractor is None:
        with _extractor_lock:
            if _extractor is None:
                _extractor = QuintupleExtractor()
    return _extractor


async def extract_quintuples(text: str) -> List[QuintupleType]:
    """
    便捷函数：异步提取五元组

    Args:
        text: 待提取的文本

    Returns:
        五元组列表
    """
    return await get_extractor().extract_async(text)


def extract_quintuples_sync(text: str) -> List[QuintupleType]:
    """
    便捷函数：同步提取五元组

    Args:
        text: 待提取的文本

    Returns:
        五元组列表
    """
    return asyncio.run(get_extractor().extract_async(text))


__all__ = [
    "QuintupleExtractor",
    "get_extractor",
    "extract_quintuples",
    "extract_quintuples_sync",
]
