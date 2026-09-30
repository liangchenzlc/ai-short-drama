import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  base: '/',
  server: { port: 8080, strictPort: true, watch: { ignored: ['**/.runtime/**'] }, proxy: {
    '/api': { target: loadEnv(mode, '.', '').API_PROXY_TARGET || 'http://127.0.0.1:8000', changeOrigin: true },
  } },
  build: { rollupOptions: { output: { onlyExplicitManualChunks: true, manualChunks(id) {
    if (/node_modules[\\/](react|react-dom|react-router|react-router-dom|scheduler)[\\/]/.test(id)) return 'react';
    if (/node_modules[\\/](antd[\\/]es[\\/]table|rc-table)[\\/]/.test(id)) return 'data-table';
    if (/node_modules[\\/](antd[\\/]es[\\/]drawer|rc-drawer)[\\/]/.test(id)) return 'drawers';
    if (/node_modules[\\/](@xzdarcy|react-virtualized)[\\/]/.test(id)) return 'timeline';
  } } } },
}));
