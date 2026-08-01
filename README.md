<div align="center">

# 《彼方的她-Aliya》

**一个基于 LLM 的 AI 伴侣桌面应用，集成 Live2D 角色展示、语音对话与记忆系统。**

</div>

## 简介

Aliya 是一个桌面端 AI 角色交互应用，采用 Electron + Vue 3 构建前端界面，Python 后端提供 LLM 对话、TTS 语音合成、知识图谱记忆等功能。角色以 Live2D 模型呈现在桌面，支持实时口型同步、表情切换，提供沉浸式的交互体验。

## 功能

- **Live2D 角色** — 桌面 Live2D 角色展示，支持表情切换、口型同步
- **LLM 对话** — 基于大语言模型的智能对话，支持多模型切换
- **TTS 语音** — 语音合成（Edge TTS），实现角色发声
- **记忆系统** — Neo4j 图数据库记忆，支持长期记忆与知识关联
- **实时状态** — 角色心情、Token 用量实时展示
- **系统托盘** — 后台常驻运行，托盘快捷操作

## 快速开始

### 后端

```bash
uv sync
uv run python main.py
```

### 前端

```bash
cd GUI
npm install
npm run dev
```

## 致谢

- **Live2D 角色** — [darkjungle8](https://space.bilibili.com/41738135)（B站主页），感谢提供的精美 Live2D 模型
- **记忆系统** — [RTGS2017 NagaAgent](https://github.com/RTGS2017/NagaAgent)，感谢提供的记忆系统参考
- **Agent 架构** — [anthropics claude-code](https://github.com/anthropics/claude-code)，感谢提供的 Agent 实现参考
- **项目框架参考** — [Playa-0v0 Cyrene-Agent](https://github.com/Playa-0v0/Cyrene-Agent)

## 许可证

[MIT](./LICENSE.txt)
