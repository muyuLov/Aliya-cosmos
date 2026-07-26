# 昔涟状态面板（GUI）

轻量 Electron 桌面应用，粉紫渐变玻璃质感风格的"昔涟"角色状态侧边栏面板。

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

## 项目结构

```
GUI/
├── main.js                  # Electron 主进程
├── preload.js               # contextBridge 桥接层
├── index.html               # Vite 入口 HTML
├── vite.config.js           # Vite 构建配置
├── package.json
├── public/
│   └── avatar.png           # 角色头像
├── src/
│   ├── main.js              # Vue 应用入口
│   ├── App.vue              # 根组件
│   ├── components/          # Vue 单文件组件
│   │   ├── TopBar.vue       # 顶栏 + 窗口按钮
│   │   ├── AvatarCard.vue   # 头像 + 在线状态
│   │   ├── StatusCard.vue   # 状态指示卡
│   │   ├── MoodCard.vue     # 心情指示卡
│   │   ├── ModelCard.vue    # 模型信息 + 操作按钮
│   │   ├── ModelSelector.vue
│   │   ├── SettingsCard.vue
│   │   ├── TokenFooter.vue  # 版本号 + Token 统计
│   │   └── ToastNotification.vue
│   ├── composables/
│   │   └── useSidebarAPI.js # preload API 封装
│   ├── constants/
│   │   └── mappings.js      # 状态/心情 → Emoji 映射
│   ├── utils/
│   │   └── formatters.js    # Token/模型名格式化
│   └── styles/
│       ├── tokens.css       # 设计令牌
│       └── base.css         # 基础重置
├── static/                  # Live2D 资源
├── dist/                    # Vite 构建产物
└── logs/                    # 运行时日志
```

## 架构说明

- **渲染层**：Vue 3 SFC 组件化架构，CSS 使用 `scoped` 实现样式隔离
- **通信层**：`composables/useSidebarAPI.js` 封装 Electron preload 桥接，通过 IPC 与主进程交互
- **实时数据**：主进程通过 WebSocket 连接后端 Agent，推送心情和 Token 更新到渲染层
- **构建工具**：Vite 5 负责打包，产出 `dist/` 目录，Electron 通过 `loadFile` 加载

## 设计参考

视觉系统沿用 Cyrene-Agent 的设计令牌（粉紫渐变、玻璃质感、大圆角），实现为 CSS 自定义属性（`--rb-*` 系列），所有组件基于这些令牌构建。
