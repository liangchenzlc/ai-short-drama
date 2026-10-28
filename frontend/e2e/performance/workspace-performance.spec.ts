import { readdirSync, readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import { fixture, root } from '../support/studio-fixture';

const productionOrigin = process.env.PLAYWRIGHT_PRODUCTION_URL;
test.use({ baseURL: productionOrigin ?? 'http://127.0.0.1:4175' });

function productionEditorChunks() {
  const directory = new URL('../../dist/assets/', import.meta.url);
  const bundles = readdirSync(directory).filter(name => name.endsWith('.js'))
    .map(name => ({ path: `/assets/${name}`, code: readFileSync(new URL(name, directory), 'utf8') }));
  function find(marker: string) {
    const matches = bundles.filter(bundle => bundle.code.includes(marker));
    expect(matches, `编辑器构建标识：${marker}`).toHaveLength(1);
    return matches[0].path;
  }
  // 页面改为 index.tsx 后 chunk 不再带页面名，按实际构建内容定位，避免旧正则空匹配。
  return {
    writing: find('在这里写剧本'),
    materials: find('standard-asset-card'),
    storyboard: find('storyboard-summary'),
    assembly: find('assembly-workspace'),
  };
}

test('opening writing does not load unvisited material or storyboard stages', async ({ page }) => {
  const state = await fixture(page, false, productionOrigin);
  await page.goto(`${root}/source`);
  await expect(page.getByRole('textbox', { name: '剧本正文', exact: true })).toBeEditable();
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

test('visited writing preserves a conflicted script draft while moving between stages', async ({ page }) => {
  const state = await fixture(page, false, productionOrigin);
  await page.route(`**/api/v1${root}/script`, route => route.request().method() === 'PUT'
    ? route.fulfill({ status: 409, json: { error: { code: 'version_conflict' } } }) : route.fallback());
  await page.goto(`${root}/source`);
  const script = page.getByRole('textbox', { name: '剧本正文', exact: true });
  await script.fill('保留这个尚未保存的剧本草稿。');
  await expect(page.getByRole('button', { name: /^保存状态：需处理/ })).toBeVisible();
  await page.getByRole('navigation', { name: '创作流程' }).getByRole('button', { name: '资产生成', exact: true }).click();
  await expect(page.getByRole('button', { name: '创建资产', exact: true }).first()).toBeVisible();
  await page.getByRole('navigation', { name: '创作流程' }).getByRole('button', { name: '剧本创作', exact: true }).click();
  await expect(script).toHaveValue('保留这个尚未保存的剧本草稿。');
  expect(state.errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test('same-type model selectors share requests and one focus refresh', async ({ page }) => {
  const state = await fixture(page, false, productionOrigin);
  await page.goto(`${root}/source`);
  await expect(page.getByRole('textbox', { name: '剧本正文', exact: true })).toBeEditable();
  await page.getByRole('navigation', { name: '创作流程' }).getByRole('button', { name: '资产生成', exact: true }).click();
  await expect(page.getByRole('button', { name: '创建资产', exact: true }).first()).toBeVisible();
  await expect(page.getByRole('combobox', { name: '生图模型', exact: true })).toBeVisible();
  await page.getByRole('navigation', { name: '创作流程' }).getByRole('button', { name: '分镜制作', exact: true }).click();
  await expect(page.getByRole('combobox', { name: '生图模型', exact: true })).toBeVisible();
  const imageReads = () => state.requests.filter(item => item.path === '/ai-model-configs' && item.query.get('service_type') === 'image');
  await expect.poll(() => imageReads().length).toBe(1);
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect.poll(() => imageReads().length).toBe(2);
  expect(state.errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test('production project entry loads its script and defers unvisited editors', async ({ page }) => {
  const origin = process.env.PLAYWRIGHT_PRODUCTION_URL;
  test.skip(!origin, 'Production chunk protection runs against vite preview.');
  const state = await fixture(page, false, origin);
  const editors = productionEditorChunks();
  const chunks: string[] = [];
  page.on('request', request => {
    const pathname = new URL(request.url()).pathname;
    if (pathname.endsWith('.js')) chunks.push(pathname);
  });
  await page.goto(`${origin}/projects/10`);
  await expect(page.getByRole('textbox', { name: '剧本正文', exact: true })).toBeEditable();
  expect(chunks).toContain(editors.writing);
  expect(chunks.filter(path => [editors.materials, editors.storyboard, editors.assembly].includes(path)
    || /timeline-|data-table-|drawers-/.test(path))).toEqual([]);
  expect(state.errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test('production preloads the current editor code while authentication protects business reads', async ({ page }) => {
  test.skip(!productionOrigin, 'Preload timing protection runs against vite preview.');
  const state = await fixture(page, false, productionOrigin);
  const editors = productionEditorChunks();
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
    await expect.poll(() => chunks.includes(editors.writing)).toBe(true);
    expect(state.requests.filter(item => item.path.startsWith('/projects'))).toEqual([]);
    expect(chunks.filter(path => [editors.materials, editors.storyboard, editors.assembly].includes(path)
      || /timeline-/.test(path))).toEqual([]);
  } finally { release(); }
  await expect(page.getByRole('textbox', { name: '剧本正文', exact: true })).toBeEditable();
  expect(state.errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});
