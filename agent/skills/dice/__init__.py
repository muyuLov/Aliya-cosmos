"""示例 Skill：掷骰子。"""

from __future__ import annotations

import random

from agent.tools.base import ToolContext, ToolDefinition


definition = ToolDefinition(
    id="roll_dice",
    name="roll_dice",
    description="掷骰子游戏：当用户想掷骰、抽奖或做随机数决定时调用。",
    input_schema={
        "type": "object",
        "properties": {
            "sides": {"type": "integer", "description": "面数，默认 6", "default": 6},
            "count": {"type": "integer", "description": "骰子数，默认 1", "default": 1},
        },
        "required": [],
    },
    enabled=True,
)


async def execute(_ctx: ToolContext, args: dict) -> str:
    # _ctx 真实字段：user_query / conversation_id / memory
    sides = int(args.get("sides", 6))
    count = int(args.get("count", 1))
    rolls = [random.randint(1, sides) for _ in range(max(1, count))]
    return f"掷出：{rolls}（合计 {sum(rolls)}）"
