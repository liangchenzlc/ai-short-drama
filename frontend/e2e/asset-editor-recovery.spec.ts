import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from './studio-fixture';

async function editorFixture(page: Page) {
  const base = await fixture(page, true, String(test.info().project.use.baseURL));
  const server: Record<string, any> = { ...base.asset, row_version: '4' };
  const operations: { method: string; path: string; body: any }[] = [];
  const state = { detailFailure: false, conflict: false, stale: false, shared: false, candidateAvailable: false };
  await page.route('**/api/v1/assets/501**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname.slice('/api/v1'.length);
    const method = request.method();
    const body = method === 'POST' || method === 'PATCH' ? request.postDataJSON() : null;
    operations.push({ method, path, body });
    if (path === '/assets/501' && method === 'GET') {
      if (state.detailFailure) return route.fulfill({ status: 503, json: { error: { code: 'OFFLINE' } } });
      return route.fulfill({ json: server });
    }
    if (path === '/assets/501' && method === 'PATCH') {
      if (state.conflict || body.row_version !== server.row_version) return route.fulfill({ status: 409, json: { error: { code: 'asset_version_conflict' } } });
      Object.assign(server, body, { row_version: String(BigInt(server.row_version) + 1n), state: 'unconfirmed' });
      return route.fulfill({ json: server });
    }
    if (path === '/assets/501/image-candidates') {
      if (method === 'POST') {
        expect(body.media_id).toBe(server.media_id);
        state.candidateAvailable = true;
        return route.fulfill({ status: 201, json: { id: '701', media_id: server.media_id, url: server.image.url, created_at: '2026-10-04T00:00:00Z', generation: null } });
      }
      return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 20 } });
    }
    if (path === '/assets/501/confirm') {
      expect(state.candidateAvailable).toBe(true);
      expect(body.expected_media_id).toBe(server.media_id);
      if (state.conflict || body.row_version !== server.row_version) return route.fulfill({ status: 409, json: { error: { code: 'asset_version_conflict' } } });
      if (state.stale && !body.acknowledge_stale_source) return route.fulfill({ status: 409, json: { error: { code: 'stale_source' } } });
      if (state.shared && !body.confirm_shared) return route.fulfill({ status: 409, json: { error: { code: 'shared_asset_confirmation_required' } } });
      Object.assign(server, { state: 'confirmed', row_version: String(BigInt(server.row_version) + 1n) });
      return route.fulfill({ json: server });
    }
    return route.fallback();
  });
  return { ...base, server, operations, state };
}

async function openEditor(page: Page) {
  await expect(page.getByRole('button', { name: '林晚', exact: true })).toBeVisible();
  const menu = page.getByRole('button', { name: '林晚更多操作', exact: true });
  if (await menu.count()) {
    await menu.click();
    await page.getByRole('menuitem', { name: '编辑素材', exact: true }).click();
  } else await page.getByRole('button', { name: '林晚', exact: true }).click();
  return page.locator('.episode-asset-detail, .asset-editor-drawer');
}

for (const scope of ['episode', 'project', 'global'] as const) {
  test(`current published image can be reconfirmed in the ${scope} library`, async ({ page }) => {
    const data = await editorFixture(page);
    if (scope === 'episode') await page.goto(`${root}/assets`);
    else if (scope === 'global') await page.goto('/assets/character');
    else {
      await page.goto('/projects/10');
      await page.getByRole('button', { name: '项目资源库', exact: true }).click();
    }
    const editor = await openEditor(page);
    await editor.getByRole('textbox', { name: '描述', exact: true }).fill('修改后的外观，继续使用已采用的图片。');
    await editor.getByRole('button', { name: '保存修改', exact: true }).click();
    await expect(editor.getByText('待确认', { exact: true }).first()).toBeVisible();
    await editor.getByRole('button', { name: '重新确认当前图片', exact: true }).click();
    await page.getByRole('dialog', { name: '确认操作', exact: true }).getByRole('button', { name: '确认继续', exact: true }).click();
    await expect.poll(() => data.server.state).toBe('confirmed');
    expect(data.operations.filter(op => op.path.endsWith('/confirm'))).toHaveLength(1);
    expect(data.operations.find(op => op.path.endsWith('/confirm'))?.body.row_version).toBe('5');
    expect(data.errors).toEqual([]);
  });
}

test('opening the editor reads latest detail and retries a failed initial read', async ({ page }) => {
  const data = await editorFixture(page);
  data.server.description = '服务端最新描述';
  data.state.detailFailure = true;
  await page.goto(`${root}/assets`);
  const editor = await openEditor(page);
  await expect(editor.getByRole('button', { name: '重新读取素材', exact: true })).toBeVisible();
  await expect(editor.getByRole('button', { name: '保存修改', exact: true })).toBeDisabled();
  data.state.detailFailure = false;
  await editor.getByRole('button', { name: '重新读取素材', exact: true }).click();
  await expect(editor.getByRole('textbox', { name: '描述', exact: true })).toHaveValue('服务端最新描述');
  expect(data.operations.filter(op => op.method === 'GET' && op.path === '/assets/501')).toHaveLength(2);
});

test('a version conflict preserves and downloads the draft before explicit reconciliation', async ({ page }) => {
  const data = await editorFixture(page);
  await page.goto(`${root}/assets`);
  const editor = await openEditor(page);
  const description = editor.getByRole('textbox', { name: '描述', exact: true });
  await expect(description).toBeEnabled();
  await description.fill('本页尚未保存的草稿');
  Object.assign(data.server, { description: '协作者最新描述', row_version: '5' });
  await editor.getByRole('button', { name: '保存修改', exact: true }).click();
  await expect(description).toHaveValue('本页尚未保存的草稿');
  await expect(editor.getByRole('button', { name: '保存修改', exact: true })).toBeDisabled();
  await expect(editor.getByRole('button', { name: /保存并生成图片$/ })).toBeDisabled();
  const download = page.waitForEvent('download');
  await editor.getByRole('button', { name: '下载素材草稿', exact: true }).click();
  expect((await download).suggestedFilename()).toBe('asset-501-draft.json');
  await editor.getByRole('button', { name: '核对最新版本', exact: true }).click();
  const review = page.getByRole('dialog', { name: '核对素材版本', exact: true });
  await expect(review.getByText('协作者最新描述', { exact: true })).toBeVisible();
  await expect(description).toHaveValue('本页尚未保存的草稿');
  expect(data.operations.filter(op => op.method === 'PATCH')).toHaveLength(1);
  await review.getByRole('button', { name: '已核对，保留草稿继续编辑', exact: true }).click();
  await expect(description).toHaveValue('本页尚未保存的草稿');
  await editor.getByRole('button', { name: '保存修改', exact: true }).click();
  await expect.poll(() => data.server.description).toBe('本页尚未保存的草稿');
  expect(data.operations.filter(op => op.method === 'PATCH').at(-1)?.body.row_version).toBe('5');
  expect(data.errors).toEqual([]);
});

test('reconfirmation keeps stale-source and shared-impact confirmations', async ({ page }) => {
  const data = await editorFixture(page);
  Object.assign(data.server, { state: 'unconfirmed', reference_count: 2 });
  data.state.stale = true;
  data.state.shared = true;
  await page.goto(`${root}/assets`);
  const editor = await openEditor(page);
  await editor.getByRole('button', { name: '重新确认当前图片', exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: '确认操作', exact: true });
  await confirmation.getByRole('button', { name: '确认继续', exact: true }).click();
  await expect(confirmation).toContainText('旧内容');
  await confirmation.getByRole('button', { name: '确认继续', exact: true }).click();
  await expect(confirmation).toContainText('共享引用');
  await confirmation.getByRole('button', { name: '确认继续', exact: true }).click();
  await expect.poll(() => data.server.state).toBe('confirmed');
  expect(data.operations.filter(op => op.path.endsWith('/confirm')).at(-1)?.body).toMatchObject({ acknowledge_stale_source: true, confirm_shared: true, row_version: '4', expected_media_id: '9901' });
});

test('closing a delayed detail request prevents the old response from reopening the editor', async ({ page }) => {
  const data = await editorFixture(page);
  let release!: () => void;
  const delayed = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/v1/assets/501', async route => {
    if (route.request().method() !== 'GET') return route.fallback();
    await delayed;
    await route.fulfill({ json: { ...data.server, name: '迟到的旧响应' } }).catch(() => {});
  });
  await page.goto(`${root}/assets`);
  const editor = await openEditor(page);
  await expect(editor.getByText('正在读取最新素材…', { exact: true })).toBeVisible();
  await editor.getByRole('button', { name: '返回素材列表', exact: true }).click();
  release();
  await expect(editor).toHaveCount(0);
  await expect(page.getByText('迟到的旧响应', { exact: true })).toHaveCount(0);
  expect(data.errors).toEqual([]);
});

test('a generation source conflict exposes recovery without submitting another task', async ({ page }) => {
  const data = await editorFixture(page);
  let submissions = 0;
  await page.route('**/api/v1/ai/generations/image', route => {
    submissions++;
    return route.fulfill({ status: 409, json: { error: { code: 'asset_version_conflict' } } });
  });
  await page.goto(`${root}/assets`);
  const editor = await openEditor(page);
  await editor.getByRole('button', { name: '保存并生成图片', exact: true }).click();
  await expect(editor.getByRole('button', { name: '核对最新版本', exact: true })).toBeVisible();
  await expect(editor.getByRole('textbox', { name: '描述', exact: true })).toHaveValue(data.server.description);
  await expect(editor.getByRole('button', { name: /保存并生成图片$/ })).toBeDisabled();
  expect(submissions).toBe(1);
});

test('conflict review can fail safely before explicitly discarding the draft at narrow widths', async ({ page }) => {
  const data = await editorFixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${root}/assets`);
  const editor = await openEditor(page);
  const description = editor.getByRole('textbox', { name: '描述', exact: true });
  await description.fill('保留到明确弃稿之前');
  Object.assign(data.server, { description: '另一页面最新文字', row_version: '5' });
  await editor.getByRole('button', { name: '保存修改', exact: true }).click();
  data.state.detailFailure = true;
  await editor.getByRole('button', { name: '核对最新版本', exact: true }).click();
  const review = page.getByRole('dialog', { name: '核对素材版本', exact: true });
  await expect(review.getByRole('button', { name: '重试读取最新版本', exact: true })).toBeVisible();
  await expect(description).toHaveValue('保留到明确弃稿之前');
  data.state.detailFailure = false;
  await review.getByRole('button', { name: '重试读取最新版本', exact: true }).click();
  await expect(review.getByText('另一页面最新文字', { exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await review.getByRole('button', { name: '放弃草稿并载入最新版本', exact: true }).click();
  await expect(description).toHaveValue('另一页面最新文字');
  await expect(editor.getByRole('button', { name: '保存修改', exact: true })).toBeDisabled();
  expect(data.operations.filter(op => op.method === 'PATCH')).toHaveLength(1);
});
