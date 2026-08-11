<div align="center">

# 《彼方的她-Aliya》

**一个基于 LLM 的 AI 伴侣桌面应用，集成 Live2D 角色展示、语音对话与记忆系统。**

</div>

## 简介

Aliya-cosmos 是一个桌面端 AI 角色交互应用，取材自文字冒险游戏《彼方的她-Aliya》，采用 Electron + Vue 3 构建前端界面，Python 后端提供 LLM 对话、TTS 语音合成、知识图谱记忆等功能。角色以 Live2D 模型呈现在桌面，支持实时口型同步、表情切换，提供沉浸式的交互体验。

## 原作《彼方的她-Aliya》

本应用的角色与世界观设定取材自 **TDGame Studio（瞳电游）** 开发、**Anotherindie** 发行的科幻文字冒险游戏《彼方的她-Aliya》。该作于 **2024 年 4 月 25 日**在 Steam 平台发售，由成都信息工程大学学生创立的瞳电游工作室打造。

游戏以**现实时间同步**为核心机制：玩家通过 COSMOS 终端意外连接上一千年后于太空遇险的少女 Aliya，其回复与事件进程与真实时间完全同步（包括关闭游戏期间）。玩家在同步对话中聆听她的过去、影响她的未来，并逐步揭开背后的真相。

- 销量突破 10 万份，Steam 收获"好评如潮"评价
- 移动端版本于 2025 年 5 月 21 日获国家新闻出版署审批
- 续作《彼方的她-Aliya2 曙光》计划于 2026 年推出

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

- **原作游戏** — [《彼方的她-Aliya》](https://store.steampowered.com/app/2704110/Aliya/?l=schinese)（TDGame Studio 开发，Anotherindie 发行）
- **Live2D 角色** — [darkjungle8](https://space.bilibili.com/41738135)（B站主页）
- **记忆系统** — [RTGS2017 NagaAgent](https://github.com/RTGS2017/NagaAgent)
- **Agent 架构** — [anthropics claude-code](https://github.com/anthropics/claude-code)
- **Agent项目框架 GUI界面参考** — [Playa-0v0 Cyrene-Agent](https://github.com/Playa-0v0/Cyrene-Agent)

## 许可证

[MIT](./LICENSE.txt)
