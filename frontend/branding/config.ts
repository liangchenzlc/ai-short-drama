import { readFileSync, statSync } from 'node:fs';
import { dirname, extname, resolve } from 'node:path';
import type { BrandConfig } from '../src/types/shared/branding.ts';

const imageTypes: Record<string, string> = {
  '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.gif': 'image/gif',
  '.ico': 'image/x-icon', '.avif': 'image/avif',
};

export function brandAssetMime(path: string): string {
  const mime = imageTypes[extname(path).toLowerCase()];
  if (!mime) throw new Error(`品牌资源不支持的图片类型：${path}`);
  return mime;
}

function record(value: unknown, keys: string[], label: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`品牌配置 ${label} 必须是对象。`);
  }
  const result = value as Record<string, unknown>;
  if (Object.keys(result).length !== keys.length || keys.some(key => !Object.hasOwn(result, key))) {
    throw new Error(`品牌配置 ${label} 必须且只能包含：${keys.join(', ')}。`);
  }
  return result;
}

function text(value: unknown, label: string, name?: string): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`品牌配置 ${label} 必须为非空字符串。`);
  const trimmed = value.trim();
  if (name !== undefined && /[{}]/.test(trimmed.replaceAll('{name}', ''))) {
    throw new Error(`品牌配置 ${label} 包含不支持的模板，仅允许 {name}。`);
  }
  return name === undefined ? trimmed : trimmed.replaceAll('{name}', () => name);
}

function asset(value: unknown, label: string, root: string): string {
  const path = text(value, `assets.${label}`);
  const absolute = resolve(root, path);
  if (/[\\:]/.test(path) || path.split('/').some(segment => !segment || segment === '.' || segment === '..')) {
    throw new Error(`品牌资源 ${label} 必须是配置目录内的相对路径。`);
  }
  brandAssetMime(path);
  if (!statSync(absolute, { throwIfNoEntry: false })?.isFile()) throw new Error(`品牌资源不存在：${path}`);
  return path;
}

export function loadBrandConfig(path: string): BrandConfig {
  let parsed: unknown;
  try {
    parsed = JSON.parse(readFileSync(path, 'utf8'));
  } catch (error) {
    throw new Error(`品牌配置无法读取：${path}`, { cause: error });
  }
  const config = record(parsed, ['name', 'tagline', 'titles', 'descriptions', 'assets'], '根对象');
  const titles = record(config.titles, ['app', 'canvas'], 'titles');
  const descriptions = record(config.descriptions, ['app', 'canvas'], 'descriptions');
  const assets = record(config.assets, ['mark', 'lockup', 'icon'], 'assets');
  const name = text(config.name, 'name');
  const root = dirname(path);
  return {
    name, tagline: text(config.tagline, 'tagline'),
    titles: { app: text(titles.app, 'titles.app', name), canvas: text(titles.canvas, 'titles.canvas', name) },
    descriptions: {
      app: text(descriptions.app, 'descriptions.app', name),
      canvas: text(descriptions.canvas, 'descriptions.canvas', name),
    },
    assets: { mark: asset(assets.mark, 'mark', root), lockup: asset(assets.lockup, 'lockup', root), icon: asset(assets.icon, 'icon', root) },
  };
}
