import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { dirname, extname, posix, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { PluginContext } from 'rollup';
import { brandAssetMime, loadBrandConfig } from './config.ts';

export { loadBrandConfig } from './config.ts';

const defaultConfig = fileURLToPath(new URL('../../backend/src/short_drama/core/branding/brand.json', import.meta.url));
const virtualId = '\0virtual:brand';
const assetKeys = ['mark', 'lockup', 'icon'] as const;

function escapeHtml(value: string): string {
  return value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}

export function brandPlugin(surface: 'app' | 'canvas', configPath = defaultConfig) {
  let config: { base: string; command: 'build' | 'serve'; build: { assetsDir: string } };
  let brand: ReturnType<typeof loadBrandConfig>;
  let assets: { key: typeof assetKeys[number]; path: string; fileName: string; url: string; mime: string; source: Buffer }[];
  return {
    name: 'miniart-brand',
    configResolved(resolved: typeof config) {
      config = resolved;
      brand = loadBrandConfig(configPath);
      assets = assetKeys.map(key => {
        const path = resolve(dirname(configPath), brand.assets[key]);
        const source = readFileSync(path);
        const extension = extname(path);
        const hash = createHash('sha256').update(source).digest('hex').slice(0, 12);
        const fileName = posix.join(config.build.assetsDir, 'branding', `${key}-${hash}${extension}`);
        const url = `${config.base}${fileName.split('/').map(encodeURIComponent).join('/')}`;
        return { key, path, source, fileName, url, mime: brandAssetMime(path) };
      });
      brand = { ...brand, assets: { mark: assets[0].url, lockup: assets[1].url, icon: assets[2].url } };
    },
    resolveId(id: string) {
      if (id === 'virtual:brand') return virtualId;
    },
    load(id: string) {
      if (id === virtualId) return `export const brand = ${JSON.stringify(brand)};`;
    },
    buildStart(this: Pick<PluginContext, 'addWatchFile' | 'emitFile'>) {
      this.addWatchFile(configPath);
      for (const asset of assets) {
        this.addWatchFile(asset.path);
        if (config.command === 'build') this.emitFile({ type: 'asset', fileName: asset.fileName, source: asset.source });
      }
    },
    configureServer(server: { middlewares: { use(handler: (request: IncomingMessage, response: ServerResponse, next: () => void) => void): unknown } }) {
      server.middlewares.use((request, response, next) => {
        if (request.method !== 'GET' && request.method !== 'HEAD') return next();
        const pathname = new URL(request.url ?? '/', 'http://localhost').pathname;
        const asset = assets.find(item => item.url === pathname);
        if (!asset) return next();
        response.writeHead(200, { 'Content-Type': asset.mime, 'Content-Length': asset.source.length });
        response.end(request.method === 'HEAD' ? undefined : asset.source);
      });
    },
    transformIndexHtml: {
      order: 'post' as const,
      handler(html: string) {
        const icon = assets.find(asset => asset.key === 'icon')!;
        const tags = `<title>${escapeHtml(brand.titles[surface])}</title>\n`
          + `<meta name="description" content="${escapeHtml(brand.descriptions[surface])}" />\n`
          + `<link rel="icon" type="${icon.mime}" href="${escapeHtml(icon.url)}" />\n`;
        return html.replace('</head>', () => `${tags}</head>`);
      },
    },
  };
}
