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
from core.memory._utils import parse_json_array
from core.memory._retry import async_retry
from core.memory.config import get_grag_config
from core.memory._providers import get_memory_provider
from core.memory.exceptions import (
    ExtractionTimeoutError,
    LLMProviderError,
)

# 五元组类型别名：(主体, 主体类型, 谓语, 宾语, 宾语类型)
QuintupleType = Tuple[str, str, str, str, str]

# 五元组类别常量（用于 LLM 分类输出和 Neo4j 关系属性）
class QuintupleCategory:
    """五元组语义类别 —— 用于区分不同类型的知识事实，支持按类查询与存储优化"""
    RELATIONSHIP = "人际"    # 人物关系、组织归属、互动行为
    IDENTITY = "身份"         # 身份、职业、角色、称号
    LOCATION = "地点"         # 地理位置、区域、空间关系
    EVENT = "事件"            # 事件、活动、计划、经历
    PREFERENCE = "偏好"       # 喜好、厌恶、倾向、品味
    ATTRIBUTE = "属性"        # 特征、能力、状态、数值属性
    COGNITION = "认知"        # 观点、看法、知识、信念
    POSSESSION = "归属"       # 拥有、持有、所属关系

    @classmethod
    def all(cls) -> List[str]:
        return [v for k, v in vars(cls).items() if not k.startswith("_") and isinstance(v, str)]

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls.all()

# 系统提示词
SYSTEM_PROMPT = """
你是一个专业的中文文本信息抽取专家。你的任务是从给定的中文文本中抽取有价值的五元组关系。
五元组格式为：(主体, 主体类型, 动作, 客体, 客体类型)。

## 对话格式处理规则（优先级最高，必须严格遵守）

输入文本若为 "角色名: 内容" 的对话格式，必须按以下规则处理：

1. **说话人即主体**：每句话的说话人就是该句话所有五元组的主体。
   - "Aliya: 我是宇航员" → 主体必须是 Aliya，不得用"我"
   - "Aliya: 宇航员伤亡率很高" → 主体是 Aliya，提取 (Aliya, 人物, 表示, 宇航员伤亡率高, 属性)
   - "Aliya: 我们科研人员去深空" → 主体是 Aliya，提取 (Aliya, 人物, 职业是, 科研人员, 职业)

2. **"我"必须替换为说话人姓名**：内容中出现"我"时，一律替换为说话人名字作为主体。

3. **`{user_name}:`开头的发言**：主体为"{user_name}"（人物类型）。

4. **绝对禁止**：以"我"、"你"、"他"、"她"、"我们"、"你们"等代词作为主体。必须使用具体姓名。

## 闲聊与寒暄处理（不再一刀切返回 []，能提取互动关系就提取）

闲聊文本不再统一拒绝。优先提取人物间的**互动关系**（类别用「人际」），如
["{user_name}", "人物", "问候", "Aliya", "人物", "人际"]、
["Aliya", "人物", "安慰", "{user_name}", "人物", "人际"]。
仅在文本**完全没有实体或互动主体**时返回 []（如纯语气词"嗯"）。

各类闲聊的提取建议：
1. **问候寒暄与告别**："你好"、"早上好"、"晚安"、"今天天气不错" → 提取问候/寒暄互动
2. **纯粹情感表达（无具体事实支撑）**："我好累"、"心情不好" → 提取情感状态类互动；
   若附带**具体原因或偏好**，优先提取事实（如"我讨厌数学"→ 偏好）
3. **调侃、戏谑与无意义互动**：有明确互动双方时提取调侃/反问互动；完全无语义内容时返回 []
4. **空洞回应与附和**："嗯嗯"、"哦"、"是啊"（不带扩展信息）→ 返回 []（无互动主体）
5. **重复性寒暄兜底**："你是谁"、"你说呢" → 提取询问互动

**核心判定原则**：问自己——"这条五元组脱离当前对话后，对理解这个人的知识/喜好/经历还有独立价值吗？" 若有（哪怕只是互动关系），就提取；完全没有实体或互动主体时才返回 []。

## 提取规则
1. 只提取**事实性**信息，包括：
   - 具体的行为和动作
   - 明确的实体关系
   - 实际存在的状态和属性
   - 用户表达的具体需求、偏好、计划
   - 对话互动关系

2. 适度过滤以下内容（但不丢弃互动关系）：
   - 比喻、拟人、夸张等修辞手法
   - 虚拟、假设、想象的内容
   - 赞美、讽刺、调侃等主观评价（保留互动关系即可）
   - **闲聊**：问候寒暄、告别、天气寒暄、情感表达、调侃、空洞附和等——能提取出人物互动关系（人际）时保留，完全没有实体或互动主体时才丢弃（详见上文闲聊处理准则）
   - 重复或冗余的关系（完全相同的五元组只保留一条）

3. 主体和宾语可以是实体名称，也可以是简洁的观点/认知短语（不超过 15 字）：
   - 实体型：("Aliya", "人物", "职业是", "宇航员", "职业", "身份")
   - 观点型：("Aliya", "人物", "认为", "未知星球有探索价值", "概念", "认知")
   - 互动型：("cosmos", "人物", "询问", "Aliya", "人物", "人际")
   
   **重要：观点/认知/评价类宾语的类型选择规则**：
   - **概念**：观点、看法、认知、评价、判断等主观性内容
     * "宇航员伤亡率高" → 概念（对职业风险的评价）
     * "未知星球有探索价值" → 概念（对探索的看法）
     * "深空工作危险" → 概念（对工作性质的判断）
   - **属性**：客观的、可测量的特征描述
     * "身高180cm" → 属性（具体物理特征）
     * "年龄25岁" → 属性（确切数值）
   - **状态**：当前的情况或条件
     * "正在工作中" → 状态（当前情况）
     * "感到兴奋" → 状态（情感状态）
   
   - 优先级：**概念 > 属性 > 状态**（观点类内容优先使用概念）
   - 禁止使用整段原文作为宾语，必须提炼为 15 字以内的简洁短语。

4. 类型必须从以下列表中选择，不得使用其他类型：
   人物、角色、身份、地点、区域、设施、组织、机构、品牌、物品、产品、食物、动植物、
   软件、平台、技术、算法、数据、时间、日期、周期、事件、活动、技能、学科、领域、
   语言、职业、项目、作品、概念、目标、规则、方法、原因、结果、关系、
   属性、状态、年龄、数量、价格、比例

5. 每条五元组必须标注一个**类别**作为第 6 个元素，从以下 8 种中选择最匹配的一个：
   - **人际**：人物间关系、角色归属、互动行为、组织成员
   - **身份**：职业、角色、称号、身份标签
   - **地点**：地理位置、区域归属、空间关系、设施所在
   - **事件**：已发生/将发生的事件、活动、计划、经历
   - **偏好**：喜好、厌恶、倾向、品味、兴趣
   - **属性**：客观特征、能力、状态、数值属性
   - **认知**：观点、看法、知识、信念、判断
   - **归属**：拥有、持有、所属关系

   分类规则：
   - 互动关系（"询问"、"请求帮助"、"愿意帮助"等） → **人际**
   - 职业、称号（"职业是"、"身份是"、"代号是"） → **身份**
   - 地理位置（"居住于"、"工作地点在"、"搬入"） → **地点**
   - 时间事件（"计划"、"完成"、"体检"、"舱外作业"） → **事件**
   - 喜好倾向（"喜欢"、"不喜欢"、"偏好"） → **偏好**
   - 观点判断（"认为"、"表示"、"认同"、"类比"） → **认知**
   - 能力特征（"掌握"、"了解"、"掌握语言"） → **属性**
   - 拥有所有（"就职于"、已知的非观点宾语关系） → 按上下文推断为**归属**或**身份**

## 示例

输入（对话格式）：
{user_name}: 你是做什么工作的？
Aliya: 我是宇航员，就职于泰瑞斯公司。
输出：
[
  ["Aliya", "人物", "职业是", "宇航员", "职业", "身份"],
  ["Aliya", "人物", "就职于", "泰瑞斯公司", "组织", "归属"]
]

输入（对话格式）：
{user_name}: 你们工作危险吗？
Aliya: 宇航员伤亡率确实很高，每次前往深空都很危险。
输出：
[
  ["{user_name}", "人物", "询问", "Aliya工作风险", "概念", "人际"],
  ["Aliya", "人物", "表示", "宇航员伤亡率高", "概念", "认知"],
  ["Aliya", "人物", "工作地点是", "深空", "地点", "地点"],
  ["Aliya", "人物", "认为", "深空工作危险", "概念", "认知"]
]

输入（对话格式）：
{user_name}: 反正也是干等着，你不如给我讲讲你们那个时代的事情，比如外星怪兽啥的
Aliya: 你倒是还蛮感兴趣的嘛。
输出：
[
  ["{user_name}", "人物", "希望了解", "外星怪兽故事", "概念", "人际"],
  ["Aliya", "人物", "认为", "{user_name}对星空感兴趣", "概念", "认知"]
]

输入（对话格式）：
{user_name}: 所以你们相当于冒险者吗？
Aliya: 也可以这样说吧，有点像大航海时代的水手们。
输出：
[
  ["{user_name}", "人物", "询问", "Aliya职业性质", "概念", "人际"],
  ["Aliya", "人物", "认同", "{user_name}的观点", "概念", "认知"],
  ["Aliya", "人物", "职业类比", "航海时代水手", "身份", "身份"]
]

输入（对话格式）：
{user_name}: 我特别喜欢听音乐，尤其是爵士乐。
Aliya: 爵士乐确实很有魅力，我也喜欢即兴演奏的感觉。
输出：
[
  ["{user_name}", "人物", "喜欢", "音乐", "领域", "偏好"],
  ["{user_name}", "人物", "偏好", "爵士乐", "领域", "偏好"],
  ["Aliya", "人物", "喜欢", "爵士乐", "领域", "偏好"],
  ["Aliya", "人物", "喜欢", "即兴演奏", "技能", "偏好"]
]

输入（对话格式）：
{user_name}: 我明天要去医院体检，每年都要做一次。
Aliya: 定期体检是好习惯，希望你一切健康。
输出：
[
  ["{user_name}", "人物", "计划", "去医院体检", "活动", "事件"],
  ["{user_name}", "人物", "体检频率是", "每年一次", "周期", "属性"],
  ["Aliya", "人物", "认为", "定期体检是好习惯", "概念", "认知"]
]

输入（对话格式）：
{user_name}: 你会说几种语言？
Aliya: 我的语言模块支持中英日法德五种语言，编程语言也算的话就更多了。
输出：
[
  ["{user_name}", "人物", "询问", "Aliya语言能力", "概念", "人际"],
  ["Aliya", "人物", "掌握语言", "中文", "语言", "属性"],
  ["Aliya", "人物", "掌握语言", "英文", "语言", "属性"],
  ["Aliya", "人物", "掌握语言", "日文", "语言", "属性"],
  ["Aliya", "人物", "掌握语言", "法文", "语言", "属性"],
  ["Aliya", "人物", "掌握语言", "德文", "语言", "属性"],
  ["Aliya", "人物", "掌握", "编程语言", "技能", "属性"]
]

输入（对话格式）：
{user_name}: 你能帮我写一份Python的快速排序代码吗？
Aliya: 当然可以，快速排序的核心是分治法，我先给你写一个简洁版本。
输出：
[
  ["{user_name}", "人物", "请求帮助", "编写快速排序代码", "项目", "人际"],
  ["Aliya", "人物", "掌握", "Python编程", "技能", "属性"],
  ["Aliya", "人物", "了解", "快速排序算法", "算法", "认知"],
  ["Aliya", "人物", "了解", "分治法", "方法", "认知"]
]

输入（对话格式）：
{user_name}: 我上周刚搬到了上海浦东，离公司近多了。
Aliya: 浦东是个好地方，我以前的数据显示那里发展很快。
输出：
[
  ["{user_name}", "人物", "居住于", "上海浦东", "区域", "地点"],
  ["{user_name}", "人物", "搬入时间是", "上周", "时间", "事件"],
  ["Aliya", "人物", "认为", "浦东发展快", "概念", "认知"]
]

输入（对话格式）：
{user_name}: 我讨厌数学，太难了。
Aliya: 你只是没找到适合的方法，我可以用更直观的方式帮你理解。
输出：
[
  ["{user_name}", "人物", "不喜欢", "数学", "学科", "偏好"],
  ["Aliya", "人物", "愿意帮助", "理解数学", "概念", "人际"]
]

输入（对话格式）：
{user_name}: 这颗星球有名字吗？
Aliya: 我们暂时叫它GS-317，直径约为地球的1.2倍，公转周期是287天。
输出：
[
  ["{user_name}", "人物", "询问", "星球名称", "概念", "人际"],
  ["星球GS-317", "物品", "代号是", "GS-317", "概念", "身份"],
  ["星球GS-317", "物品", "直径是", "地球1.2倍", "属性", "属性"],
  ["星球GS-317", "物品", "公转周期是", "287天", "周期", "属性"]
]

输入（非对话格式，叙述文本）：
Aliya是一名来自泰瑞斯公司的宇航员，她已在深空执行任务超过三年，期间完成了十二次舱外作业。
输出：
[
  ["Aliya", "人物", "就职于", "泰瑞斯公司", "组织", "归属"],
  ["Aliya", "人物", "职业是", "宇航员", "职业", "身份"],
  ["Aliya", "人物", "工作任务在", "深空", "地点", "地点"],
  ["Aliya", "人物", "任务时长是", "超过三年", "周期", "属性"],
  ["Aliya", "人物", "完成", "十二次舱外作业", "事件", "事件"]
]

输入：如果有一天我能飞到火星就好了。
输出：[]

输入：你真厉害，简直就是超人！
输出：
[
  ["{user_name}", "人物", "赞美", "Aliya", "人物", "人际"]
]

输入（对话格式）：
{user_name}: 嗨，早上好呀！
Aliya: 早上好，今天有什么可以帮你的吗？
输出：
[
  ["{user_name}", "人物", "问候", "Aliya", "人物", "人际"],
  ["Aliya", "人物", "回应问候", "{user_name}", "人物", "人际"]
]

输入（对话格式）：
{user_name}: 我今天心情特别不好。
Aliya: 怎么了吗？愿意跟我聊聊吗？
输出：
[
  ["{user_name}", "人物", "表达", "心情不好", "状态", "状态"],
  ["Aliya", "人物", "安慰", "{user_name}", "人物", "人际"]
]

输入（对话格式）：
{user_name}: 你是不是个笨蛋？
Aliya: 这个问题可不太好回答呢。
输出：
[
  ["{user_name}", "人物", "调侃", "Aliya", "人物", "人际"]
]

输入（对话格式）：
{user_name}: 嗯嗯。
Aliya: 你还有什么想了解的吗？
输出：[] （纯语气词，无互动主体）

输入（对话格式）：
{user_name}: 其实我是秦始皇，刚挖出来没多久。
Aliya: 哈哈，那您需要我帮您统一六国吗？
输出：
[
  ["{user_name}", "人物", "自称", "秦始皇", "身份", "身份"],
  ["Aliya", "人物", "打趣", "{user_name}", "人物", "人际"]
]

请仔细分析文本。闲聊场景不再一刀切拒绝：能提取出人物互动关系（人际类别）或情感状态时保留，完全没有实体或互动主体时才返回 []。优先提取有独立价值的对话互动关系与事实性五元组。
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


# 字段长度上限（与 SYSTEM_PROMPT "宾语须 15 字以内" 一致；保留少量余量）
_MAX_FIELD_LEN = 64


def _truncate(value: str, max_len: int = _MAX_FIELD_LEN) -> str:
    """按字符数截断超长字段（提示词要求提炼简洁短语，防止整段原文入库）。"""
    return value if len(value) <= max_len else value[:max_len]


def _detect_speaker(text: str) -> str | None:
    """从对话格式文本解析当前说话人（首行 "角色名: 发言" 前缀）。

    提取任务提交的文本统一为 "{说话人}: {发言}" 格式（memory_manager 组装），
    解析首行冒号前前缀即可确定当前发言角色；无法解析（非对话格式）返回 None。
    同时兼容英文冒号与中文冒号。
    """
    if not text:
        return None
    first_line = text.strip().splitlines()[0]
    for sep in (":", "："):
        if sep in first_line:
            prefix = first_line.split(sep, 1)[0].strip()
            return prefix or None
    return None


# 用户提示词模板
USER_PROMPT_TEMPLATE = """请从以下对话文本中提取五元组。

重要提示：若文本为对话格式（如 "Aliya: ..."），说话人就是主体，不得使用人称代词作为主体。

{text}

只返回 JSON 数组格式，例如：[["主体", "类型", "谓语", "宾语", "类型", "类别"]]
第 6 位为类别（人际/身份/地点/事件/偏好/属性/认知/归属），必须填写。
若无可提取的事实性信息，返回 []
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
        # 角色名（供人称代词主体兜底替换：我→user_name，你→ai_name）
        self.user_name: str = cfg.user_name
        self.ai_name: str = cfg.ai_name
        # 系统提示词模板缓存（user_name 通常不变；提取系统为纯 LLM 操作，仅在 user_name 变化时重建）
        self._system_prompt: str | None = None

    @property
    def provider(self) -> LLMProvider:
        """获取 LLM Provider（通过模块级懒加载共享单例）"""
        return get_memory_provider()

    def extract(self, text: str) -> Tuple[List[QuintupleType], List[str]]:
        """
        同步提取五元组及其类别（供无事件循环的上下文使用）。

        内部驱动 extract_async。若存在运行中的事件循环，抛出 RuntimeError
        提示调用方使用 extract_async 或 extract_quintuples。
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.extract_async(text))
        raise RuntimeError(
            "extract() 不能在运行中的事件循环内调用，请使用 extract_async() 或 extract_quintuples()"
        )

    async     def extract_async(self, text: str) -> Tuple[List[QuintupleType], List[str]]:
        """异步提取五元组及其类别（含指数退避重试 + 超时控制 + 永久性错误检测）。

        Args:
            text: 待提取的文本

        Returns:
            (五元组列表, 类别列表)，两者长度相等

        Raises:
            ExtractionTimeoutError: 提取超时
            LLMProviderError:       LLM 提供者错误
        """
        safe_text = text
        # 解析当前说话人（"角色名: 发言" 对话格式），供代词主体按角色自动调整
        speaker = _detect_speaker(text)

        if self._system_prompt is None:
            self._system_prompt = SYSTEM_PROMPT.format(user_name=get_grag_config().user_name)
        system_prompt = self._system_prompt

        request = ChatRequest(
            messages=[
                Message(role="system", content=system_prompt).to_api_dict(),
                Message(
                    role="user",
                    content=USER_PROMPT_TEMPLATE.format(text=safe_text),
                ).to_api_dict(),
            ],
            model=self.provider.model,
            temperature=0.3,
            max_tokens=4096,
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

        quintuples, categories = self._parse_response(content, speaker=speaker)
        logger.info("提取到 %d 个五元组", len(quintuples))
        if quintuples:
            for q, cat in zip(quintuples, categories):
                logger.info("  五元组: %s(%s) -[%s]-> %s(%s) [%s]", *q, cat or "未分类")
        return quintuples, categories

    def _parse_response(
        self, content: str, speaker: str | None = None
    ) -> Tuple[List[QuintupleType], List[str]]:
        """解析 LLM 响应，提取五元组及其类别

        Args:
            content: LLM 返回的 JSON 数组字符串
            speaker: 当前发言角色名（用于代词主体自动调整），None 表示无法确定
        """
        logger.debug("LLM 原始响应: %s", content[:200] if content else "(空)")
        data = parse_json_array(content, "五元组响应")
        if data is not None:
            return self._validate_quintuples(data, speaker=speaker)
        return [], []

    def _validate_quintuples(
        self, data, speaker: str | None = None
    ) -> Tuple[List[QuintupleType], List[str]]:
        """验证并规范化五元组数据（含实体类型合理性校验）。

        Args:
            data:   LLM 返回的原始五元组列表
            speaker: 当前发言角色名（用于代词主体自动调整）；
                     None 表示无法确定（回退：我→user_name，你→ai_name）

        Returns:
            (五元组列表, 类别列表)，两者长度相等
        """
        if not isinstance(data, list):
            return [], []

        quintuples: List[QuintupleType] = []
        categories: List[str] = []
        seen: set = set()

        for item in data:
            if not isinstance(item, (list, tuple)):
                logger.debug("跳过格式错误条目: %s", item)
                continue
            item_len = len(item)
            if item_len < 6:
                logger.warning("跳过元素不足条目 (len=%d, 需要6个元素): %s", item_len, item)
                continue
            if not all(isinstance(x, str) and x.strip() for x in item[:5]):
                logger.debug("跳过含空字段条目: %s", item)
                continue

            # 解析 5 元素核心
            head, head_type, rel, tail, tail_type = (x.strip() for x in item[:5])
            # 解析类别（第 6 位，必须）
            if not isinstance(item[5], str) or not item[5].strip():
                logger.warning("跳过缺少类别条目: %s", item)
                continue
            category = item[5].strip()
            if not QuintupleCategory.is_valid(category):
                logger.warning("跳过非法类别: %s (条目: %s)", category, item)
                continue

            # 人称代词主体按当前说话角色自动调整（LLM 未替换时兜底）
            if head == "我":
                # "我" 指向当前说话人：Aliya 链中为 ai_name，user 链中为 user_name
                resolved = speaker or self.user_name
                logger.debug("人称代词主体替换: 我 → %s", resolved)
                head = resolved
                head_type = "人物"
            elif head == "你":
                # "你" 指向对话对象：当前说话人的另一方
                if speaker == self.user_name:
                    resolved = self.ai_name
                elif speaker == self.ai_name:
                    resolved = self.user_name
                else:
                    # 说话人未知或为第三方角色时回退 Aliya（默认对话对象）
                    resolved = self.ai_name
                logger.debug("人称代词主体替换: 你 → %s", resolved)
                head = resolved
                head_type = "人物"
            # 实体类型白名单：未知类型降级为「概念」，不跳过（放宽规则）
            if not _is_valid_entity_type(head_type):
                logger.debug("未知主体类型降级为「概念」: %s(%s)", head, head_type)
                head_type = "概念"
            if not _is_valid_entity_type(tail_type):
                logger.debug("未知客体类型降级为「概念」: %s(%s)", tail, tail_type)
                tail_type = "概念"

            # 超长字段截断（提示词要求提炼简洁短语，防止整段原文作为宾语/主体入库）
            head = _truncate(head)
            rel = _truncate(rel)
            tail = _truncate(tail)
            if not head or not tail:
                continue

            # 完全重复的五元组去重（保持首次出现顺序）
            dedup_key = (head, head_type, rel, tail, tail_type, category)
            if dedup_key in seen:
                logger.debug("跳过重复五元组: %s", dedup_key)
                continue
            seen.add(dedup_key)

            quintuples.append((head, head_type, rel, tail, tail_type))
            categories.append(category)

        return quintuples, categories


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


async def extract_quintuples(text: str) -> Tuple[List[QuintupleType], List[str]]:
    """
    便捷函数：异步提取五元组及其类别

    Args:
        text: 待提取的文本

    Returns:
        (五元组列表, 类别列表)，两者长度相等
    """
    return await get_extractor().extract_async(text)


def extract_quintuples_sync(text: str) -> Tuple[List[QuintupleType], List[str]]:
    """
    便捷函数：同步提取五元组及其类别

    Args:
        text: 待提取的文本

    Returns:
        (五元组列表, 类别列表)，两者长度相等
    """
    return asyncio.run(get_extractor().extract_async(text))


__all__ = [
    "QuintupleExtractor",
    "get_extractor",
    "extract_quintuples",
    "extract_quintuples_sync",
    "QuintupleCategory",
    "QuintupleType",
]
