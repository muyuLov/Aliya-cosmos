# 昔涟状态面板（GUI）

轻量 Electron 桌面应用，复刻 `example/Cyrene-Agent-master` 视觉风格的"昔涟"状态面板侧边栏。

## 快速开始

```bash
cd GUI
npm install
npm start
```

## 项目结构

```
GUI/
├── main.js                 # Electron 主进程（无边框 + 透明窗口）
├── preload.js              # 桥接层（contextBridge 暴露受控 API）
├── package.json
└── src/
    ├── index.html          # 状态面板结构
    ├── css/
    │   ├── tokens.css      # 设计令牌（颜色 / 字体 / 间距）
    │   ├── base.css        # 基础重置
    │   └── sidebar.css     # 侧边栏玻璃质感样式
    └── js/
        └── sidebar.js      # 交互逻辑
```

## 功能

- 无边框透明窗口，圆角 24px
- 顶栏：置顶切换、最小化、关闭
- 头像：粉色光晕边框 + 在线徽章
- 状态卡 / 心情卡（带 emoji 映射）
- 正在喂养：显示当前模型
- 操作按钮：打开聊天 / 切换模型 / 设置
- Toast 提示

## 设计参考

视觉系统完全沿用 Cyrene-Agent 的设计 token（粉紫渐变、玻璃质感、大圆角），但实现精简：单一窗口 + 原生 HTML/CSS/JS，无构建链。
