import { expect, test } from '@playwright/test';
import { assetSizes, latencyMs, origin, record } from './measurement';
import { fixture, root } from '../e2e/studio-fixture';

test('warm transitions keep route data and defer first visits', async ({ page }) => {
  const state = await fixture(page, true, origin);
  const requests: { method: string; url: string; at: number }[] = [];
  const scripts: string[] = [];
  let start = Date.now();
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname.startsWith('/api/v1')) requests.push({ method: request.method(), url: url.pathname.slice('/api/v1'.length) + url.search, at: Date.now() - start });
    if (url.pathname.endsWith('.js')) scripts.push(url.pathname);
  });
  await page.route('**/api/v1/**', async route => {
    await new Promise(resolve => setTimeout(resolve, latencyMs));
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/v1/auth/capabilities') return route.fulfill({ json: { enabled: true } });
    if (path === '/api/v1/auth/me') return route.fulfill({ json: { user: { id: '1', username: 'audit', display_name: 'audit', email: 'audit@example.test', email_verified: true } } });
    if (path === '/api/v1/users/me/model-preferences') return route.fulfill({ json: { items: [] } });
    return route.fallback();
  });
  await page.goto(`${root}/source`);
  await expect(page.locator('#episode-novel')).toBeEditable();
  await page.waitForTimeout(1500);
  const results: unknown[] = [];
  for (const stage of ['assets', 'source', 'storyboard', 'assembly', 'source'] as const) {
    requests.length = 0; scripts.length = 0; start = Date.now();
    await page.locator('.episode-stage-link').filter({ hasText: ({ assets: '素材准备', source: '小说改编', storyboard: '分镜制作', assembly: '成片合成与导出' } as const)[stage] }).click();
    if (stage === 'assets') await expect(page.getByRole('button', { name: '林晚', exact: true })).toBeVisible();
    if (stage === 'source') await expect(page.locator('#episode-novel')).toBeEditable();
    if (stage === 'storyboard') await expect(page.locator('.storyboard-summary')).toHaveCount(20);
    if (stage === 'assembly') await expect(page.getByRole('region', { name: '视频时间轴' })).toBeVisible();
    const readyAt = Date.now() - start;
    await page.waitForTimeout(1500);
    results.push({ stage, readyAt, requests: [...requests], scripts: [...new Set(scripts)], assets: assetSizes(scripts) });
  }
  record('warm', { latencyMs, results, errors: state.errors, unexpected: state.unexpected }, []);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});
