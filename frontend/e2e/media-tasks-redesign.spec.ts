import { test, expect } from '@playwright/test';
import { fixture, root } from './studio-fixture';

const time = '2026-10-03T08:00:00Z';

test('media assets use four image cards, ignore removed filters and open from the card', async ({ page }) => {
  const state = await fixture(page, true);
  await page.goto('/media-library/image?name=removed&source_id=101&created_after=2025-01-01');
  const cards = page.locator('.media-gallery-card');
  await expect(cards).toHaveCount(4);
  await expect(page.getByRole('searchbox')).toHaveCount(0);
  await expect(page.getByText('更多筛选', { exact: false })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '刷新', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '查看生成任务', exact: true })).toHaveCount(0);
  for (const request of state.requests.filter(item => item.path === '/media-library/items')) {
    expect([...request.query.keys()].sort()).toEqual(['limit', 'media_type', 'offset']);
  }
  const bounds = await Promise.all((await cards.all()).map(card => card.boundingBox()));
  expect(new Set(bounds.map(box => box!.y)).size).toBe(1);
  expect(bounds[0]!.width).toBeLessThan(300);
  const name = cards.first().locator('.media-gallery-name');
  expect(await name.evaluate(node => getComputedStyle(node).position)).toBe('absolute');
  await cards.first().focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog', { name: '资产详情', exact: true })).toBeVisible();
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test('video cards and asset loading, empty and error states fit a narrow viewport', async ({ page }) => {
  const state = await fixture(page, true);
  let mode: 'loading' | 'empty' | 'error' | 'video' = 'loading';
  let resolveLoading: (() => void) | undefined;
  const loaded = new Promise<void>(resolve => { resolveLoading = resolve; });
  await page.route('**/api/v1/media-library/items?*', async route => {
    if (mode === 'loading') await loaded;
    if (mode === 'error') return route.fulfill({ status: 503, json: { error: { code: 'UNAVAILABLE', message: '资产服务暂不可用' } } });
    const items = mode === 'empty' ? [] : Array.from({ length: 4 }, (_, i) => ({
      asset_id: String(7001 + i), generation_id: '8001', media_id: String(9901 + i),
      media_type: 'video', name: `雨夜片段 ${i + 1}`, row_version: '1', url: '/.runtime/timeline-fixture.mp4', created_at: time,
    }));
    await route.fulfill({ json: { items, total: items.length, offset: 0, limit: 20 } });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/media-library/video');
  await expect(page.getByRole('status', { name: '正在加载资产' })).toBeVisible();
  mode = 'empty'; resolveLoading!();
  await expect(page.getByText('还没有资产，完成图片或视频生成后会显示在这里')).toBeVisible();
  mode = 'error'; await page.reload();
  await expect(page.getByText('服务暂时不可用，请稍后重试。')).toBeVisible();
  mode = 'video'; await page.getByRole('button', { name: '重新加载', exact: true }).click();
  await expect(page.locator('.media-gallery-card')).toHaveCount(4);
  await expect(page.locator('.media-gallery-card > video')).toHaveCount(4);
  expect(await page.locator('.media-gallery-grid').evaluate(node => getComputedStyle(node).gridTemplateColumns.split(' ').length)).toBe(2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.locator('.media-gallery-card').first().hover();
  expect(await page.locator('.media-gallery-card').first().evaluate(node => getComputedStyle(node).transform)).toBe('none');
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test('tasks remove extra filters and details, link completed tasks to source and show full failures', async ({ page }) => {
  const state = await fixture(page, true);
  const reason = '参考图片已失效，请在来源分镜中重新选择有效图片并核对模型参数，然后重新生成。';
  await page.route('**/api/v1/ai/generations?*', async route => route.fulfill({ json: {
    items: [
      { generation_id: '8001', service_type: 'image', status: 'succeeded', source: { scene: 'shot_image', shot_id: '101', project_id: '10', episode_id: '20', layout: 'single' }, created_at: time, can_cancel: false, can_retry: false, can_resume: false },
      { generation_id: '8002', service_type: 'image', status: 'failed', error: { code: 'INVALID_REFERENCE', message: reason }, created_at: time, can_cancel: false, can_retry: false, can_resume: false },
    ], total: 2, offset: 0, limit: 20,
  } }));
  await page.goto('/tasks/image?config_id=77&source_id=101&created_after=2025-01-01');
  await expect(page.locator('.ant-table-row')).toHaveCount(2);
  await expect(page.getByRole('button', { name: '详情', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '刷新', exact: true })).toHaveCount(0);
  await expect(page.getByText('更多筛选', { exact: false })).toHaveCount(0);
  await expect(page.getByText(reason, { exact: true })).toBeVisible();
  expect(await page.getByText(reason, { exact: true }).evaluate(node => getComputedStyle(node).whiteSpace)).toBe('normal');
  const source = page.getByRole('link', { name: '打开来源', exact: true });
  await expect(source).toHaveAttribute('href', `${root}/storyboard`);
  await source.click();
  await expect(page).toHaveURL(`${root}/storyboard`);
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test('generic completed tasks retain result access and failed tasks respect recovery permissions', async ({ page }) => {
  const state = await fixture(page, true);
  await page.route('**/api/v1/ai/generations?*', async route => route.fulfill({ json: {
    items: [
      { generation_id: '8001', service_type: 'text', status: 'succeeded', source: null, created_at: time, can_cancel: false, can_retry: false, can_resume: false },
      { generation_id: '8002', service_type: 'text', status: 'failed', error: { code: 'UNKNOWN', message: '供应商受理状态待核实' }, created_at: time, can_cancel: false, can_retry: false, can_resume: true },
      { generation_id: '8003', service_type: 'image', status: 'succeeded', source: null, created_at: time, can_cancel: false, can_retry: false, can_resume: false },
    ], total: 3, offset: 0, limit: 20,
  } }));
  await page.goto('/tasks/text');
  await expect(page.getByRole('link', { name: '查看作品', exact: true })).toHaveAttribute('href', '/media-library/image');
  await expect(page.getByRole('button', { name: '安全恢复', exact: true })).toHaveCount(1);
  await expect(page.getByRole('button', { name: '重新生成', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '查看结果', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '任务详情', exact: true })).toBeVisible();
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});
