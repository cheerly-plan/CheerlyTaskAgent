import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

// 前端源码在 web/，构建产物直接输出到后端托管目录 src/taskagent/web/。
// 该目录不入仓：`pnpm build` 之后 `uv run taskagent serve` 才能打开界面。
export default defineConfig({
  plugins: [react()],
  base: '/',
  resolve: {
    alias: {'@': path.resolve(__dirname, './src')},
  },
  build: {
    outDir: '../src/taskagent/web',
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
  server: {
    port: 5173,
    strictPort: true,
    // `pnpm dev` 时把 API / SSE 透传给本地后端，避免跨域
    proxy: {
      '/api': {target: 'http://127.0.0.1:8010', changeOrigin: true, ws: false},
    },
  },
});