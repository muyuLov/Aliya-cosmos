import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import path from 'path';
import fs from 'fs';

const pkg = JSON.parse(fs.readFileSync('./package.json', 'utf-8'));

export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  plugins: [
    vue(),
    // Electron file:// 协议下移除 crossorigin 属性
    {
      name: 'remove-crossorigin',
      transformIndexHtml(html) {
        return html.replace(/\bcrossorigin\b(=[^\s>]*)?/g, '');
      },
    },

    // 把 src/assets/live2d/ 中的 Live2D 模型文件（model3.json + 相对引用的 moc3/纹理等）
    // 原样复制到 dist/assets/live2d/，使 pixi-live2d-display 能按相对路径 fetch
    // 同时把 static/lib/live2dcubismcore.min.js 复制到 dist/lib/（Cubism 4 Core 全局对象）
    {
      name: 'copy-live2d-assets',
      apply: 'build',
      closeBundle() {
        const modelSrc = path.resolve(__dirname, 'src/assets/live2d');
        const modelDst = path.resolve(__dirname, 'dist/assets/live2d');
        if (fs.existsSync(modelSrc)) {
          fs.cpSync(modelSrc, modelDst, { recursive: true });
        }
        const libSrc = path.resolve(__dirname, 'static/lib');
        const libDst = path.resolve(__dirname, 'dist/lib');
        if (fs.existsSync(libSrc)) {
          fs.cpSync(libSrc, libDst, { recursive: true });
        }
      },
    },
  ],
  root: __dirname,
  base: './',
  // 多入口：状态面板（sidebar.html）+ Live2D 透明窗口（live2d.html）+ 设置窗口（settings.html）+ 聊天窗口（chat.html）
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // 对齐 Electron 31 内置 Chromium 126，避免冗余转译（更小的产物、更快的构建）
    target: 'chrome126',
    reportCompressedSize: false,
    // pixi.js 全量打包体积固有较大（pixi-live2d-display 依赖完整 PIXI 对象），提高告警阈值
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      input: {
        sidebar: path.resolve(__dirname, 'sidebar.html'),
        live2d: path.resolve(__dirname, 'live2d.html'),
        settings: path.resolve(__dirname, 'settings.html'),
        chat: path.resolve(__dirname, 'chat.html'),
      },
      output: {
        // 依赖拆分：vue/pinia 与 pixi/live2d 各自独立 chunk，便于缓存与并行加载
        manualChunks(id) {
          if (
            id.includes('node_modules/pixi.js') ||
            id.includes('node_modules/pixi-live2d-display')
          ) {
            return 'pixi-vendor';
          }
          // vue/pinia/@vue 与 naive-ui 合并为一个 chunk：
          // naive-ui 运行时依赖 vue，单独拆分会产生循环 chunk 警告
          if (
            id.includes('node_modules/vue') ||
            id.includes('node_modules/pinia') ||
            id.includes('node_modules/@vue') ||
            id.includes('node_modules/naive-ui')
          ) {
            return 'vue-vendor';
          }
        },
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
});
