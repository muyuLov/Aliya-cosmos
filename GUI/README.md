# Aliya状态面板（GUI）

轻量 Electron 桌面应用，粉紫渐变玻璃质感风格的"Aliya"角色状态侧边栏面板。

基于 **Vue 3 + Vite** 构建，与后端 Agent 服务通过 WebSocket 通信，实时展示角色心情、Token 用量等信息。

## 快速开始

```bash
cd GUI
npm install
npm start          # 构建 + 启动
npm run dev        # 构建 + 开发模式启动
npm run build      # 仅构建，不启动
```

## 功能

- 无边框透明窗口，圆角 24px
- 顶栏：置顶切换、最小化、关闭
- 头像：粉色光晕边框 + 在线徽章
- 状态卡 / 心情卡（带 emoji 映射），心情从 Agent WebSocket 实时推送
- 当前模型展示 + 模型选择浮层
- 操作按钮：打开聊天 / 切换模型 / 设置
- Token 用量统计（实时更新）
- Toast 提示反馈
- 独立透明 Live2D 窗口（鼠标跟随 + TTS 口型同步 + 情绪调制）

## 项目结构

```
GUI/
├── main.js                   # Electron 主进程入口（转发到 main/）
├── main/                     # 主进程模块（按职责拆分）
│   ├── index.js              # App 生命周期 / 组装
│   ├── state.js              # 跨模块共享状态
│   ├── logger.js             # 日志系统（控制台编码检测 + 文件轮转）
│   ├── config.js             # 模型/身份配置读取（YAML/JSON）
│   ├── windows.js            # 窗口创建与管理（侧边栏 / Live2D）
│   ├── tray.js               # 系统托盘
│   ├── notifications.js      # 桌面通知
│   ├── state-push.js         # Token 追踪 + 状态快照批量推送
│   ├── ws.js                 # Agent WebSocket 客户端（自动重连）
│   └── ipc.js                # IPC 处理器注册
├── preload.js                # contextBridge 桥接层（状态面板）
├── preload-live2d.js         # contextBridge 桥接层（Live2D 窗口）
├── sidebar.html              # 状态面板入口 HTML
├── live2d.html               # Live2D 窗口入口 HTML
├── vite.config.js            # Vite 构建配置（多入口）
├── package.json
├── src/                      # 渲染层
│   ├── sidebar.js            # 状态面板 Vue 入口（Pinia）
│   ├── live2d/               # Live2D 渲染模块
│   │   ├── index.js          # 引擎初始化 / 焦点跟随 / 工具栏
│   │   ├── constants.js      # 布局/物理/口型同步可调参数
│   │   └── lip-sync.js       # 口型同步引擎（音频特征 → 面部参数）
│   ├── App.vue               # 根组件
│   ├── components/           # Vue 单文件组件
│   │   ├── TopBar.vue        # 顶栏 + 窗口按钮
│   │   ├── AvatarCard.vue    # 头像 + 在线状态
│   │   ├── IndicatorCard.vue # 状态/心情指示卡（variant 区分）
│   │   ├── ModelCard.vue     # 模型信息 + 操作按钮
│   │   ├── ModelSelector.vue # Provider 选择浮层
│   │   ├── SettingsCard.vue  # 设置入口
│   │   ├── TokenFooter.vue   # 版本号 + Token 统计
│   │   └── ToastNotification.vue
│   ├── stores/
│   │   └── appStore.js       # Pinia 统一状态 Store
│   ├── constants/
│   │   └── mappings.js       # 状态/心情 → Emoji 映射
│   ├── utils/
│   │   └── formatters.js     # Token/模型名格式化
│   └── styles/
│       ├── tokens.css        # 设计令牌
│       ├── base.css          # 基础重置 + 共享卡片/按钮样式
│       └── live2d.css        # Live2D 窗口样式
├── static/                   # Live2D Cubism Core 库
├── dist/                     # Vite 构建产物
└── data/                     # 项目根 data/ 下的运行时日志（data/logs）
```

## 架构说明

- **主进程（`main/`）**：按职责拆分为独立模块，`state.js` 集中管理跨模块共享状态（窗口引用/置顶/缩放/Token/WS），避免模块间循环依赖；`main.js` 仅作入口转发
- **渲染层**：Vue 3 SFC 组件化架构 + Pinia 统一状态管理（`stores/appStore.js`），单一数据源，组件共享读写
- **通信层**：`preload.js` 经 `contextBridge` 暴露受控 IPC；情绪/状态/Token 由主进程合并为 50ms 批量快照（`sidebar:state-snapshot`）单通道推送，降低渲染进程唤醒频率
- **实时数据**：主进程通过 WebSocket 连接后端 Agent（`main/ws.js`，断线 5s 自动重连），解析后批量推送心情和 Token 更新到渲染层
- **Live2D 窗口**：独立透明窗口，口型同步引擎（`src/live2d/lip-sync.js`）接收 TTS 音频特征（volume/centroid/zcr）驱动面部参数，支持空闲 20fps / 活跃 60fps 帧率切换
- **构建工具**：Vite 5 负责打包（对齐 Electron 内置 Chromium 的 `target`，`manualChunks` 拆分 vue/pixi 依赖），多入口产出 `dist/` 目录，Electron 通过 `loadFile` 加载

## 设计参考

视觉系统沿用 Cyrene-Agent 的设计令牌（粉紫渐变、玻璃质感、大圆角），实现为 CSS 自定义属性（`--rb-*` 系列），所有组件基于这些令牌构建。
