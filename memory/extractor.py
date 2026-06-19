"""五元组提取模块

使用项目 core/llm/ 替换直接 API 调用，通过 memory._providers 复用共享 Provider。

五元组格式: (主体, 主体类型, 谓语, 宾语, 宾语类型)
"""

from __future__ import annotations

import asyncio
import json
from typing import List, Tuple

from core.llm.models import ChatRequest, Message
from core.logger import get_logger

from memory.config import get_grag_config
from memory._providers import get_memory_provider
from memory.exceptions import (
    ExtractionTimeoutError,
    LLMProviderError,
)

logger = get_logger(__name__)

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

3. 类型包括但不限于：人物、地点、组织、物品、概念、时间、事件、活动等。

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
        self.max_retries = max_retries or cfg.extractor.max_retries
        self.timeout = timeout or cfg.extractor.timeout

    @property
    def provider(self):
        """获取 LLM Provider（通过模块级懒加载共享单例）"""
        return get_memory_provider()

    def extract(self, text: str) -> List[QuintupleType]:
        """
        同步提取五元组（供无事件循环的上下文使用）。

        内部驱动 extract_async，调用方须在无运行中事件循环的上下文中使用。
        """
        return asyncio.run(self.extract_async(text))

    async def extract_async(self, text: str) -> List[QuintupleType]:
        """
        异步提取五元组（含指数退避重试 + 超时控制）。

        Args:
            text: 待提取的文本

        Returns:
            五元组列表

        Raises:
            ExtractionTimeoutError: 提取超时
            LLMProviderError:       LLM 提供者错误
        """
        request = ChatRequest(
            messages=[
                Message(role="system", content=SYSTEM_PROMPT).to_api_dict(),
                Message(
                    role="user",
                    content=USER_PROMPT_TEMPLATE.format(text=text),
                ).to_api_dict(),
            ],
            model=self.provider.model,
            temperature=0.3,
            max_tokens=2000,
        )

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    self.provider.async_chat_completion(request),
                    timeout=self.timeout,
                )
                quintuples = self._parse_response(response.content.strip())
                logger.info("提取到 %d 个五元组", len(quintuples))
                return quintuples

            except asyncio.TimeoutError:
                last_exc = asyncio.TimeoutError(f"LLM 调用超时 ({self.timeout}s)")
                logger.warning("提取超时 (尝试 %d)", attempt + 1)
                if attempt < self.max_retries:
                    await asyncio.sleep(1 + attempt)

            except Exception as e:
                last_exc = e
                logger.warning("提取失败 (尝试 %d): %s", attempt + 1, e)
                if attempt < self.max_retries:
                    await asyncio.sleep(1 + attempt)

        # 所有重试均失败
        assert last_exc is not None
        if isinstance(last_exc, asyncio.TimeoutError):
            raise ExtractionTimeoutError(
                timeout=float(self.timeout),
                details={"attempt": self.max_retries + 1},
                cause=last_exc,
            )
        raise LLMProviderError(
            message=str(last_exc),
            provider=type(self.provider).__name__,
            details={"attempt": self.max_retries + 1},
            cause=last_exc,
        )

    def _parse_response(self, content: str) -> List[QuintupleType]:
        """解析 LLM 响应，提取五元组"""
        content = content.strip()

        # 尝试直接解析 JSON
        try:
            data = json.loads(content)
            return self._validate_quintuples(data)
        except json.JSONDecodeError:
            pass

        # 尝试提取 JSON 块
        if "[" in content and "]" in content:
            start = content.index("[")
            end = content.rindex("]") + 1
            try:
                data = json.loads(content[start:end])
                return self._validate_quintuples(data)
            except json.JSONDecodeError:
                pass

        logger.warning("无法解析五元组响应: %.200s", content)
        return []

    def _validate_quintuples(self, data) -> List[QuintupleType]:
        """验证并规范化五元组数据"""
        if not isinstance(data, list):
            return []

        result = []
        for item in data:
            if isinstance(item, (list, tuple)) and len(item) == 5:
                # 确保所有元素都是非空字符串
                if all(isinstance(x, str) and x.strip() for x in item):
                    result.append(tuple(x.strip() for x in item))

        return result


# 全局提取器实例
_extractor: QuintupleExtractor | None = None


def get_extractor() -> QuintupleExtractor:
    """获取五元组提取器单例"""
    global _extractor
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
