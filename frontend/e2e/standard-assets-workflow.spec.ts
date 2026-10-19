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
  await page.getByRole('combobox', { name: '素材类型', exact: true }).focus();
  await page.keyboard.press('Enter');
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
  await expect(page.locator('.standard-assets-filters').getByRole('button', { name: '全部', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.standard-asset-card').getByRole('button', { name: '新角色', exact: true })).toHaveCount(1);
  for (const [kind, label] of [['scene', '场景'], ['prop', '道具'], ['character', '角色']] as const) {
    await page.locator('.standard-assets-filters').getByRole('button', { name: label, exact: true }).click();
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
  const output = page.getByRole('button', { name: '比例与分辨率', exact: true });
  const options = page.getByRole('dialog', { name: '生成选项', exact: true });
  await expect(output).toHaveAttribute('aria-expanded', 'false');
  await output.click();
  await expect(output).toHaveAttribute('aria-expanded', 'true');
  await expect(options).toBeVisible();
  await expect(options.locator('.standard-output-options-heading')).toHaveText('比例与分辨率');
  await expect(options.getByRole('button', { name: '关闭生成选项', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '画面比例 2:3', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: '画面比例 2:3', exact: true })).toHaveAttribute('title', '当前暂不支持 2:3 比例');
  await expect(page.getByRole('button', { name: '画面比例 3:2', exact: true })).toBeDisabled();
  await expect(page.getByRole('combobox', { name: '画面比例', exact: true })).toHaveCount(0);
  await options.getByRole('button', { name: '画面比例 9:16', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '分辨率', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '2K', exact: true }).click();
  await expect.poll(() => data.creationDrafts.get('501:asset_image')?.image_settings?.resolution).toBe('2K');
  await page.getByRole('button', { name: '使用模型默认分辨率', exact: true }).click();
  await expect.poll(() => data.creationDrafts.get('501:asset_image')?.image_settings?.resolution).toBeNull();
  await expect(page.getByRole('button', { name: '使用模型默认分辨率', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: '2K', exact: true }).click();
  await page.keyboard.press('Escape');
  await expect(options).toBeHidden();
  await expect(output).toHaveAttribute('aria-expanded', 'false');
  await expect(output).toBeFocused();
  await expect(page.getByRole('button', { name: '关闭生成设置', exact: true })).toBeVisible();
  await output.press('Enter');
  await expect(options).toBeVisible();
  await options.getByRole('button', { name: '关闭生成选项', exact: true }).click();
  await expect(options).toBeHidden();
  await expect(output).toHaveAttribute('aria-expanded', 'false');
  await expect(output).toBeFocused();
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

for (const operation of ['submission', 'lookup'] as const) {
  test(`a delayed asset generation ${operation} cannot clear another account attempt`, async ({ page }) => {
    const data = await assetFixture(page);
    const account = (id: string) => ({ id, username: `artist-${id}`, display_name: `创作者 ${id}`, email: `artist-${id}@example.test`, email_verified: true });
    await page.route('**/api/v1/auth/capabilities', route => route.fulfill({ json: { enabled: true } }));
    await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: account('101') } }));
    await page.route('**/api/v1/auth/login', route => route.fulfill({ json: { user: account('102') } }));
    let waiting = false;
    let release!: () => void;
    const held = new Promise<void>(resolve => { release = resolve; });
    const receipt = { generation_id: '99001', service_type: 'image', status: 'queued' };
    await page.route('**/api/v1/ai/generations/image', async route => {
      if (operation === 'lookup') return route.fulfill({ status: 503, json: { error: { code: 'OFFLINE' } } });
      waiting = true; await held;
      return route.fulfill({ status: 202, json: receipt });
    });
    await page.route('**/api/v1/ai/generations/by-idempotency-key**', route => route.fulfill({ json: receipt }));
    await page.route('**/api/v1/ai/generations/99001', async route => {
      waiting = true; await held;
      return route.fulfill({ json: { ...receipt, source: { scene: 'asset_image', asset_id: '501', row_version: '1' }, created_at: '2026-10-14T00:00:00Z', can_cancel: true, can_retry: false, can_resume: false } });
    });
    await page.goto(`${root}/assets`);
    const generate = page.getByRole('button', { name: '生成图片', exact: true });
    await expect(generate).toBeEnabled();
    const nextScope = 'generation-attempt:user:102:asset-image:501';
    const previousScope = 'creation-recovery:user:101:asset-generation-submission:501';
    await page.evaluate(key => sessionStorage.setItem(key, 'other-account-attempt'), nextScope);
    await generate.click();
    if (operation === 'lookup') await page.getByRole('button', { name: '核对原生成请求', exact: true }).click();
    await expect.poll(() => waiting).toBe(true);
    await page.evaluate(() => window.dispatchEvent(new Event('session-expired')));
    const login = page.getByRole('dialog', { name: '重新登录，继续创作', exact: true });
    await login.getByLabel('密码', { exact: true }).fill('fixture-password');
    await login.getByRole('button', { name: '登录并返回编辑', exact: true }).click();
    await expect(page.getByRole('link', { name: '账号中心：创作者 102', exact: true })).toBeVisible();
    await expect(generate).toBeEnabled();
    const response = page.waitForResponse(value => new URL(value.url()).pathname === `/api/v1/ai/generations/${operation === 'submission' ? 'image' : '99001'}`);
    release(); await (await response).finished();
    await page.waitForTimeout(100);
    expect(await page.evaluate(key => sessionStorage.getItem(key), nextScope)).toBe('other-account-attempt');
    expect(await page.evaluate(key => localStorage.getItem(key), previousScope)).not.toBeNull();
    expect(data.errors).toEqual([]);
  });
}

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
  await expect(page.getByText('本机素材记录无法读取。原记录已保留，请下载后核对。', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '下载本机恢复记录', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '新增素材', exact: true }).first()).toBeDisabled();
  expect(await page.evaluate(key => localStorage.getItem(key), key)).toBe(raw);
  expect(data.creates).toHaveLength(0);
  expect(data.errors).toEqual([]);
});


test('asset metadata, references, tabs and preview tools follow the confirmed prototype', async ({ page }) => {
  const data = await fixture(page, true, String(test.info().project.use.baseURL));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${root}/assets?asset=501`);
  const detail = page.locator('.episode-asset-detail');
  await expect(detail).toBeVisible();
  await expect(page.locator('.standard-assets-heading h2')).toHaveText('本集素材 3');
  for (const [label, count] of [['全部', 3], ['角色', 1], ['场景', 1], ['道具', 1]] as const) {
    await expect(page.locator('.standard-asset-category-tabs').getByRole('button', { name: label, exact: true }).locator('small')).toHaveText(String(count));
  }
  await expect(page.getByRole('textbox', { name: '搜索素材', exact: true })).toHaveAttribute('placeholder', '搜索名称或描述');
  await expect(page.locator('.standard-assets-filters .ant-input-search-button')).toHaveCount(0);
  expect((await page.locator('.standard-asset-category-tabs').boundingBox())!.height).toBe(27);
  const card = page.locator('.standard-asset-card').first();
  const cardBounds = await card.boundingBox();
  expect(cardBounds!.width).toBe(224); expect(cardBounds!.height).toBe(187);
  expect((await card.locator('.standard-asset-card-media').boundingBox())!.height).toBe(147);
  expect((await card.locator('.standard-asset-card-caption').boundingBox())!.height).toBe(38);
  const heading = await detail.locator('.standard-object-heading').boundingBox();
  const tabs = await detail.locator('.standard-object-tabs').boundingBox();
  const preview = await detail.locator('.standard-object-preview').boundingBox();
  const settings = await detail.locator('.standard-object-settings').boundingBox();
  expect(heading!.height).toBe(56); expect(tabs!.height).toBe(41);
  expect(tabs!.width).toBe((await detail.boundingBox())!.width);
  expect(preview!.y).toBe(tabs!.y + tabs!.height); expect(settings!.y).toBe(preview!.y);
  expect((await detail.locator('.standard-asset-settings-heading').boundingBox())!.height).toBe(49);
  const name = await page.getByRole('textbox', { name: '素材名称', exact: true }).boundingBox();
  const type = await detail.locator('.standard-asset-name-type .ant-select').boundingBox();
  expect(name!.y).toBe(type!.y); expect(Math.abs(name!.width / type!.width - 1.35)).toBeLessThan(0.02);
  expect((await page.getByRole('textbox', { name: '描述', exact: true }).boundingBox())!.height).toBe(82);
  const reference = page.getByRole('button', { name: '添加个人参考图片', exact: true });
  await expect(reference).toBeEnabled();
  expect((await reference.boundingBox())!.height).toBe(58);
  expect((await reference.boundingBox())!.width).toBe((await detail.locator('.standard-asset-references').boundingBox())!.width);
  await expect(detail.getByRole('button', { name: '当前采用', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(detail.locator('.standard-asset-status')).toHaveText('当前采用');
  const tools = detail.getByRole('group', { name: '图片预览工具', exact: true });
  await expect(tools).toBeVisible();
  await tools.getByRole('button', { name: '放大预览', exact: true }).click();
  await expect(tools.locator('span[aria-live]')).toHaveText('110%');
  await tools.getByRole('button', { name: '适合窗口', exact: true }).click();
  await expect(tools.locator('span[aria-live]')).toHaveText('100%');
  await expect(detail.getByRole('button', { name: '共享素材到项目', exact: true })).toHaveCount(0);
  await detail.getByRole('button', { name: '生成记录', exact: true }).click();
  await page.getByRole('button', { name: '素材更多操作', exact: true }).click();
  await expect(page.getByRole('button', { name: '上传作品图片', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '从资产库选择', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '共享素材到项目', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '从本集移除素材', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
  await page.locator('.standard-asset-category-tabs').getByRole('button', { name: '场景', exact: true }).click();
  await expect(page.locator('.standard-asset-card')).toHaveCount(1);
  await expect(page.locator('.standard-assets-heading h2')).toHaveText('本集素材 3');
  await pickType(page, '场景');
  await expect.poll(() => data.asset.kind).toBe('scene');
  await expect(page.locator('.standard-asset-category-tabs').getByRole('button', { name: '场景', exact: true }).locator('small')).toHaveText('2');
  await expect(page.locator('.standard-asset-category-tabs').getByRole('button', { name: '角色', exact: true }).locator('small')).toHaveText('0');
  await expect(page.locator('.standard-asset-card')).toHaveCount(2);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('asset latest-result tab previews a candidate without adopting it', async ({ page }) => {
  const data = await fixture(page, true, String(test.info().project.use.baseURL));
  let confirmations = 0;
  await page.route('**/api/v1/assets/501/image-candidates**', route => route.fulfill({ json: {
    items: [{ id: '601', media_id: '9911', url: data.asset.image!.url, generation: null }], total: 1, offset: 0, limit: 20,
  } }));
  await page.route('**/api/v1/assets/501/confirm', async route => { confirmations += 1; await route.fallback(); });
  await page.goto(`${root}/assets?asset=501`);
  const detail = page.locator('.episode-asset-detail');
  await detail.getByRole('button', { name: '最新生成', exact: true }).click();
  await expect(detail.getByRole('button', { name: '最新生成', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(detail.locator('.standard-asset-status')).toHaveText('新结果 · 未采用');
  await expect(detail.getByRole('button', { name: '采用此结果', exact: true })).toBeEnabled();
  await expect(detail.getByRole('button', { name: '预览林晚未采用结果', exact: true })).toBeVisible();
  expect(confirmations).toBe(0); expect(data.asset.media_id).toBe('9901');
  await detail.getByRole('button', { name: '当前采用', exact: true }).click();
  await expect(detail.getByRole('button', { name: '采用此结果', exact: true })).toHaveCount(0);
  await expect(detail.getByRole('button', { name: '预览林晚', exact: true })).toBeVisible();
  expect(confirmations).toBe(0); expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('asset list retains the prototype loading, error recovery and empty states', async ({ page }) => {
  const data = await fixture(page, true, String(test.info().project.use.baseURL));
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let listState: 'loading' | 'error' | 'empty' | 'success' = 'loading';
  await page.route(`**/api/v1${root}/assets**`, async route => {
    const url = new URL(route.request().url());
    if (url.pathname !== `/api/v1${root}/assets` || url.searchParams.get('limit') === '1') return route.fallback();
    if (listState === 'loading') await gate;
    if (listState === 'error') return route.fulfill({ status: 503, json: { error: { code: 'OFFLINE', message: '素材列表暂不可读取' } } });
    if (listState === 'empty') return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 20 } });
    return route.fallback();
  });
  await page.goto(`${root}/assets`);
  await expect(page.getByRole('status', { name: '正在加载素材列表', exact: true })).toBeVisible();
  await expect(page.locator('.standard-asset-skeleton')).toHaveCount(3);
  listState = 'error'; release();
  await expect(page.getByText('列表加载失败', { exact: true })).toBeVisible();
  listState = 'success';
  await page.getByRole('button', { name: '重新加载素材', exact: true }).click();
  await expect(page.locator('.standard-asset-card')).toHaveCount(3);
  listState = 'empty';
  await page.getByRole('textbox', { name: '搜索素材', exact: true }).fill('不存在的素材');
  await expect(page.getByText('没有匹配的条目', { exact: true })).toBeVisible();
  await expect(page.getByText('试试其他关键词或分类。', { exact: true })).toBeVisible();
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('asset reference tiles add library media to the personal draft without replacing artwork', async ({ page }) => {
  const data = await fixture(page, true, String(test.info().project.use.baseURL));
  await page.goto(`${root}/assets?asset=501`);
  const add = page.getByRole('button', { name: '添加个人参考图片', exact: true });
  await expect(add).toBeEnabled(); await add.click();
  await page.getByRole('menuitem', { name: '从资产库选择参考图片', exact: true }).click();
  const picker = page.getByRole('dialog', { name: '从图片资产库选择', exact: true });
  await expect(picker).toBeVisible();
  await picker.getByRole('button', { name: '选择此图片', exact: true }).first().click();
  await expect(picker).toHaveCount(0);
  await expect(page.getByRole('button', { name: '移除个人参考图片 9901', exact: true })).toBeVisible();
  await expect.poll(() => data.creationDrafts.get('501:asset_image')?.reference_media_ids).toEqual(['9901']);
  const tile = page.locator('.standard-reference-strip figure').first();
  expect((await tile.boundingBox())!.width).toBe(64); expect((await tile.boundingBox())!.height).toBe(58);
  expect(data.asset.media_id).toBe('9901');
  expect(data.requests.some(request => request.method === 'POST' && request.path === '/assets/501/confirm')).toBe(false);
  await page.getByRole('button', { name: '移除个人参考图片 9901', exact: true }).click();
  await expect.poll(() => data.creationDrafts.get('501:asset_image')?.reference_media_ids).toEqual([]);
  expect(data.asset.media_id).toBe('9901'); expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});


test('asset category-count failure exposes retry without inventing category totals', async ({ page }) => {
  const data = await fixture(page, true, String(test.info().project.use.baseURL));
  let failCounts = true;
  await page.route(`**/api/v1${root}/assets**`, route => {
    const url = new URL(route.request().url());
    if (url.pathname === `/api/v1${root}/assets` && url.searchParams.get('limit') === '1' && url.searchParams.get('kind') === 'character' && failCounts) return route.fulfill({ status: 503, json: { error: { code: 'OFFLINE', message: '分类计数暂不可读取' } } });
    return route.fallback();
  });
  await page.goto(`${root}/assets?asset=501`);
  await expect(page.locator('.standard-assets-heading h2')).toHaveText('本集素材 —');
  await expect(page.getByRole('button', { name: '重试读取分类数量', exact: true })).toBeVisible();
  await expect(page.locator('.standard-asset-card')).toHaveCount(3);
  failCounts = false;
  await page.getByRole('button', { name: '重试读取分类数量', exact: true }).click();
  await expect(page.locator('.standard-assets-heading h2')).toHaveText('本集素材 3');
  await expect(page.locator('.standard-asset-category-tabs').getByRole('button', { name: '角色', exact: true }).locator('small')).toHaveText('1');
  await expect(page.getByRole('button', { name: '重试读取分类数量', exact: true })).toHaveCount(0);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('an incomplete local asset switches stages without confirmation and restores its input', async ({ page }) => {
  const data = await assetFixture(page);
  await page.goto(`${root}/assets`);
  await page.getByRole('button', { name: '新增素材', exact: true }).first().click();
  await page.getByRole('textbox', { name: '描述', exact: true }).fill('名称和类型稍后再填，先保留人物设定');
  await expect(page.getByText('本机草稿已保留', { exact: true })).toHaveCount(0);
  const nav = page.getByRole('navigation', { name: '分集制作流程', exact: true });
  await nav.getByRole('button', { name: /剧本创作/ }).click();
  await expect(page).toHaveURL(new RegExp(`${root}/source`));
  await expect(page.getByRole('dialog', { name: /未保存|确认操作/ })).toHaveCount(0);
  await nav.getByRole('button', { name: /素材生成/ }).click();
  await expect(page.getByRole('textbox', { name: '描述', exact: true })).toHaveValue('名称和类型稍后再填，先保留人物设定');
  expect(data.creates).toHaveLength(0); expect(data.errors).toEqual([]);
});
