import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from '../support/studio-fixture';

type BatchItem = { id: string; source_id: string; name: string; status: string; task_id: string; error: null; task: Record<string, unknown> };
type BatchFixture = { id: string; scene: string; scope: unknown; status: string; items: BatchItem[] };

const candidateUrl = (id: string) => `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="120" height="90"><rect width="120" height="90" fill="#785577"/><text x="8" y="45" fill="white">${id}</text></svg>`)}`;

async function batchApi(page: Page, brokenCandidate?: 'once' | 'always') {
  const batches = new Map<string, BatchFixture>();
  const details: { id: string; offset: number }[] = [];
  const creates: { body: any; key: string }[] = [];
  const retries: { body: any; key: string }[] = [];
  const groupReads: string[] = [];
  let activeGroups = 0, maxActiveGroups = 0;
  let nextId = 900;
  let secondUnknown = true;
  let retryUnknown = true;
  await page.route('**/invalid-candidate/**', route => route.fulfill({ status: 404 }));
  const makeItem = (sourceId: string, scene: string, status: string, retry = false): BatchItem => ({
    id: String(10000 + Number(sourceId)), source_id: sourceId, name: `镜头 ${Number(sourceId) - 100}`,
    status, task_id: String(20000 + Number(sourceId)), error: null,
    task: { generation_id: String(20000 + Number(sourceId)), service_type: scene === 'shot_video' ? 'video' : 'image',
      image_group_id: scene === 'shot_video' ? null : String(30000 + Number(sourceId) + (retry ? 1000 : 0)),
      status: status === 'active' ? 'running' : status === 'waiting' ? 'queued' : status,
      can_retry: status === 'failed', can_resume: false, created_at: retry ? '2026-11-08T01:00:00Z' : '2026-11-08T00:00:00Z' },
  });
  const summary = (batch: BatchFixture) => ({ ...batch, config_id: '77', counts: {}, total: batch.items.length,
    can_control: true, can_cancel: true, created_at: '2026-11-08T00:00:00Z' });
  await page.route('**/api/v1/image-groups/*', async route => {
    const id = new URL(route.request().url()).pathname.split('/').at(-1)!;
    groupReads.push(id);
    const reads = groupReads.filter(groupId => groupId === id).length;
    const url = id === '30501' && (brokenCandidate === 'always' || brokenCandidate === 'once' && reads === 1)
      ? `/invalid-candidate/${id}?attempt=${reads}` : candidateUrl(id);
    activeGroups++; maxActiveGroups = Math.max(maxActiveGroups, activeGroups);
    try {
      await new Promise(resolve => setTimeout(resolve, 80));
      return await route.fulfill({ json: { id, kind: 'generation', status: 'completed', is_current: true,
        target_kind: 'shot', target_id: id, target_version: '1', expected_count: 1, primary_index: null, primary_version: '1',
        notice: null, task_id: '1', created_at: '2026-11-08T00:00:00Z', items: [{ id: `4${id}`, output_index: 1,
          media_id: `5${id}`, url, width: 120, height: 90, item_version: '1', status: 'ready', error: null, prompt: null, history: [] }] } });
    } finally { activeGroups--; }
  });
  await page.route('**/api/v1/ai/generation-batches**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (path.endsWith('/capabilities')) return route.fulfill({ json: { enabled: true, max_items: 100 } });
    if (path.endsWith('/preflight')) {
      const body = request.postDataJSON(), ids = body.source_ids as string[];
      return route.fulfill({ json: { preflight_hash: 'a'.repeat(64), task_count: ids.length, output_count: ids.length, concurrency: 2,
        items: ids.map(id => ({ source_id: id, name: `镜头 ${Number(id) - 100}`, state: 'ready', reason: '', task_id: null, parameters: {} })) } });
    }
    if (path.endsWith('/generation-batches') && request.method() === 'POST') {
      const body = request.postDataJSON();
      creates.push({ body, key: request.headers()['idempotency-key'] });
      if (body.scene === 'shot_image' && body.accepted_ids.length === 1 && secondUnknown) {
        secondUnknown = false;
        return route.abort('failed');
      }
      const id = String(nextId++);
      const items = (body.accepted_ids as string[]).map((sourceId, index) => {
        const status = body.scene === 'shot_video' ? 'waiting' : body.scene === 'asset_image'
          ? ['succeeded', 'failed', 'needs_review'][index] ?? 'succeeded'
          : body.accepted_ids.length === 1 ? sourceId === '101' ? 'active' : 'succeeded' : ['waiting', 'active', 'failed', 'needs_review'][index] ?? 'succeeded';
        return makeItem(sourceId, body.scene, status, body.scene === 'shot_image' && body.accepted_ids.length === 1 && sourceId === '101');
      });
      const batch = { id, scene: body.scene, scope: body, status: items.some(item => item.status === 'needs_review') ? 'needs_review' : 'running', items };
      batches.set(id, batch);
      return route.fulfill({ json: summary(batch) });
    }
    const retry = path.match(/\/generation-batches\/(\d+)\/retry-failed$/);
    if (retry) {
      retries.push({ body: request.postDataJSON(), key: request.headers()['idempotency-key'] });
      if (retryUnknown) { retryUnknown = false; return route.abort('failed'); }
      const original = batches.get(retry[1])!, id = String(nextId++);
      const selected = original.items.filter(item => retries.at(-1)!.body.item_ids.includes(item.id));
      const batch = { id, scene: original.scene, scope: original.scope, status: 'running', items: selected.map(item => makeItem(item.source_id, original.scene, 'active', true)) };
      batches.set(id, batch);
      return route.fulfill({ json: summary(batch) });
    }
    const control = path.match(/\/generation-batches\/(\d+)\/(pause|resume)$/);
    if (control) {
      const batch = batches.get(control[1])!;
      batch.status = control[2] === 'pause' ? 'paused' : 'running';
      if (control[2] === 'resume') for (const item of batch.items) item.status = 'active';
      return route.fulfill({ json: summary(batch) });
    }
    const id = path.match(/\/generation-batches\/(\d+)$/)?.[1];
    if (id) {
      const batch = batches.get(id)!;
      const offset = Number(url.searchParams.get('offset') ?? 0);
      const limit = Math.min(Number(url.searchParams.get('limit') ?? 20), 20);
      details.push({ id, offset });
      return route.fulfill({ json: { ...summary(batch), items: batch.items.slice(offset, offset + limit), offset, limit } });
    }
    return route.fallback();
  });
  return { batches, creates, details, retries, groupReads, activeGroups: () => activeGroups, maxActiveGroups: () => maxActiveGroups };
}

test('正式分镜集合在宫格和列表持续展示101项批次状态并恢复重试', async ({ page }, info) => {
  test.setTimeout(90000);
  const studio = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  for (let position = 81; position <= 101; position++) studio.shots.push({ ...structuredClone(studio.shots[0]), id: String(100 + position), position, title: `站台镜头 ${position}` });
  studio.shots[0].video = { media_id: '99001', media_asset_id: '99002', url: null, resolution: '720p', duration_ms: 3000, first_frame_media_id: null, is_stale: false };
  const api = await batchApi(page);
  await page.goto(`${root}/storyboard?shot=101`);
  const workspace = page.getByRole('region', { name: '分镜工作区', exact: true });
  await workspace.getByRole('button', { name: '宫格展示', exact: true }).click();
  await expect(workspace.locator('.resourceCollectionItem')).toHaveCount(101);
  await workspace.getByRole('checkbox', { name: '全选筛选结果', exact: true }).check();
  await workspace.getByRole('button', { name: '批量生成分镜图', exact: true }).click();
  let drawer = page.getByRole('dialog', { name: '批量生成预检' });
  await drawer.getByRole('combobox').first().click();
  await page.getByRole('option', { name: '本地验收模型' }).click();
  await drawer.getByRole('button', { name: '检查可执行项' }).click();
  await drawer.getByRole('button', { name: '确认并启动 101 项生成' }).click();
  await expect(page.getByText(/第 2 批.*受理.*待核对/)).toBeVisible();
  await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click();
  const card = (position: number) => workspace.getByRole('button', { name: `打开站台镜头 ${position}`, exact: true });
  await expect(card(1)).toContainText('图：已设置 · 排队');
  await expect(card(2)).toContainText('图：已设置 · 生成中');
  await expect(card(3)).toContainText('图：已设置 · 失败');
  await expect(card(4)).toContainText('图：未设置 · 受理待核对');
  await expect(card(5)).toContainText('图：未设置 · 生成完成');
  await expect(card(5).locator('img')).toHaveAttribute('src', candidateUrl('30105'));
  await expect(card(5)).toContainText('候选');
  await expect(card(1).locator('img')).toHaveAttribute('src', studio.shots[0].image.url);
  expect(api.groupReads).not.toContain('30201');
  expect(api.maxActiveGroups()).toBeLessThanOrEqual(4);
  await expect(card(101)).toContainText('图：已设置 · 受理待核对');
  await expect(card(1)).toContainText('视频：已设置');
  await expect(card(1)).toHaveAccessibleDescription(/图：已设置 · 排队.*视频：已设置/);
  await expect.poll(async () => (await workspace.locator('.resourceItemStatus').allTextContents()).filter(text => text.includes('生成完成')).length).toBe(96);
  expect(api.details.filter(item => item.id === '900').map(item => item.offset)).toEqual(expect.arrayContaining([0, 20, 40, 60, 80]));
  await page.setViewportSize({ width: 1280, height: 844 });
  await page.screenshot({ path: info.outputPath('storyboard-grid-1280.png'), animations: 'disabled' });
  await workspace.getByRole('button', { name: '列表展示', exact: true }).click();
  await expect(card(4)).toContainText('受理待核对');
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.screenshot({ path: info.outputPath('storyboard-list-1440.png'), animations: 'disabled' });
  await workspace.getByRole('button', { name: '继续核对批次' }).click();
  await page.getByRole('button', { name: '继续提交' }).click();
  await expect(page.getByRole('heading', { name: '分镜图片 · 受理待核对' })).toBeVisible();
  expect(api.creates[2]).toEqual(api.creates[1]);
  const failedRow = page.locator('.ant-table-row').filter({ hasText: '镜头 3' });
  await failedRow.getByRole('checkbox').check();
  await page.getByRole('button', { name: '重新生成所选失败项' }).click();
  await page.getByRole('button', { name: '确认继续' }).click();
  await expect(page.getByText('重试请求受理待核对，请使用原请求继续核对。')).toBeVisible();
  await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click();
  await expect(card(3)).toContainText('图：已设置 · 受理待核对');
  await workspace.getByRole('button', { name: '继续核对批次' }).click();
  await page.getByRole('button', { name: '继续核对重试' }).click();
  await page.getByRole('button', { name: '确认继续' }).click();
  await expect(page.getByRole('heading', { name: '分镜图片 · 执行中' })).toBeVisible();
  expect(api.retries[1]).toEqual(api.retries[0]);
  await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click();
  await expect(card(3)).toContainText('图：已设置 · 生成中');
  for (const batch of api.batches.values()) {
    batch.status = 'succeeded';
    for (const item of batch.items) item.status = 'succeeded';
  }
  await expect(card(1)).toContainText('图：已设置 · 生成完成', { timeout: 12000 });
  await expect(card(3)).toContainText('图：已设置 · 生成完成', { timeout: 12000 });
  await expect(card(101)).toContainText('图：已设置 · 生成完成');
  await expect.poll(async () => (await workspace.locator('.resourceItemStatus').allTextContents()).filter(text => text.includes('生成完成')).length).toBe(101);
  await card(101).scrollIntoViewIfNeeded();
  await expect(card(101).locator('img')).toHaveAttribute('src', candidateUrl('30201'));
  await expect.poll(() => api.activeGroups()).toBe(0);
  const count = api.details.length;
  const imageCount = api.groupReads.length;
  await page.waitForTimeout(4500);
  expect(api.details.length).toBe(count);
  expect(api.groupReads.length).toBe(imageCount);
  await page.reload();
  await workspace.getByRole('button', { name: '宫格展示', exact: true }).click();
  await expect(card(101)).toContainText('图：已设置 · 生成完成');
  await workspace.getByRole('checkbox', { name: '选择分镜：站台镜头 1', exact: true }).check();
  await workspace.getByRole('button', { name: '批量生成分镜图', exact: true }).click();
  drawer = page.getByRole('dialog', { name: '批量生成预检' });
  await drawer.getByRole('combobox').first().click();
  await page.getByRole('option', { name: '本地验收模型' }).click();
  await drawer.getByRole('button', { name: '检查可执行项' }).click();
  await drawer.getByRole('button', { name: '确认并启动 1 项生成' }).click();
  await expect(page.getByRole('heading', { name: '分镜图片 · 执行中' })).toBeVisible();
  await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click();
  await expect(card(1)).toContainText('图：已设置 · 生成中');
  await expect(card(101)).toContainText('图：已设置 · 生成完成');
  const newestImage = [...api.batches.values()].at(-1)!;
  newestImage.status = 'succeeded'; newestImage.items[0].status = 'succeeded';
  await expect(card(1)).toContainText('图：已设置 · 生成完成', { timeout: 12000 });
  await expect(card(1).locator('img')).toHaveAttribute('src', candidateUrl('31101'));
  await workspace.getByRole('button', { name: '批量生成视频', exact: true }).click();
  drawer = page.getByRole('dialog', { name: '批量生成预检' });
  await drawer.getByRole('combobox').first().click();
  await page.getByRole('option', { name: '本地验收模型' }).click();
  await drawer.getByRole('button', { name: '检查可执行项' }).click();
  await drawer.getByRole('button', { name: '确认并启动 1 项生成' }).click();
  await expect(page.getByRole('heading', { name: '分镜视频 · 执行中' })).toBeVisible();
  await page.getByRole('button', { name: '暂停后续任务' }).click();
  await expect(page.getByRole('heading', { name: '分镜视频 · 已暂停' })).toBeVisible();
  await expect(card(1)).toContainText('视频：已设置 · 已暂停');
  await page.getByRole('button', { name: '恢复批次' }).click();
  await expect(page.getByRole('heading', { name: '分镜视频 · 执行中' })).toBeVisible();
  await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click();
  await expect(card(1)).toContainText('图：已设置 · 生成完成');
  await expect(card(1)).toContainText('视频：已设置 · 生成中');
  expect(studio.errors).toEqual([]);
});

test('正式素材集合保留主图状态并显示批量任务结果', async ({ page }) => {
  const studio = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  const api = await batchApi(page, 'once');
  await page.goto(`${root}/assets?asset=501`);
  const workspace = page.getByRole('region', { name: '资产工作区', exact: true });
  await workspace.getByRole('button', { name: '宫格展示', exact: true }).click();
  await workspace.getByRole('checkbox', { name: '全选筛选结果' }).check();
  await workspace.getByRole('button', { name: '批量生成图片', exact: true }).click();
  const drawer = page.getByRole('dialog', { name: '批量生成预检' });
  await drawer.getByRole('combobox').first().click();
  await page.getByRole('option', { name: '本地验收模型' }).click();
  await drawer.getByRole('button', { name: '检查可执行项' }).click();
  await drawer.getByRole('button', { name: '确认并启动 3 项生成' }).click();
  await expect(page.getByRole('heading', { name: '素材图片 · 受理待核对' })).toBeVisible();
  await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click();
  const card = (name: string) => workspace.getByRole('button', { name: `打开${name}`, exact: true });
  await expect(card('林晚')).toContainText('主图：已设置 · 生成完成');
  await expect(card('林晚').locator('img')).toHaveAttribute('src', candidateUrl('30501'));
  await expect(card('林晚')).toContainText('候选');
  expect(api.groupReads.filter(id => id === '30501')).toHaveLength(2);
  expect(studio.asset.media_id).toBe('9901');
  expect(api.groupReads).not.toContain('30502');
  await expect(card('雨夜车站')).toContainText('主图：已设置 · 失败');
  await expect(card('泛黄的旧信')).toContainText('主图：已设置 · 受理待核对');
  await workspace.getByRole('button', { name: '列表展示', exact: true }).click();
  await expect(card('林晚')).toContainText('主图：已设置 · 生成完成');
  await page.reload();
  await workspace.getByRole('button', { name: '宫格展示', exact: true }).click();
  await expect(card('雨夜车站')).toContainText('主图：已设置 · 失败');
  expect(studio.errors).toEqual([]);
});

test('候选图连续加载失败时回退主图并停止重试', async ({ page }) => {
  const studio = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  const api = await batchApi(page, 'always');
  await page.goto(`${root}/assets?asset=501`);
  const workspace = page.getByRole('region', { name: '资产工作区', exact: true });
  await workspace.getByRole('button', { name: '宫格展示', exact: true }).click();
  await workspace.getByRole('checkbox', { name: '全选筛选结果' }).check();
  await workspace.getByRole('button', { name: '批量生成图片', exact: true }).click();
  const drawer = page.getByRole('dialog', { name: '批量生成预检' });
  await drawer.getByRole('combobox').first().click();
  await page.getByRole('option', { name: '本地验收模型' }).click();
  await drawer.getByRole('button', { name: '检查可执行项' }).click();
  await drawer.getByRole('button', { name: '确认并启动 3 项生成' }).click();
  await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click();
  const card = workspace.getByRole('button', { name: '打开林晚', exact: true });
  await expect.poll(() => api.groupReads.filter(id => id === '30501').length).toBe(2);
  await expect(card.locator('img')).toHaveAttribute('src', studio.asset.image.url);
  await expect(card).not.toContainText('候选');
  await page.waitForTimeout(1500);
  expect(api.groupReads.filter(id => id === '30501')).toHaveLength(2);
  expect(studio.errors).toEqual([]);
});
