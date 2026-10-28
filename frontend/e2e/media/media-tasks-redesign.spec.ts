import { test, expect, type Page } from '@playwright/test';
import type { MediaAsset } from '../../src/api/types/generations';
import { fixture } from '../support/studio-fixture';

const image = 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#292929"/><path d="M150 260L280 100L400 260L490 160L570 260" fill="none" stroke="#aaa" stroke-width="8"/></svg>');
const clip = '/e2e/media/fixtures/asset-preview.mp4';
type SampleAsset = MediaAsset & { workspace_mode: 'standard' | 'infinite_canvas' };

async function libraryFixture(page: Page) {
  const state = await fixture(page, true);
  const queries: URL[] = [], reads: string[] = [], writes: string[] = [];
  const assets: SampleAsset[] = Array.from({ length: 60 }, (_, index) => {
    const kind = (['image', 'video', 'audio'] as const)[index % 3];
    return {
      asset_id: String(7001 + index), record_id: String(6001 + index), generation_id: '8001', media_id: String(9901 + index),
      media_type: kind, workspace_mode: index % 2 ? 'infinite_canvas' : 'standard',
      name: '雨夜' + ({ image: '画面', video: '片段', audio: '声音' }[kind]) + ' ' + (index + 1),
      row_version: '1', url: kind === 'image' ? image : clip, width: kind === 'audio' ? null : 640,
      height: kind === 'audio' ? null : 360, duration_ms: kind === 'image' ? null : 2000,
      created_at: new Date(Date.UTC(2026, 9, 28, 12, 0, 60 - index)).toISOString(),
    };
  });
  await page.route('**/api/v1/media-library/items**', async route => {
    const request = route.request(), url = new URL(request.url());
    if (request.method() !== 'GET') {
      writes.push(request.method() + ' ' + url.pathname);
      return route.fulfill({ status: 405, json: {} });
    }
    if (url.pathname.endsWith('/items')) {
      queries.push(url);
      const mode = url.searchParams.get('workspace_mode'), kind = url.searchParams.get('media_type'), name = url.searchParams.get('name');
      const matches = assets.filter(asset => (!mode || asset.workspace_mode === mode) && (!kind || asset.media_type === kind) && (!name || asset.name.includes(name)));
      const offset = Number(url.searchParams.get('offset') ?? 0), limit = Number(url.searchParams.get('limit') ?? 20);
      return route.fulfill({ json: { items: matches.slice(offset, offset + limit), total: matches.length, offset, limit } });
    }
    const id = url.pathname.split('/').pop()!;
    reads.push(id);
    const asset = assets.find(item => item.asset_id === id);
    return route.fulfill(asset ? { json: asset } : { status: 404, json: { error: { code: 'not_found', message: '资产不存在' } } });
  });
  return { ...state, assets, queries, reads, writes };
}

test('assets keep six smaller cards per row across supported desktop viewports', async ({ page }, info) => {
  const state = await libraryFixture(page);
  await page.goto('/media-library');
  await expect(page).toHaveURL('/media-library/all');
  const cards = page.locator('.media-gallery-card');
  await expect(cards).toHaveCount(24);
  await expect(page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name: '任务管理' })).toHaveCount(0);
  await expect(page.getByRole('searchbox', { name: '搜索资产名称' })).toBeVisible();
  for (const [width, height] of [[1280, 720], [1366, 768], [1440, 900], [1920, 1080]]) {
    await page.setViewportSize({ width, height });
    const bounds = await Promise.all((await cards.all()).slice(0, 7).map(card => card.boundingBox()));
    expect(new Set(bounds.slice(0, 6).map(box => Math.round(box!.y))).size).toBe(1);
    expect(bounds[6]!.y).toBeGreaterThan(bounds[0]!.y);
    expect(bounds[0]!.height / bounds[0]!.width).toBeCloseTo(.75, 1);
    const gridWidth = await page.locator('.media-gallery-grid').evaluate(node => node.clientWidth);
    expect(bounds[0]!.width / ((gridWidth - 48) / 4)).toBeLessThan(.72);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({ path: info.outputPath('asset-library-' + width + '.png'), fullPage: true, animations: 'disabled' });
  }
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await cards.first().hover();
  expect(await cards.first().evaluate(node => getComputedStyle(node).transform)).toBe('none');
  expect(state.queries[0].searchParams.get('limit')).toBe('24');
  expect(state.queries[0].searchParams.has('media_type')).toBe(false);
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test('workspace and media buttons combine all twelve server filters with accurate totals', async ({ page }) => {
  const state = await libraryFixture(page);
  await page.goto('/media-library/all');
  for (const [mode, label] of [['all', '生成历史'], ['standard', '创作模式'], ['infinite_canvas', '画布模式']]) {
    await page.getByRole('tab', { name: label, exact: true }).click();
    for (const [kind, text] of [['all', '全部'], ['image', '图片'], ['video', '视频'], ['audio', '音频']]) {
      const button = page.getByRole('group', { name: '按媒体类型筛选' }).getByRole('button', { name: text, exact: true });
      await button.click();
      const matches = state.assets.filter(asset => (mode === 'all' || asset.workspace_mode === mode) && (kind === 'all' || asset.media_type === kind));
      await expect(button).toHaveAttribute('aria-pressed', 'true');
      await expect(page.locator('.media-gallery-count')).toHaveText('共 ' + matches.length + ' 个资产');
      await expect(page.locator('.media-gallery-card')).toHaveCount(Math.min(24, matches.length));
      await expect.poll(() => state.queries.at(-1)?.searchParams.get('workspace_mode')).toBe(mode === 'all' ? null : mode);
      expect(state.queries.at(-1)?.searchParams.get('media_type')).toBe(kind === 'all' ? null : kind);
      await expect(page.locator('.media-gallery-name').first()).toHaveText(matches[0].name);
    }
  }
  expect(state.writes).toEqual([]); expect(state.errors).toEqual([]);
});

test('name search resets pagination and survives media changes, reload, history and clearing', async ({ page }) => {
  const state = await libraryFixture(page);
  await page.goto('/media-library/all?workspace_mode=standard&offset=24');
  await expect(page.locator('.media-gallery-card')).toHaveCount(6);
  const search = page.getByRole('searchbox', { name: '搜索资产名称' });
  await search.fill('  片段  ');
  await expect(page.locator('.media-gallery-card')).toHaveCount(10);
  await expect(page).toHaveURL(url => url.searchParams.get('name') === '片段' && !url.searchParams.has('offset'));
  await page.getByRole('group', { name: '按媒体类型筛选' }).getByRole('button', { name: '视频', exact: true }).click();
  await expect(page).toHaveURL(url => url.pathname === '/media-library/video' && url.searchParams.get('name') === '片段' && url.searchParams.get('workspace_mode') === 'standard');
  await page.reload();
  await expect(search).toHaveValue('片段');
  await expect(page.getByRole('tab', { name: '创作模式' })).toHaveAttribute('aria-selected', 'true');
  await page.goBack();
  await expect(page).toHaveURL(url => url.pathname === '/media-library/all' && url.searchParams.get('name') === '片段');
  await expect(search).toHaveValue('片段');
  await search.fill('不存在的资产');
  await expect(page.getByText('没有符合条件的资产', { exact: true })).toBeVisible();
  await page.locator('.media-gallery-search .ant-input-clear-icon').click();
  await expect(page.locator('.media-gallery-count')).toHaveText('共 30 个资产');
  await expect(search).toHaveValue('');
  await expect(page).toHaveURL(url => !url.searchParams.has('name'));
  expect(state.errors).toEqual([]);
});

test('Chinese composition waits before searching and ignores superseded requests', async ({ page }) => {
  const state = await libraryFixture(page);
  let release: (() => void) | undefined;
  const delayed = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/v1/media-library/items?*', async route => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('name') !== '慢查询') return route.fallback();
    await delayed;
    await route.fulfill({ json: { items: [state.assets[0]], total: 1, offset: 0, limit: 24 } });
  });
  await page.goto('/media-library/all');
  await expect(page.locator('.media-gallery-card')).toHaveCount(24);
  const search = page.getByRole('searchbox', { name: '搜索资产名称' });
  const before = state.queries.length;
  await search.dispatchEvent('compositionstart');
  await search.fill('雨夜');
  await page.waitForTimeout(400);
  expect(state.queries.length).toBe(before);
  await search.dispatchEvent('compositionend');
  await expect.poll(() => state.queries.at(-1)?.searchParams.get('name')).toBe('雨夜');
  await search.fill('慢查询');
  await expect(page).toHaveURL(url => url.searchParams.get('name') === '慢查询');
  await search.fill('声音');
  await expect(page.locator('.media-gallery-card')).toHaveCount(20);
  release!();
  await expect(page.locator('.media-gallery-count')).toHaveText('共 20 个资产');
  await expect(page.locator('.media-gallery-name').first()).toHaveText('雨夜声音 3');
  expect(state.errors).toEqual([]);
});

test('image cards open the canvas-style preview directly and restore keyboard focus', async ({ page }, info) => {
  const state = await libraryFixture(page);
  await page.goto('/media-library/image');
  const card = page.locator('.media-gallery-card').first();
  await card.focus(); await page.keyboard.press('Enter');
  await expect(page.locator('.ant-image-preview')).toBeVisible();
  await expect(page.locator('.ant-image-preview-img')).toHaveAttribute('alt', '雨夜画面 1');
  await expect(page.locator('.ant-drawer')).toHaveCount(0);
  await expect(page.getByRole('textbox', { name: '资产名称', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '指定创作目标' })).toHaveCount(0);
  await page.locator('.ant-image-preview-operations .anticon-zoom-in').click();
  await page.screenshot({ path: info.outputPath('asset-image-preview.png'), animations: 'disabled' });
  await page.keyboard.press('Escape');
  await expect(page.locator('.ant-image-preview')).toHaveCount(0);
  await expect(card).toBeFocused();
  await expect(page).toHaveURL(url => !url.searchParams.has('asset'));
  expect(state.reads).toEqual(['7001']);
  expect(state.writes).toEqual([]); expect(state.errors).toEqual([]);
});

for (const kind of ['video', 'audio'] as const) {
  test(kind + ' previews play without editing actions and stop when closed', async ({ page }, info) => {
    const state = await libraryFixture(page);
    await page.goto('/media-library/' + kind);
    const card = page.locator('.media-gallery-card').first();
    await card.focus(); await page.keyboard.press('Enter');
    const modal = page.getByRole('dialog', { name: kind === 'video' ? '视频预览' : '音频预览', exact: true });
    await expect(modal).toBeVisible();
    const media = modal.locator(kind), handle = (await media.elementHandle())!;
    await expect.poll(() => media.evaluate(node => (node as HTMLMediaElement).readyState)).toBeGreaterThanOrEqual(2);
    await media.evaluate(node => (node as HTMLMediaElement).play());
    await expect.poll(() => media.evaluate(node => (node as HTMLMediaElement).currentTime)).toBeGreaterThan(0);
    if (kind === 'video') expect((await media.boundingBox())!.width).toBeGreaterThan(600);
    await expect(modal.getByRole('button', { name: /采用|编辑|保存|任务/ })).toHaveCount(0);
    await page.screenshot({ path: info.outputPath('asset-' + kind + '-preview.png'), animations: 'disabled' });
    await page.keyboard.press('Escape');
    await expect(modal).toHaveCount(0);
    expect(await handle.evaluate(node => (node as HTMLMediaElement).paused)).toBe(true);
    expect(await handle.getAttribute('src')).toBe(null);
    await expect(card).toBeFocused();
    expect(state.writes).toEqual([]); expect(state.errors).toEqual([]);
  });
}

test('expired preview links are refreshed through detail reads and missing assets can close', async ({ page }) => {
  const state = await libraryFixture(page);
  let reads = 0;
  await page.route('**/expired-asset.png', route => route.fulfill({ status: 404, body: '' }));
  await page.route('**/api/v1/media-library/items/7001', async route => {
    reads += 1;
    await route.fulfill({ json: { ...state.assets[0], url: reads === 1 ? '/expired-asset.png' : image } });
  });
  await page.goto('/media-library/image');
  await page.locator('.media-gallery-card').first().click();
  await expect(page.getByText('预览暂时无法加载，链接可能已过期。', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '重新加载', exact: true }).click();
  await expect(page.locator('.ant-image-preview-img')).toHaveAttribute('src', image);
  expect(reads).toBe(2);
  await page.keyboard.press('Escape');
  await page.goto('/media-library/all?asset=99999');
  await expect(page.getByRole('dialog', { name: '资产预览', exact: true })).toBeVisible();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect.poll(() => page.getByRole('dialog', { name: '资产预览', exact: true }).evaluate(node => node.contains(document.activeElement))).toBe(true);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: '资产预览', exact: true })).toHaveCount(0);
  expect(state.writes).toEqual([]); expect(state.errors).toEqual([]);
});

test('loading, empty and failed lists keep filters and provide a retry', async ({ page }) => {
  const state = await libraryFixture(page);
  let mode: 'loading' | 'empty' | 'error' | 'ready' = 'loading';
  let release: (() => void) | undefined;
  const loaded = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/v1/media-library/items?*', async route => {
    if (mode === 'loading') await loaded;
    if (mode === 'error') return route.fulfill({ status: 503, json: { error: { code: 'UNAVAILABLE', message: '资产服务暂不可用' } } });
    const items = mode === 'empty' ? [] : state.assets.slice(0, 24);
    await route.fulfill({ json: { items, total: items.length, offset: 0, limit: 24 } });
  });
  await page.goto('/media-library/all');
  await expect(page.getByRole('status', { name: '正在加载资产', exact: true })).toBeVisible();
  mode = 'empty'; release!();
  await expect(page.getByText('还没有资产，在工作台生成图片、视频或音频后会显示在这里', { exact: true })).toBeVisible();
  mode = 'error'; await page.reload();
  await expect(page.getByText('服务暂时不可用，请稍后重试。', { exact: true })).toBeVisible();
  mode = 'ready'; await page.getByRole('button', { name: '重新加载', exact: true }).click();
  await expect(page.locator('.media-gallery-card')).toHaveCount(24);
  expect(state.errors).toEqual([]);
});

test('retired task URLs return to projects without querying or exposing the task manager', async ({ page }) => {
  const state = await libraryFixture(page);
  for (const path of ['/tasks', '/tasks/text?task=8001', '/tasks/image', '/tasks/video?batch=6001', '/tasks/audio', '/tasks/old/deep-link']) {
    await page.goto(path);
    await expect(page).toHaveURL('/projects');
    await expect(page.getByRole('link', { name: '任务管理', exact: true })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: '任务管理', exact: true })).toHaveCount(0);
  }
  expect(state.requests.filter(request => request.path === '/ai/generations')).toEqual([]);
  expect(state.errors).toEqual([]);
});
