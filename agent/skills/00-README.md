# Agent Skills

Aliya Agent 的技能配置目录。技能用于指导 LLM 在特定场景下的行为和响应方式。

## 技能文件格式

每个技能文件使用 Markdown 格式，包含 YAML frontmatter 和正文内容：

```markdown
---
name: 技能名称
description: 技能描述（用于触发匹配的关键词）
version: 1.0.0
enabled: true
priority: 10
---

# 技能指令正文

技能的具体指令内容，指导 LLM 如何行为和响应。
```

## 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 技能名称 |
| `description` | string | 是 | 技能描述，包含触发关键词 |
| `version` | string | 否 | 版本号，默认 `1.0.0` |
| `enabled` | boolean | 否 | 是否启用，默认 `true` |
| `priority` | integer | 否 | 优先级（数字越小优先级越高），默认 `100` |

## 技能触发机制

当用户输入中包含技能 `description` 中的任意关键词时，该技能会被激活并注入到 LLM 的上下文中。

关键词匹配规则：
- 不区分大小写
- 按空格分词后进行匹配
- 任意关键词命中即激活

## 现有技能

| 技能 | 描述 | 优先级 |
|------|------|--------|
| 代码助手 | 代码编写、重构、调试 | 10 |
| 学习导师 | 学习教学、概念解释 | 20 |
| 写作助手 | 文案创作、内容编辑 | 30 |

## 添加新技能

1. 在 `agent/skills/` 目录创建 `.md` 文件
2. 添加 YAML frontmatter 和指令正文
3. 技能会自动被 `SkillLoader` 加载

## 参考

- `agent/skill_loader.py` - 技能加载器实现
- `agent/models.py:Skill` - 技能数据模型
