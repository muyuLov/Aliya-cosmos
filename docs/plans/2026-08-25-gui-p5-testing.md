# GUI P5 测试联调 Implementation Plan

> **提交策略：** 本计划中所有 `git commit` 步骤均**跳过**（用户要求：不提交 git、不推送 GitHub）。任务完成标准不变。

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 全量测试通过、生产构建产物可运行、四窗口全链路联调验证（对话/情绪/音频/会话），并补充 GUI 使用文档。

**Architecture:** 本阶段无新功能，专注验证与收尾：单测全量、生产构建、手动联调清单、README。

**Tech Stack:** vitest、vite build、electron。

**前置条件:** P0-P4 完成；`npm run typecheck` 0 错误。

---

### Task 1: 全量单元测试

**Files:**
- 无新增；运行验证并修复

**Step 1: 运行全部单测**

Run: `cd GUI && npm test`
Expected: 所有 `src/**/*.test.ts` 通过（config / backend / ws / state / emotion-map 等）。

**Step 2: 修复失败用例**

如有失败：
- 协议映射字段与后端实际不一致 → 修正 `ws.ts` 的 `mapProtocolEvent`
- `config.ts` 读取 yaml 边缘情况 → 补正则处理

**Step 3: 提交**

```bash
git add GUI/src
git commit -m "test(gui): all unit tests pass"
```

---

### Task 2: 生产构建与启动验证

**Files:**
- 无新增；运行验证

**Step 1: 生产构建**

Run: `cd GUI && npm run build`
Expected: `dist/main`（main+preload 的 CommonJS）+ `dist/renderer`（4 个 HTML + 资源）生成完整。

**Step 2: 生产模式启动**

Run: `cd GUI && npm start`
Expected:
- 加载本地 `dist/renderer/*`（非 dev server）
- 自动拉起后端、WS 连接、四窗口全部可见
- 桌宠模型、双主题、贴图路径均正常（Vite `base:"./"` 相对路径在 `file://` 下生效）

**Step 3: 提交（如有修复）**

```bash
git add GUI
git commit -m "fix(gui): production build fixes"
```

---

### Task 3: 全链路手动联调清单

**Files:**
- 无新增；按清单逐项验证

**Step 1: 对话闭环**

1. 聊天窗口发送"你好" → 气泡流式输出
2. 触发工具调用 → 工具步骤卡片展示 → 需要确认时弹确认卡 → 点允许/拒绝 → 后端继续
3. 点击停止 → `stop` 生效
4. 侧栏会话列表 → 新建/切换/删除 → 与后端 `session_*` 同步

**Step 2: 情绪与音频**

1. 后端 `emotion_changed` → 侧栏状态/心情更新 + 桌宠表情切换
2. 开启 TTS → 音频帧 → 聊天/桌宠出声 + 嘴型跟随
3. 断网/停后端 → 连接徽章转灰 + 重连提示；后端恢复 → 自动重连

**Step 3: 设置与主题**

1. 设置面板仅显示保留项；切换 LLM provider 后 `LLMProviders.json` 更新
2. 外观切换深色/浅色 → 四窗口同步
3. 后端"自动拉起"开关切换 → 手动模式仅连接（需先手动启动后端验证）

**Step 4: 提交（修复项）**

```bash
git add GUI/src
git commit -m "fix(gui): e2e regression fixes"
```

---

### Task 4: GUI 使用文档与收尾

**Files:**
- Create: `GUI/README.md`

**Step 1: 编写 README.md**

```markdown
# Aliya GUI

Aliya 桌面伴侣（Cyrene-Agent UI 复刻版）。原生 TypeScript + Electron + Live2D。

## 环境要求
- Node.js ≥ 24、npm ≥ 10
- Python 3.11+（后端，GUI 默认自动拉起）

## 开发
\`\`\`bash
npm install
npm run dev    # Vite dev server + electron
\`\`\`

## 构建与运行
\`\`\`bash
npm run build
npm start
\`\`\`

## 测试
\`\`\`bash
npm test
npm run typecheck
\`\`\`

## 配置
- 后端配置：仓库根 \`data/config/main.yml\`、\`LLMProviders.json\`
- GUI 偏好（主题/字体/后端启动模式）：\`%APPDATA%/gui-preferences.json\`
- WS 地址：默认 \`ws://127.0.0.1:8765/agent/ws\`（GUI 偏好可改）

## 窗口
- 聊天（\`chat/\`）：对话、工具确认、会话管理、模式过滤
- 状态侧栏（\`sidebar/\`）：情绪/在线状态/模型信息
- 设置（\`settings/\`）：LLM/TTS/外观/后端
- Live2D 桌宠（\`index.html\`）：阿库露模型，情绪驱动表情、TTS 驱动嘴型

## 未接入（后续）
- 通话 / 任务 / 贴纸窗口：后端补齐 ASR、todo 工具、贴纸语义匹配后接入
- 聊天"风格"下拉：后端 ContextBuilder 多风格注入后生效
```

**Step 2: 提交**

```bash
git add GUI/README.md
git commit -m "docs(gui): add README"
```

---

### Task 5: 收尾检查

**Files:**
- 无新增；最终验证

**Step 1: 最终验证**

Run: `cd GUI && npm run typecheck && npm test && npm run build`
Expected: 全部通过。

**Step 2: 汇总交付**

- 更新 `docs/plans/2026-08-25-cyrene-ui-replica-design.md` 的"实施状态"（若已实现）
- 提交全部变更

---

## 完成标准

- [ ] `npm test` 全量通过
- [ ] `npm run build` + `npm start` 生产模式四窗口正常
- [ ] 对话/工具确认/停止/会话管理联调通过
- [ ] 情绪→侧栏+桌宠、TTS→嘴型联调通过
- [ ] 双主题、设置读写、后端拉起开关验证通过
- [ ] `GUI/README.md` 就绪
- [ ] 各 Task 均有独立 commit
