import { expect, test } from '@playwright/test';
import { fixture, root } from './studio-fixture';

const productionOrigin = process.env.PLAYWRIGHT_PRODUCTION_URL;
test.use({ baseURL: productionOrigin ?? 'http://127.0.0.1:4175' });

test('opening writing does not load unvisited material or storyboard stages', async ({ page }) => {
  const state = await fixture(page, false, productionOrigin);
  await page.goto(`${root}/source`);
  await expect(page.locator('#episode-novel')).toBeEditable();
  expect(state.requests.filter(item => item.path === `${root}/assets`)).toEqual([]);
  expect(state.requests.filter(item => item.query.get('source_scene') === 'script_assets')).toEqual([]);
  expect(state.requests.filter(item => item.path === `${root}/shots`)).toEqual([]);
  expect(state.errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test('opening assembly does not load unvisited writing history or material stages', async ({ page }) => {
  const state = await fixture(page, true, productionOrigin);
  await page.goto(`${root}/assembly`);
  await expect(page.getByRole('region', { name: '视频时间轴' })).toBeVisible();
  expect(state.requests.filter(item => item.path === `${root}/scripts`)).toEqual([]);
  expect(state.requests.filter(item => item.path === `${root}/assets`)).toEqual([]);
  expect(state.requests.filter(item => item.path === '/ai-model-configs')).toEqual([]);
  expect(state.errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test('visited writing preserves unsaved adaptation instructions while moving between stages', async ({ page }) => {
  const state = await fixture(page, false, productionOrigin);
  await page.goto(`${root}/source`);
  const instructions = page.getByRole('textbox', { name: '改编要求 选填' });
  await instructions.fill('保留这个尚未提交的改编要求。');
  await page.locator('.episode-stage-link').filter({ hasText: '素材准备' }).click();
  await expect(page.getByRole('button', { name: '新建角色', exact: true }).first()).toBeVisible();
  await page.locator('.episode-stage-link').filter({ hasText: '小说改编' }).click();
  await expect(instructions).toHaveValue('保留这个尚未提交的改编要求。');
  expect(state.errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test('same-type model selectors share requests and one focus refresh', async ({ page }) => {
  const state = await fixture(page, false, productionOrigin);
  await page.goto(`${root}/source`);
  await expect(page.locator('#episode-novel')).toBeEditable();
  await page.locator('.episode-stage-link').filter({ hasText: '素材准备' }).click();
  await expect(page.getByRole('button', { name: '新建角色', exact: true }).first()).toBeVisible();
  const textReads = () => state.requests.filter(item => item.path === '/ai-model-configs' && item.query.get('service_type') === 'text');
  await expect.poll(() => textReads().length).toBe(1);
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect.poll(() => textReads().length).toBe(2);
  expect(state.errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test('production project details defer episode editors and their table or drawer chunks', async ({ page }) => {
  const origin = process.env.PLAYWRIGHT_PRODUCTION_URL;
  test.skip(!origin, 'Production chunk protection runs against vite preview.');
  const state = await fixture(page, false, origin);
  const chunks: string[] = [];
  page.on('request', request => {
    const pathname = new URL(request.url()).pathname;
    if (pathname.endsWith('.js')) chunks.push(pathname);
  });
  await page.goto(`${origin}/projects/10`);
  await expect(page.locator('.episode-media-card')).toHaveCount(1);
  expect(chunks.filter(path => /EpisodePage-|AssetLibraryPanel-|StoryboardStage-|timeline-|data-table-|drawers-/.test(path))).toEqual([]);
  expect(state.errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test('production preloads the current editor code while authentication protects business reads', async ({ page }) => {
  test.skip(!productionOrigin, 'Preload timing protection runs against vite preview.');
  const state = await fixture(page, false, productionOrigin);
  let release!: () => void;
  const authenticated = new Promise<void>(resolve => { release = resolve; });
  const chunks: string[] = [];
  page.on('request', request => {
    const pathname = new URL(request.url()).pathname;
    if (pathname.endsWith('.js')) chunks.push(pathname);
  });
  await page.route('**/api/v1/auth/capabilities', route => route.fulfill({ json: { enabled: true } }));
  await page.route('**/api/v1/auth/me', async route => {
    await authenticated;
    await route.fulfill({ json: { user: { id: '1', username: 'creator', display_name: '创作者', email: 'creator@example.test', email_verified: true } } });
  });
  await page.route('**/api/v1/users/me/model-preferences', route => route.fulfill({ json: { items: [] } }));
  try {
    await page.goto(`${root}/source`);
    await expect.poll(() => chunks.some(path => /SourceStage-/.test(path))).toBe(true);
    expect(state.requests.filter(item => item.path.startsWith('/projects'))).toEqual([]);
    expect(chunks.filter(path => /AssetsStage-|StoryboardStage-|timeline-/.test(path))).toEqual([]);
  } finally { release(); }
  await expect(page.locator('#episode-novel')).toBeEditable();
  expect(state.errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});
