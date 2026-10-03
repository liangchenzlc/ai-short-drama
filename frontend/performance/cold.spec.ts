import { test, expect } from '@playwright/test';
import { latencyMs, origin, record } from './measurement';
import { fixture, root } from '../e2e/studio-fixture';

const routes = ['/projects', '/projects/10', '/projects/10?section=resources', '/projects/10?section=collaboration',
  `${root}/source`, `${root}/assets`, `${root}/storyboard`, `${root}/assembly`,
  '/assets/character', '/assets/scene', '/assets/prop', '/tasks/text', '/tasks/image', '/tasks/video', '/tasks/audio',
  '/media-library/image', '/media-library/video', '/ai_config', '/account',
  '/login', '/register', '/verify-email', '/reset-password'];

for (const path of routes) test(`audit ${path}`, async ({ page }) => {
  const state = await fixture(page, true, origin);
  const timings: { method: string; url: string; start: number; finish?: number; failed?: string }[] = [];
  const startedAt = Date.now();
  const map = new Map<unknown, typeof timings[number]>();
  const scripts: string[] = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname.endsWith('.js')) scripts.push(url.pathname);
    if (!url.pathname.startsWith('/api/v1')) return;
    const item = { method: request.method(), url: url.pathname.slice('/api/v1'.length) + url.search, start: Date.now() - startedAt };
    timings.push(item); map.set(request, item);
  });
  page.on('requestfinished', request => { const item = map.get(request); if (item) item.finish = Date.now() - startedAt; });
  page.on('requestfailed', request => { const item = map.get(request); if (item) { item.finish = Date.now() - startedAt; item.failed = request.failure()?.errorText; } });
  await page.route('**/api/v1/**', async route => {
    await new Promise(resolve => setTimeout(resolve, latencyMs));
    const url = new URL(route.request().url());
    if (url.pathname === '/api/v1/auth/capabilities') return route.fulfill({ json: { enabled: true } });
    if (url.pathname === '/api/v1/auth/me') return route.fulfill({ json: { user: { id: '1', username: 'audit', display_name: '性能测量', email: 'audit@example.test', email_verified: true } } });
    if (url.pathname === '/api/v1/users/me/model-preferences') return route.fulfill({ json: { items: [] } });
    if (url.pathname === '/api/v1/projects/10/members') return route.fulfill({ json: { items: [{ user_id: '1', username: 'audit', display_name: 'audit', role: 'owner' }] } });
    if (path.includes('section=collaboration') && url.pathname === '/api/v1/projects/10') return route.fulfill({ json: {
      id: '10', name: '雨夜来信', synopsis: '一封迟到的信改变了两个人的命运。', style: '电影质感', aspect: '16:9', episode_count: 1,
      row_version: '1', capabilities: { delete: true, manage_members: true },
    } });
    if (url.pathname === '/api/v1/projects/10/invitations') return route.fulfill({ json: { items: [] } });
    await route.fallback();
  });
  await page.goto(path);
  await expect(page.locator('h1').first()).toBeVisible();
  const headingAt = Date.now() - startedAt;
  let readyAt: number | null = null;
  if (path.endsWith('/source')) await expect(page.locator('#episode-novel')).toBeEditable();
  if (path.endsWith('/assets')) await expect(page.getByRole('button', { name: '林晚', exact: true })).toBeVisible();
  if (path.endsWith('/storyboard')) await expect(page.locator('.storyboard-summary')).toHaveCount(20);
  if (path.endsWith('/assembly')) await expect(page.getByRole('region', { name: '视频时间轴' })).toBeVisible();
  if (path.includes('section=collaboration')) await expect(page.locator('.collaboration-person')).toHaveCount(1);
  if (path === '/projects') await expect(page.locator('.project-tile')).toHaveCount(4);
  if (path === '/projects/10') await expect(page.locator('.episode-media-card')).toHaveCount(1);
  if (path.includes('section=resources') || path.startsWith('/assets/')) await expect(page.locator('.asset-name-button').first()).toBeVisible();
  if (path.startsWith('/tasks/')) await expect(page.locator('.ant-table-tbody .ant-table-row').first()).toBeVisible();
  if (path.startsWith('/media-library/')) await expect(page.locator('.media-gallery-card')).toHaveCount(4);
  if (path === '/ai_config') await expect(page.locator('.config-table tbody tr')).toHaveCount(1);
  if (path === '/account') await expect(page.locator('.account-details')).toBeVisible();
  if (['/login', '/register', '/verify-email', '/reset-password'].includes(path)) await expect(page.locator('.identity-form form')).toBeVisible();
  readyAt = Date.now() - startedAt;
  // Collect initial requests; this window does not include a full 4-second polling cycle.
  await page.waitForTimeout(1500);
  const result = { path, latencyMs, headingAt, readyAt, finalUrl: page.url(), timings, scripts: [...new Set(scripts)],
    errors: state.errors, unexpected: state.unexpected,
    jsResources: await page.evaluate(() => performance.getEntriesByType('resource').filter(item => item.name.endsWith('.js')).map(item => ({ name: new URL(item.name).pathname, startTime: item.startTime, duration: item.duration }))),
    paints: await page.evaluate(() => performance.getEntriesByType('paint').map(item => ({ name: item.name, startTime: item.startTime }))) };
  record(`cold${path.replaceAll('/', '_').replaceAll('?', '_').replaceAll('=', '_') || 'root'}`, result, result.scripts);
  expect(state.errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});
