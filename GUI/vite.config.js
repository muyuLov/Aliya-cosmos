import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import path from 'path';

export default defineConfig({
  plugins: [
    vue(),
    // Electron file:// 协议下移除 crossorigin 属性
    {
      name: 'remove-crossorigin',
      transformIndexHtml(html) {
        return html.replace(/\bcrossorigin\b(=[^\s>]*)?/g, '');
      },
    },
  ],
  root: __dirname,
  base: './',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
});
