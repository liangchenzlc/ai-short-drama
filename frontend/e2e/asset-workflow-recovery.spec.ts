import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from './studio-fixture';

async function baseFixture(page: Page) {
  return fixture(page, true, String(test.info().project.use.baseURL));
}

async function openEditor(page: Page) {
  await page.getByRole('button', { name: '林晚更多操作', exact: true }).click();
  await page.getByRole('menuitem', { name: '编辑素材', exact: true }).click();
  const editor = page.locator('.episode-asset-detail');
  await expect(editor.getByRole('textbox', { name: '描述', exact: true })).toBeVisible();
  return editor;
}

test('project import excludes episode members beyond the first page', async ({ page }) => {
  const data = await baseFixture(page);
  const existing = { ...data.asset, id: '999', name: '第二页已有角色' };
  const fresh = { ...data.asset, id: '888', name: '尚未加入角色' };
  const members = [...Array.from({ length: 20 }, (_, i) => ({ ...data.asset, id: String(1000 + i), name: `角色${i}` })), existing];
  await page.route('**/api/v1/projects/10/**assets**', async route => {
    const url = new URL(route.request().url());
    const values = url.pathname.includes('/episodes/') ? members : [existing, fresh];
    const offset = Number(url.searchParams.get('offset') ?? 0);
    const limit = Number(url.searchParams.get('limit') ?? 20);
    return route.fulfill({ json: { items: values.slice(offset, offset + limit), total: values.length, offset, limit } });
  });
  await page.goto(`${root}/assets`);
  await page.getByRole('button', { name: '从项目库添加', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '从项目素材库添加', exact: true });
  await expect(dialog.getByText('尚未加入角色', { exact: true })).toBeVisible();
  await expect(dialog.getByText('第二页已有角色', { exact: true })).toHaveCount(0);
  expect(data.errors).toEqual([]);
});

test('image candidates show initial loading and recover from an initial error', async ({ page }) => {
  const data = await baseFixture(page);
  let release!: () => void;
  const delayed = new Promise<void>(resolve => { release = resolve; });
  let failed = true;
  await page.route('**/api/v1/assets/501/image-candidates?*', async route => {
    await delayed;
    if (failed) return route.fulfill({ status: 503, json: { error: { code: 'OFFLINE' } } });
    return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 20 } });
  });
  await page.goto(`${root}/assets`);
  const editor = await openEditor(page);
  await expect(editor.getByText('正在读取图片候选…', { exact: true })).toBeVisible();
  release();
  await expect(editor.getByRole('button', { name: '重试读取图片候选', exact: true })).toBeVisible();
  failed = false;
  await editor.getByRole('button', { name: '重试读取图片候选', exact: true }).click();
  await expect(editor.getByText('暂无其他候选图片。', { exact: true })).toBeVisible();
  expect(data.errors).toEqual([]);
});

test('image history distinguishes initial loading from an empty history', async ({ page }) => {
  await baseFixture(page);
  let release!: () => void;
  const delayed = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/v1/ai/generations?*', async route => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('source_scene') !== 'asset_image') return route.fallback();
    await delayed;
    return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 20 } });
  });
  await page.goto(`${root}/assets`);
  const editor = await openEditor(page);
  await editor.locator('.asset-generation-history > summary').click();
  await expect(editor.getByText('正在读取生成记录…', { exact: true })).toBeVisible();
  await expect(editor.getByText('暂无生成记录。', { exact: true })).toHaveCount(0);
  release();
  await expect(editor.getByText('暂无生成记录。', { exact: true })).toBeVisible();
});

test('a history refresh failure preserves known tasks and provides an explicit retry', async ({ page }) => {
  await baseFixture(page);
  let failed = false;
  await page.route('**/api/v1/ai/generations?*', async route => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('source_scene') !== 'asset_image' || !failed) return route.fallback();
    return route.fulfill({ status: 503, json: { error: { code: 'OFFLINE' } } });
  });
  await page.goto(`${root}/assets`);
  const editor = await openEditor(page);
  await editor.locator('.asset-generation-history > summary').click();
  await expect(editor.getByText('任务 92001', { exact: true })).toBeVisible();
  failed = true;
  await editor.getByRole('button', { name: '刷新', exact: true }).click();
  await expect(editor.getByRole('button', { name: '重试读取生成记录', exact: true })).toBeVisible();
  await expect(editor.getByText('任务 92001', { exact: true })).toBeVisible();
  failed = false;
  await editor.getByRole('button', { name: '重试读取生成记录', exact: true }).click();
  await expect(editor.getByRole('button', { name: '重试读取生成记录', exact: true })).toHaveCount(0);
});

function extractionResult(asset: Record<string, any>, id = '88001') {
  return { generation_id: id, result_version: '1', content_version: '1', stale: false, kinds: ['character'], items: ['甲', '乙'].map((name, index) => ({ candidate_id: String(index).repeat(32), original: { ...asset, name, aliases: [] }, draft: { ...asset, name }, matches: [], duplicate_candidates: [], applied: null })) };
}

test('discarding one invalid unselected candidate preserves the other draft before adoption', async ({ page }) => {
  const data = await baseFixture(page);
  const result = extractionResult(data.asset);
  const applications: any[] = [];
  await page.route(`**/api/v1${root}/asset-extraction-results/88001**`, async route => {
    const request = route.request();
    if (request.method() === 'PATCH') {
      for (const change of request.postDataJSON().items) result.items.find(item => item.candidate_id === change.candidate_id)!.draft = change.draft;
      result.result_version = '2';
    }
    if (request.method() === 'POST') {
      applications.push(request.postDataJSON());
      return route.fulfill({ json: { created: 1, reused: 0, already_applied: false } });
    }
    return route.fulfill({ json: result });
  });
  await page.goto(`${root}/assets`);
  await page.getByRole('button', { name: '从剧本提取素材', exact: true }).click();
  const review = page.locator('.asset-extraction-dialog');
  const first = review.locator('.extraction-candidate').filter({ has: page.getByRole('heading', { name: '甲', exact: true }) });
  const second = review.locator('.extraction-candidate').filter({ has: page.getByRole('heading', { name: '乙', exact: true }) });
  await first.getByRole('checkbox', { name: '选择 甲', exact: true }).uncheck();
  await first.getByRole('button', { name: '编辑', exact: true }).click();
  await first.getByRole('textbox', { name: '素材描述', exact: true }).fill('');
  await second.getByRole('button', { name: '编辑', exact: true }).click();
  await second.getByRole('textbox', { name: '素材描述', exact: true }).fill('乙的合法修改');
  const adopt = review.getByRole('button', { name: '加入本集素材库（1）', exact: true });
  await expect(adopt).toBeDisabled();
  await expect(first.getByText('请补充描述，或明确放弃此项修改。', { exact: true })).toBeVisible();
  await first.getByRole('button', { name: '放弃此项修改', exact: true }).click();
  await page.getByRole('dialog', { name: '未保存的修改', exact: true }).getByRole('button', { name: '放弃修改', exact: true }).click();
  await expect(second.getByRole('textbox', { name: '素材描述', exact: true })).toHaveValue('乙的合法修改');
  await expect(adopt).toBeEnabled();
  await adopt.click();
  await expect.poll(() => applications.length).toBe(1);
  expect(applications[0].items.map((item: any) => item.candidate_id)).toEqual(['1'.repeat(32)]);
  expect(result.items[0].draft.description).toBe(data.asset.description);
  expect(result.items[1].draft.description).toBe('乙的合法修改');
});

test('an extraction task outside the latest twenty rows is reconciled without another generation', async ({ page }) => {
  const data = await baseFixture(page);
  let lists = 0;
  let detailReads = 0;
  const source = { scene: 'script_assets', project_id: '10', episode_id: '20' };
  const task = (id: string, status: string) => ({ generation_id: id, service_type: 'text', status, source, created_at: '2026-10-04T00:00:00Z', can_cancel: false, can_resume: false, can_retry: false });
  await page.route('**/api/v1/ai/generations**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/7001')) { detailReads++; return route.fulfill({ json: task('7001', 'succeeded') }); }
    if (url.searchParams.get('source_scene') !== 'script_assets') return route.fallback();
    lists++;
    const items = lists === 1 ? [task('7001', 'running')] : Array.from({ length: 20 }, (_, i) => task(String(7100 + i), 'succeeded'));
    return route.fulfill({ json: { items, total: 21, offset: 0, limit: 20 } });
  });
  await page.route(`**/api/v1${root}/asset-extraction-results/7001`, route => route.fulfill({ json: extractionResult(data.asset, '7001') }));
  await page.goto(`${root}/assets`);
  await expect.poll(() => lists).toBeGreaterThan(0);
  await page.getByRole('button', { name: '从剧本提取素材', exact: true }).click();
  await expect.poll(() => detailReads, { timeout: 10000 }).toBeGreaterThan(0);
  await expect(page.getByRole('button', { name: '重新提取', exact: true })).toBeEnabled();
  expect(data.requests.filter(request => request.method === 'POST' && request.path === '/ai/generations/text')).toEqual([]);
});
