import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from './studio-fixture';

async function assetFixture(page: Page) {
  const base = await fixture(page, true, String(test.info().project.use.baseURL));
  const assets = [{ ...base.asset }];
  const creates: { key: string; body: Record<string, unknown> }[] = [];
  const receipts = new Map<string, typeof base.asset>();
  const state = { uncertainCreate: false, creations: 0 };
  await page.route(`**/api/v1${root}/assets**`, async route => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    if (path !== `/api/v1${root}/assets`) return route.fallback();
    if (request.method() === 'POST') {
      const body = request.postDataJSON(); const key = request.headers()['idempotency-key'];
      creates.push({ key, body });
      let created = receipts.get(key);
      if (!created) {
        state.creations += 1;
        created = { ...base.asset, ...body, id: String(510 + state.creations), media_id: null, image: null, row_version: '1', state: 'unconfirmed' };
        receipts.set(key, created!); assets.push(created!);
      }
      if (state.uncertainCreate) return route.fulfill({ status: 503, json: { error: { code: 'OFFLINE', message: '创建回执暂时不可读取' } } });
      return route.fulfill({ status: 201, json: created });
    }
    const url = new URL(request.url()); const kind = url.searchParams.get('kind'); const filtered = assets.filter(item => !kind || item.kind === kind);
    return route.fulfill({ json: { items: filtered, total: filtered.length, offset: 0, limit: 20 } });
  });
  await page.route(/\/api\/v1\/assets\/51\d+(?:\/.*)?$/, async route => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    const id = path.split('/')[4]; const asset = assets.find(item => item.id === id);
    if (!asset) return route.fallback();
    if (path.endsWith('/image-candidates')) return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 20 } });
    if (request.method() === 'PATCH') Object.assign(asset, request.postDataJSON(), { row_version: String(BigInt(asset.row_version) + 1n) });
    return route.fulfill({ json: asset });
  });
  await page.route('**/api/v1/generation-references/asset/51*', route => route.fulfill({ json: { row_version: '1', items: [] } }));
  return { ...base, creates, assets, state };
}

async function pickType(page: Page, type: string) {
  await page.getByRole('combobox', { name: '素材类型', exact: true }).click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: type }).click();
}

test('all and named categories add immediate local cards; only complete name/type creates a server asset', async ({ page }) => {
  const data = await assetFixture(page);
  await page.goto(`${root}/assets`);
  await page.getByRole('button', { name: '新增素材', exact: true }).first().click();
  await expect(page.getByRole('dialog', { name: /新建/ })).toHaveCount(0);
  const name = page.getByRole('textbox', { name: '素材名称', exact: true });
  await expect(name).toHaveValue(''); await expect(name).toBeFocused();
  await expect(page.getByRole('combobox', { name: '素材类型', exact: true })).toHaveValue('');
  await expect(page.getByRole('button', { name: '生成图片', exact: true })).toBeDisabled();
  await name.fill('新角色');
  await page.waitForTimeout(1200);
  expect(data.creates).toHaveLength(0);
  await pickType(page, '角色');
  await expect.poll(() => data.creates.length).toBe(1);
  await expect(page.locator('.standard-assets-filters').getByText('全部', { exact: true }).locator('..')).toHaveClass(/ant-segmented-item-selected/);
  await expect(page.locator('.standard-asset-card').getByRole('button', { name: '新角色', exact: true })).toHaveCount(1);
  for (const [kind, label] of [['scene', '场景'], ['prop', '道具'], ['character', '角色']] as const) {
    await page.locator('.standard-assets-filters .ant-segmented').getByText(label, { exact: true }).click();
    await page.getByRole('button', { name: '新增素材', exact: true }).first().click();
    await expect(page.locator('.standard-asset-name-type .ant-select-selection-item')).toHaveText(label);
    await name.fill(`${label}新增`);
    await expect.poll(() => data.creates.some(item => item.body.name === `${label}新增`)).toBe(true);
    expect(data.creates.find(item => item.body.name === `${label}新增`)?.body.kind).toBe(kind);
  }
  expect(data.errors).toEqual([]);
});

test('uncertain creation survives reload and recovers using the original exact request key', async ({ page }) => {
  const data = await assetFixture(page); data.state.uncertainCreate = true;
  await page.goto(`${root}/assets`);
  await page.getByRole('button', { name: '新增素材', exact: true }).first().click();
  await page.getByRole('textbox', { name: '素材名称', exact: true }).fill('待核对庭院');
  await pickType(page, '场景');
  await expect(page.getByRole('button', { name: '核对原创建请求', exact: true })).toBeVisible();
  expect(data.state.creations).toBe(1);
  await page.getByRole('textbox', { name: '描述', exact: true }).fill('回执等待期间继续输入');
  page.removeAllListeners('dialog'); page.on('dialog', dialog => { void dialog.accept(); });
  await page.reload();
  await expect(page.getByRole('textbox', { name: '描述', exact: true })).toHaveValue('回执等待期间继续输入');
  await expect(page.getByRole('button', { name: '核对原创建请求', exact: true })).toBeVisible();
  expect(data.creates).toHaveLength(1);
  data.state.uncertainCreate = false;
  await page.getByRole('button', { name: '核对原创建请求', exact: true }).click();
  await expect.poll(() => data.creates.length).toBe(2);
  expect(data.creates[1].key).toBe(data.creates[0].key);
  expect(data.creates[1].body).toEqual(data.creates[0].body);
  expect(data.state.creations).toBe(1);
  await expect(page.locator('.standard-asset-card').getByRole('button', { name: '待核对庭院', exact: true })).toHaveCount(1);
  await expect(page.getByRole('textbox', { name: '描述', exact: true })).toHaveValue('回执等待期间继续输入');
  expect(data.errors).toEqual([]);
});

test('personal generation input and visible parameters save separately and freeze draft versions', async ({ page }) => {
  const data = await assetFixture(page);
  let submitted: any = null;
  await page.route('**/api/v1/ai/generations/image', route => {
    submitted = route.request().postDataJSON();
    return route.fulfill({ status: 202, json: { generation_id: '99001', service_type: 'image', status: 'queued' } });
  });
  await page.goto(`${root}/assets`);
  await page.getByRole('textbox', { name: '画面要求', exact: true }).fill('只用于本人这次生成的雨夜灯光');
  await page.getByRole('combobox', { name: '生成张数', exact: true }).focus();
  await page.getByRole('combobox', { name: '生成张数', exact: true }).press('ArrowDown');
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: '4 张' }).click();
  await page.getByRole('button', { name: '比例与分辨率', exact: true }).click();
  await page.getByRole('combobox', { name: '画面比例', exact: true }).click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: '9:16' }).click();
  await page.getByRole('textbox', { name: '分辨率', exact: true }).fill('2K');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: '生成图片', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '生成图片', exact: true }).click();
  await expect.poll(() => submitted).not.toBeNull();
  expect(submitted.source).toMatchObject({ scene: 'asset_image', asset_id: '501', project_id: '10', episode_id: '20', row_version: data.asset.row_version });
  expect(submitted.source.draft_id).toMatch(/^\d+$/); expect(submitted.source.draft_version).toMatch(/^\d+$/);
  expect(submitted.input).toEqual({ prompt: '', reference_media_ids: [] });
  expect(submitted.parameters).toEqual({ count: 4, aspect: '9:16', resolution: '2K' });
  expect(data.requests.some(request => request.method === 'PATCH' && request.path === '/assets/501')).toBe(false);
  expect(data.asset.description).not.toContain('雨夜灯光'); expect(data.asset.prompt).not.toContain('雨夜灯光');
  page.removeAllListeners('dialog'); page.on('dialog', dialog => { void dialog.accept(); });
  await page.reload();
  await expect(page.getByRole('textbox', { name: '画面要求', exact: true })).toHaveValue('只用于本人这次生成的雨夜灯光');
  expect(data.errors).toEqual([]);
});

test('uncertain generation only performs receipt lookup and never submits another task', async ({ page }) => {
  const data = await assetFixture(page); let submissions = 0; let lookups = 0;
  await page.route('**/api/v1/ai/generations/image', route => {
    submissions += 1; return route.fulfill({ status: 503, json: { error: { code: 'OFFLINE' } } });
  });
  await page.route('**/api/v1/ai/generations/by-idempotency-key**', route => { lookups += 1; return route.fulfill({ json: null }); });
  await page.goto(`${root}/assets`);
  const generate = page.getByRole('button', { name: '生成图片', exact: true });
  await expect(generate).toBeEnabled(); await generate.click();
  await expect(page.getByRole('button', { name: '核对原生成请求', exact: true })).toBeVisible();
  await expect(generate).toBeDisabled();
  await page.getByRole('textbox', { name: '画面要求', exact: true }).fill('网络失败后仍保留的个人输入');
  await page.getByRole('button', { name: '核对原生成请求', exact: true }).click();
  await expect.poll(() => lookups).toBe(1); expect(submissions).toBe(1);
  page.removeAllListeners('dialog'); page.on('dialog', dialog => { void dialog.accept(); });
  await page.reload();
  await expect(page.getByRole('button', { name: '核对原生成请求', exact: true })).toBeVisible();
  await expect(generate).toBeDisabled(); expect(submissions).toBe(1);
  expect(data.errors).toEqual([]);
});

test('a local asset waits for its reference upload before automatic creation and keeps the uploaded media', async ({ page }) => {
  const data = await assetFixture(page);
  let releaseUpload!: () => void;
  let uploadStarted = false;
  const uploading = new Promise<void>(resolve => { releaseUpload = resolve; });
  await page.route('**/api/v1/generation-references/uploads', async route => {
    uploadStarted = true;
    await uploading;
    await route.fulfill({ status: 201, json: { media_id: '991', name: 'reference.png', url: 'data:image/png;base64,iVBORw0KGgo=' } });
  });
  await page.goto(`${root}/assets`);
  await page.getByRole('button', { name: '新增素材', exact: true }).first().click();
  await page.locator('.standard-asset-references input[type="file"]').setInputFiles({ name: 'reference.png', mimeType: 'image/png', buffer: Buffer.from('reference image') });
  await expect.poll(() => uploadStarted).toBe(true);
  await page.getByRole('textbox', { name: '素材名称', exact: true }).fill('带个人参考的庭院');
  await pickType(page, '场景');
  await page.getByRole('textbox', { name: '画面要求', exact: true }).fill('上传等待期间继续补充灯光');
  try {
    await page.waitForTimeout(1400);
    expect(data.creates).toHaveLength(0);
  } finally { releaseUpload(); }
  await expect.poll(() => data.creates.length).toBe(1);
  const createdId = data.assets.find(asset => asset.name === '带个人参考的庭院')!.id;
  await expect.poll(() => data.creationDrafts.get(`${createdId}:asset_image`)?.reference_media_ids).toEqual(['991']);
  expect(data.creationDrafts.get(`${createdId}:asset_image`)?.input_text).toBe('上传等待期间继续补充灯光');
  await expect(page.getByRole('button', { name: '移除个人参考图片 991', exact: true })).toBeVisible();
  await page.reload();
  await page.locator('.standard-asset-card').getByRole('button', { name: '带个人参考的庭院', exact: true }).click();
  await expect(page.getByRole('button', { name: '移除个人参考图片 991', exact: true })).toBeVisible();
  expect(data.creates).toHaveLength(1);
  expect(data.errors).toEqual([]);
});

test('malformed nested local generation stays available for backup and blocks automatic creation', async ({ page }) => {
  const data = await assetFixture(page);
  const key = 'short-drama:asset-drafts:v1:anonymous:10:20';
  const raw = JSON.stringify([{ id: 'draft:broken', kind: 'character', name: '损坏恢复稿', label: '', description: '', prompt: '', tags: [], scene_time: '', status: 'local', message: '', attempt: null, generation: {} }]);
  await page.addInitScript(({ key, raw }) => { localStorage.setItem(key, raw); }, { key, raw });
  await page.goto(`${root}/assets`);
  await expect(page.getByText('本机素材草稿无法读取。请下载备份后明确清除恢复记录。', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '下载本机恢复记录', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '新增素材', exact: true }).first()).toBeDisabled();
  expect(await page.evaluate(key => localStorage.getItem(key), key)).toBe(raw);
  expect(data.creates).toHaveLength(0);
  expect(data.errors).toEqual([]);
});
