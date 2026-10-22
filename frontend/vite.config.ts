import { statSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig, loadEnv, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';

const root = dirname(fileURLToPath(import.meta.url));
const canvasPreview: Plugin = {
  name: 'canvas-preview-entry',
  configurePreviewServer(server) {
    server.middlewares.use((request, response, next) => {
      const url = new URL(request.url ?? '/', 'http://localhost');
      if (request.method !== 'GET' && request.method !== 'HEAD') return next();
      if (url.pathname === '/canvas-app') {
        response.writeHead(302, { Location: `/canvas-app/${url.search}` });
        response.end();
        return;
      }
      const accept = request.headers.accept;
      const acceptsHtml = !accept || accept.includes('text/html') || accept.includes('*/*');
      const file = resolve(server.config.root, server.config.build.outDir, `.${url.pathname}`);
      if (url.pathname.startsWith('/canvas-app/') && acceptsHtml
        && !statSync(file, { throwIfNoEntry: false })?.isFile()) {
        request.url = `/canvas-app/index.html${url.search}`;
      }
      next();
    });
  },
};

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, root, '');
  const canvasPort = Number(env.CANVAS_DEV_PORT || 8082);
  if (!Number.isInteger(canvasPort) || canvasPort < 1 || canvasPort > 65535) {
    throw new Error('CANVAS_DEV_PORT 必须为 1–65535 的整数。');
  }
  const apiProxy = { '/api': { target: env.API_PROXY_TARGET || 'http://127.0.0.1:8000', changeOrigin: true } };
  return {
    plugins: [react(), canvasPreview],
    base: '/',
    server: { port: 8080, strictPort: true, watch: { ignored: ['**/.runtime/**', '**/canvas/**'] }, proxy: {
      '/canvas-app': { target: `http://127.0.0.1:${canvasPort}`, changeOrigin: true, ws: true },
      ...apiProxy,
    } },
    preview: { proxy: apiProxy },
    build: { rollupOptions: { output: { onlyExplicitManualChunks: true, manualChunks(id) {
      if (/node_modules[\\/](react|react-dom|react-router|react-router-dom|scheduler)[\\/]/.test(id)) return 'react';
      if (/node_modules[\\/](antd[\\/]es[\\/]table|rc-table)[\\/]/.test(id)) return 'data-table';
      if (/node_modules[\\/](antd[\\/]es[\\/]drawer|rc-drawer)[\\/]/.test(id)) return 'drawers';
      if (/node_modules[\\/](@xzdarcy|react-virtualized)[\\/]/.test(id)) return 'timeline';
    } } } },
  };
});
