import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { basename, join } from 'node:path';
import { gzipSync } from 'node:zlib';

export const origin = process.env.PLAYWRIGHT_PRODUCTION_URL ?? 'http://127.0.0.1:4180';
export const latencyMs = Number(process.env.PERFORMANCE_API_LATENCY_MS ?? 250);
if (!Number.isFinite(latencyMs) || latencyMs < 0 || latencyMs > 10000) throw new Error('PERFORMANCE_API_LATENCY_MS 必须为 0–10000。');
const phase = (process.env.PERFORMANCE_PHASE ?? 'after').replace(/[^a-z0-9_-]/gi, '-');
const directory = join('.runtime', 'performance', phase);

export function assetSizes(scripts: string[]) {
  return [...new Set(scripts)].map(url => {
    const buffer = readFileSync(join('dist', 'assets', basename(url)));
    return { url, rawBytes: buffer.length, gzipBytes: gzipSync(buffer).length };
  });
}

export function record(name: string, value: object, scripts: string[]) {
  const assets = assetSizes(scripts);
  mkdirSync(directory, { recursive: true });
  writeFileSync(join(directory, `${name}.json`), JSON.stringify({
    ...value, phase, measuredAt: new Date().toISOString(), assets,
    rawBytes: assets.reduce((sum, item) => sum + item.rawBytes, 0),
    gzipBytes: assets.reduce((sum, item) => sum + item.gzipBytes, 0),
    note: '本机生产构建与 API 测试替身；gzip 为本地计算值，不是实际传输字节。',
  }, null, 2));
}
