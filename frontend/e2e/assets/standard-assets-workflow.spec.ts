import { expect, test, type Page } from '@playwright/test';
import { fixture, image, root } from '../support/studio-fixture';

async function openSaveDetails(page: Page) {
  const trigger = page.getByRole('button', { name: /^保存状态：/ });
  if (await trigger.getAttribute('aria-expanded') !== 'true') await trigger.click();
  await expect(page.locator('.standard-save-details')).toBeVisible();
}

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

async function assetDeletionFixture(page: Page) {
  const data = await fixture(page, true, String(test.info().project.use.baseURL));
  const assets = [data.asset, { ...data.asset, id: '502', kind: 'scene', name: '雨夜车站' }];
  const removals: { id: string; version: string | undefined }[] = [];
  const state = { failure: '', pause: Promise.resolve() };
  await page.route(`**/api/v1${root}/assets**`, async route => {
    const request = route.request(); const url = new URL(request.url());
    if (request.method() === 'DELETE') {
      const id = url.pathname.split('/').at(-1)!;
      removals.push({ id, version: request.headers()['if-match'] });
      await state.pause;
      if (state.failure) return route.fulfill({ status: 409, json: { error: { code: state.failure } } });
      const index = assets.findIndex(item => item.id === id);
      if (index !== -1) assets.splice(index, 1);
      return route.fulfill({ status: 204 });
    }
    if (url.pathname !== `/api/v1${root}/assets` || request.method() !== 'GET') return route.fallback();
    const kind = url.searchParams.get('kind'); const values = assets.filter(item => !kind || item.kind === kind);
    return route.fulfill({ json: { items: values, total: values.length, offset: 0, limit: 20 } });
  });
  return { ...data, assets, removals, state };
}

test('asset card deletion opens independently, confirms, and keeps another selected asset', async ({ page }) => {
  const data = await assetDeletionFixture(page);
  await page.setViewportSize({ width: 1280, height: 844 });
  await page.goto(`${root}/assets?asset=501`);
  const more = page.getByRole('button', { name: '雨夜车站卡片更多操作', exact: true });
  await more.focus(); await page.keyboard.press('Enter');
  await expect(page.getByRole('menuitem', { name: '删除素材', exact: true })).toBeVisible();
  await expect(page.locator('.episode-asset-detail .standard-object-heading h3')).toHaveText('林晚');
  await page.keyboard.press('Escape');
  await expect(more).toBeFocused();
  await more.click(); await page.getByRole('menuitem', { name: '删除素材', exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: '删除素材', exact: true });
  await expect(confirmation).toContainText('雨夜车站');
  await confirmation.getByRole('button', { name: '取消', exact: true }).click();
  expect(data.removals).toEqual([]);
  await expect(more).toBeFocused();
  let finish!: () => void;
  data.state.pause = new Promise<void>(resolve => { finish = resolve; });
  await more.click(); await page.getByRole('menuitem', { name: '删除素材', exact: true }).click();
  await confirmation.getByRole('button', { name: '删除素材', exact: true }).click();
  await expect.poll(() => data.removals.length).toBe(1);
  await expect(more).toBeDisabled();
  await expect(more).toHaveClass(/ant-btn-loading/);
  finish();
  await expect(more).toHaveCount(0);
  await expect(page.locator('.standard-asset-card')).toHaveCount(1);
  await expect(page.locator('.episode-asset-detail .standard-object-heading h3')).toHaveText('林晚');
  await expect(page.locator('.standard-assets-heading h2 small')).toHaveText('1');
  expect(data.removals).toEqual([{ id: '502', version: '"1"' }]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('asset card deletion saves current edits and uses the acknowledged asset version', async ({ page }) => {
  const data = await assetDeletionFixture(page);
  await page.goto(`${root}/assets?asset=501`);
  await page.getByRole('textbox', { name: '描述', exact: true }).fill('删除前保存的最新素材描述');
  await page.getByRole('button', { name: '林晚卡片更多操作', exact: true }).click();
  await page.getByRole('menuitem', { name: '删除素材', exact: true }).click();
  await page.getByRole('dialog', { name: '删除素材', exact: true }).getByRole('button', { name: '删除素材', exact: true }).click();
  await expect.poll(() => data.removals.length).toBe(1);
  expect(data.asset.description).toBe('删除前保存的最新素材描述');
  expect(data.removals).toEqual([{ id: '501', version: `"${data.asset.row_version}"` }]);
  expect(data.requests.some(request => request.method === 'PATCH' && request.path === '/assets/501' && request.body.description === '删除前保存的最新素材描述')).toBe(true);
  await expect(page.getByRole('button', { name: '林晚卡片更多操作', exact: true })).toHaveCount(0);
  await expect(page.locator('.episode-asset-detail .standard-object-heading h3')).toHaveText('雨夜车站');
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('asset card deletion waits for the retained background save before deleting another card', async ({ page }) => {
  const data = await assetDeletionFixture(page);
  let finishSave!: () => void;
  const saved = new Promise<void>(resolve => { finishSave = resolve; });
  let saving = false;
  await page.route('**/api/v1/assets/501', async route => {
    if (route.request().method() !== 'PATCH') return route.fallback();
    saving = true; await saved;
    Object.assign(data.asset, route.request().postDataJSON(), { row_version: '2' });
    return route.fulfill({ json: data.asset });
  });
  await page.goto(`${root}/assets?asset=501`);
  await page.getByRole('textbox', { name: '描述', exact: true }).fill('切换后继续保存的描述');
  await page.locator('.standard-asset-card').getByRole('button', { name: '雨夜车站', exact: true }).click();
  await expect(page.locator('.episode-asset-detail .standard-object-heading h3')).toHaveText('雨夜车站');
  await expect.poll(() => saving).toBe(true);
  const more = page.getByRole('button', { name: '林晚卡片更多操作', exact: true });
  await more.click(); await page.getByRole('menuitem', { name: '删除素材', exact: true }).click();
  await page.getByRole('dialog', { name: '删除素材', exact: true }).getByRole('button', { name: '删除素材', exact: true }).click();
  await expect(more).toBeDisabled();
  expect(data.removals).toEqual([]);
  finishSave();
  await expect(more).toHaveCount(0);
  expect(data.removals).toEqual([{ id: '501', version: '"2"' }]);
  await expect(page.locator('.episode-asset-detail .standard-object-heading h3')).toHaveText('雨夜车站');
  expect(data.asset.description).toBe('切换后继续保存的描述');
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('asset card deletion preserves a failed background generation draft on an unselected card', async ({ page }) => {
  const data = await assetDeletionFixture(page);
  let failed = false;
  await page.route(`**/api/v1${root}/drafts**`, route => {
    const request = route.request();
    if (request.method() === 'GET' || request.postDataJSON().object_id !== '501' && !request.url().includes('/501/')) return route.fallback();
    failed = true;
    return route.fulfill({ status: 409, json: { error: { code: 'creation_draft_version_conflict' } } });
  });
  await page.goto(`${root}/assets?asset=501`);
  await page.getByRole('textbox', { name: '画面要求', exact: true }).fill('未选中素材仍需保留的私人生成要求');
  await page.locator('.standard-asset-card').getByRole('button', { name: '雨夜车站', exact: true }).click();
  await expect(page.locator('.episode-asset-detail .standard-object-heading h3')).toHaveText('雨夜车站');
  await expect.poll(() => failed).toBe(true);
  const more = page.getByRole('button', { name: '林晚卡片更多操作', exact: true });
  await more.click(); await page.getByRole('menuitem', { name: '删除素材', exact: true }).click();
  await page.getByRole('dialog', { name: '删除素材', exact: true }).getByRole('button', { name: '删除素材', exact: true }).click();
  await expect(more).toBeEnabled();
  await openSaveDetails(page);
  await expect(page.locator('.standard-save-details')).toContainText('私人输入已保留');
  expect(data.removals).toEqual([]);
  await page.getByRole('button', { name: /^保存状态：/ }).click();
  await page.locator('.standard-asset-card').getByRole('button', { name: '林晚', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '画面要求', exact: true })).toHaveText('未选中素材仍需保留的私人生成要求');
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

for (const failure of ['asset_in_use', 'asset_version_conflict']) {
  test(`asset card deletion keeps artwork and editing input after ${failure}`, async ({ page }) => {
    const data = await assetDeletionFixture(page); data.state.failure = failure;
    await page.goto(`${root}/assets?asset=501`);
    await page.getByRole('textbox', { name: '描述', exact: true }).fill('仍然保留的素材输入');
    await page.getByRole('button', { name: '林晚卡片更多操作', exact: true }).click();
    await page.getByRole('menuitem', { name: '删除素材', exact: true }).click();
    await page.getByRole('dialog', { name: '删除素材', exact: true }).getByRole('button', { name: '删除素材', exact: true }).click();
    await expect.poll(() => data.removals.length).toBe(1);
    await expect(page.getByRole('button', { name: '林晚卡片更多操作', exact: true })).toBeEnabled();
    await expect(page.getByRole('textbox', { name: '描述', exact: true })).toHaveValue('仍然保留的素材输入');
    await expect(page.locator('.standard-asset-card')).toHaveCount(2);
    await openSaveDetails(page);
    await expect(page.locator('.standard-save-details')).toContainText(failure === 'asset_in_use' ? '解除关联' : '刷新素材');
    await expect(page.getByRole('button', { name: '重新读取素材', exact: true })).toBeVisible();
    expect(data.removals).toHaveLength(1);
    expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
  });
}

test('asset card deletion blocks an unsaved conflict and preserves its draft', async ({ page }) => {
  const data = await assetDeletionFixture(page);
  await page.route('**/api/v1/assets/501', route => route.request().method() === 'PATCH'
    ? route.fulfill({ status: 409, json: { error: { code: 'asset_version_conflict' } } }) : route.fallback());
  await page.goto(`${root}/assets?asset=501`);
  await page.getByRole('textbox', { name: '描述', exact: true }).fill('发生冲突时保留的草稿');
  await page.getByRole('button', { name: '林晚卡片更多操作', exact: true }).click();
  await page.getByRole('menuitem', { name: '删除素材', exact: true }).click();
  await page.getByRole('dialog', { name: '删除素材', exact: true }).getByRole('button', { name: '删除素材', exact: true }).click();
  await expect(page.getByRole('button', { name: '林晚卡片更多操作', exact: true })).toBeEnabled();
  await expect(page.getByRole('textbox', { name: '描述', exact: true })).toHaveValue('发生冲突时保留的草稿');
  await openSaveDetails(page);
  await expect(page.locator('.standard-save-details')).toContainText('未删除');
  expect(data.removals).toEqual([]);
  await expect(page.locator('.standard-asset-card')).toHaveCount(2);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('local asset card deletion removes only a confirmed local draft and protects unknown creation', async ({ page }) => {
  const data = await assetDeletionFixture(page);
  await page.addInitScript(() => {
    localStorage.setItem('short-drama:asset-drafts:v1:anonymous:10', JSON.stringify([{ id: 'draft:unknown-create', kind: 'scene', name: '待核对草稿', label: '', description: '', prompt: '', tags: [], scene_time: '', status: 'unknown', message: '创建结果待核对', attempt: { key: 'unknown-create', body: { kind: 'scene', name: '待核对草稿', label: '', description: '', prompt: '', tags: [], scene_time: '' } } }]));
  });
  await page.goto(`${root}/assets?asset=501`);
  await page.getByRole('button', { name: '待核对草稿卡片更多操作', exact: true }).click();
  await expect(page.getByRole('menuitem', { name: '删除素材', exact: true })).toHaveAttribute('aria-disabled', 'true');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '创建资产', exact: true }).click();
  await page.getByRole('button', { name: '未命名素材卡片更多操作', exact: true }).click();
  await page.getByRole('menuitem', { name: '删除素材', exact: true }).click();
  await page.getByRole('dialog', { name: '删除素材草稿', exact: true }).getByRole('button', { name: '删除草稿', exact: true }).click();
  await expect(page.getByRole('button', { name: '未命名素材卡片更多操作', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '待核对草稿卡片更多操作', exact: true })).toBeVisible();
  expect(data.removals).toEqual([]);
  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('short-drama:asset-drafts:v1:anonymous:10')!));
  expect(stored).toHaveLength(1); expect(stored[0].attempt.key).toBe('unknown-create');
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

async function referenceFixture(page: Page) {
  const data = await fixture(page, true, String(test.info().project.use.baseURL));
  const privateIds = ['9901', '9902', '9903'];
  data.creationDrafts.set('501:asset_image', {
    id: '95001', project_id: '10', episode_id: '20', object_id: '501', purpose: 'asset_image', row_version: '1', draft_version: '1', source_version: '1',
    model_config_id: '77', input_text: '', image_settings: { count: 1 }, video_settings: null, reference_media_ids: privateIds,
    reference_images: privateIds.map(media_id => ({ media_id, url: media_id === '9903' ? null : image, width: 800, height: 600 })),
  });
  data.referenceState['asset/501'] = [
    { media_id: '9801', name: '共享示意图', url: image }, { media_id: '9901', name: '重复的共享图', url: image },
  ];
  return data;
}

test('asset extraction history hover keeps its icon painted and its hit area stable', async ({ page }) => {
  const data = await fixture(page, true, String(test.info().project.use.baseURL));
  await page.goto(`${root}/assets`);
  const extract = page.getByRole('button', { name: '资产提取记录', exact: true });
  await expect(extract).toBeEnabled();
  const before = (await extract.boundingBox())!;
  const paintedPixels = async () => page.evaluate(async encoded => {
    const image = new Image(); image.src = `data:image/png;base64,${encoded}`; await image.decode();
    const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
    const context = canvas.getContext('2d')!; context.drawImage(image, 0, 0);
    const pixels = context.getImageData(0, 0, image.width, image.height).data;
    let count = 0;
    for (let index = 0; index < pixels.length; index += 4) {
      if (pixels[index] > 110 && pixels[index + 1] > 110 && pixels[index + 2] > 110 && pixels[index + 3] > 0) count++;
    }
    return count;
  }, (await extract.screenshot({ animations: 'disabled' })).toString('base64'));
  const initialPaint = await paintedPixels();
  expect(initialPaint).toBeGreaterThan(10);
  await extract.hover();
  await expect.poll(() => extract.evaluate(node => getComputedStyle(node).backgroundColor)).not.toBe('rgba(0, 0, 0, 0)');
  expect(await paintedPixels()).toBeGreaterThanOrEqual(initialPaint * 0.7);
  expect(await extract.boundingBox()).toEqual(before);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('the asset page selects its first item with desktop settings open and honors an explicit route', async ({ page }) => {
  const data = await fixture(page, true, String(test.info().project.use.baseURL));
  await page.addInitScript(() => {
    localStorage.setItem('short-drama:asset-drafts:v1:anonymous:10', JSON.stringify([{ id: 'draft:empty-recovery', kind: null, name: '', label: '', description: '', prompt: '', tags: [], scene_time: '', status: 'local', message: '', attempt: null }]));
  });
  await page.setViewportSize({ width: 1280, height: 844 });
  await page.goto(`${root}/assets`);
  await expect(page.locator('.episode-asset-detail .standard-object-heading h3')).toHaveText('林晚');
  await expect(page.getByRole('button', { name: '林晚', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.standard-object-settings[aria-label="素材生成设置"]')).toBeVisible();
  await expect(page.getByRole('dialog', { name: '素材生成设置', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '未命名素材', exact: true })).toHaveAttribute('aria-pressed', 'false');
  await page.goto(`${root}/assets?asset=502`);
  await expect(page.locator('.episode-asset-detail .standard-object-heading h3')).toHaveText('雨夜车站');
  await expect(page.getByRole('button', { name: '雨夜车站', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page).toHaveURL(url => url.searchParams.get('asset') === '502');
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('desktop asset options and compact model selection save privately and restore focus with Escape', async ({ page }) => {
  const data = await fixture(page, true, String(test.info().project.use.baseURL));
  await page.route('**/api/v1/ai-model-configs?**', route => route.fulfill({ json: {
    items: [77, 78].map((id, index) => ({ id: String(id), name: `生图模型 ${index + 1} · 长名称`, model_key: `fixture-image-${index + 1}`, service_type: 'image', provider: 'fixture', enabled: 1, is_default: index === 0 ? 1 : 0, is_deleted: 0, row_version: '1', has_api_key: true })), total: 2, offset: 0, limit: 100,
  } }));
  await page.setViewportSize({ width: 1280, height: 844 });
  await page.goto(`${root}/assets`);
  const settingsTrigger = page.getByRole('button', { name: '展开生成设置', exact: true });
  const settings = page.locator('.standard-object-settings[aria-label="素材生成设置"]');
  const prompt = settings.getByRole('textbox', { name: '画面要求', exact: true });
  await prompt.fill('仅保留在本人的桌面素材设置');
  const model = settings.locator('.generation-composer-model');
  const select = model.getByRole('combobox', { name: '生图模型', exact: true });
  await expect(model.locator('.ant-select-arrow, .ant-select-clear')).toHaveCount(0);
  await model.locator('.ant-select-selector').click();
  const menu = page.locator('.creation-model-popup-compact:visible');
  await expect(menu).toBeVisible();
  expect((await menu.boundingBox())!.width).toBe(240);
  await select.press('ArrowDown'); await select.press('Enter');
  await expect.poll(() => data.creationDrafts.get('501:asset_image')?.model_config_id).toBe('78');
  const output = settings.getByRole('button', { name: '比例与分辨率', exact: true });
  await output.click();
  const options = page.getByRole('dialog', { name: '生成选项', exact: true });
  await options.getByRole('button', { name: '生成 2 张图片', exact: true }).click();
  await expect.poll(() => data.creationDrafts.get('501:asset_image')?.image_settings?.count).toBe(2);
  await page.keyboard.press('Escape');
  await expect(options).toBeHidden(); await expect(settings).toBeVisible(); await expect(output).toBeFocused();
  await settings.getByRole('button', { name: '关闭生成设置', exact: true }).click();
  await expect(settings).toBeHidden(); await expect(settingsTrigger).toBeFocused();
  await settingsTrigger.click();
  await expect(prompt).toHaveText('仅保留在本人的桌面素材设置');
  await expect(model.locator('.ant-select-selection-item')).toHaveText('生图模型 2 · 长名称');
  await expect(output).toContainText('2张');
  await settings.getByRole('button', { name: '生成图片', exact: true }).scrollIntoViewIfNeeded();
  await expect(settings.getByRole('button', { name: '生成图片', exact: true })).toBeInViewport({ ratio: 1 });
  expect(data.requests.some(request => request.method === 'PATCH' && request.path === '/assets/501')).toBe(false);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('all and named categories add immediate local cards; only complete name/type creates a server asset', async ({ page }) => {
  const data = await assetFixture(page);
  await page.goto(`${root}/assets`);
  await page.getByRole('button', { name: '创建资产', exact: true }).first().click();
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
    await page.getByRole('button', { name: '创建资产', exact: true }).first().click();
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
  await page.getByRole('button', { name: '创建资产', exact: true }).first().click();
  await page.getByRole('textbox', { name: '素材名称', exact: true }).fill('待核对庭院');
  await pickType(page, '场景');
  await openSaveDetails(page);
  await expect(page.getByRole('button', { name: '核对原创建请求', exact: true })).toBeVisible();
  expect(data.state.creations).toBe(1);
  await page.getByRole('textbox', { name: '描述', exact: true }).fill('回执等待期间继续输入');
  page.removeAllListeners('dialog'); page.on('dialog', dialog => { void dialog.accept(); });
  await page.reload();
  await expect(page.getByRole('textbox', { name: '描述', exact: true })).toHaveValue('回执等待期间继续输入');
  await openSaveDetails(page);
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
  const output = page.getByRole('button', { name: '比例与分辨率', exact: true });
  const options = page.getByRole('dialog', { name: '生成选项', exact: true });
  await expect(output).toHaveAttribute('aria-expanded', 'false');
  await output.click();
  await expect(output).toHaveAttribute('aria-expanded', 'true');
  await expect(options).toBeVisible();
  await expect(options.locator('.standard-output-options-heading')).toHaveText('生成选项');
  await expect(options.getByRole('group', { name: '选择张数', exact: true })).toBeVisible();
  await options.getByRole('button', { name: '生成 4 张图片', exact: true }).click();
  await expect(options.getByRole('button', { name: '生成 4 张图片', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(() => data.creationDrafts.get('501:asset_image')?.image_settings?.count).toBe(4);
  await expect(output).toContainText('4张');
  await expect(page.getByRole('combobox', { name: '生成张数', exact: true })).toHaveCount(0);
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
  expect(submitted.source).toMatchObject({ scene: 'asset_image', asset_id: '501', project_id: '10', row_version: data.asset.row_version });
  expect(submitted.source).not.toHaveProperty('episode_id');
  expect(submitted.source.draft_id).toMatch(/^\d+$/); expect(submitted.source.draft_version).toMatch(/^\d+$/);
  expect(submitted.input).toEqual({ prompt: '', reference_media_ids: [] });
  expect(submitted.parameters).toEqual({ count: 4, aspect: '9:16', resolution: '2K' });
  expect(data.requests.some(request => request.method === 'PATCH' && request.path === '/assets/501')).toBe(false);
  expect(data.asset.description).not.toContain('雨夜灯光'); expect(data.asset.prompt).not.toContain('雨夜灯光');
  page.removeAllListeners('dialog'); page.on('dialog', dialog => { void dialog.accept(); });
  await page.reload();
  await expect(page.getByRole('textbox', { name: '画面要求', exact: true })).toHaveText('只用于本人这次生成的雨夜灯光');
  await output.click();
  await expect(options.getByRole('button', { name: '生成 4 张图片', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.keyboard.press('Escape');
  expect(data.errors).toEqual([]);
});

test('uncertain generation only performs receipt lookup and never submits another task', async ({ page }) => {
  const data = await assetFixture(page); let submissions = 0; let lookups = 0;
  await page.route('**/api/v1/ai/generations/image', route => {
    submissions += 1; return route.fulfill({ status: 503, json: { error: { code: 'OFFLINE' } } });
  });
  await page.route('**/api/v1/ai/generations/by-idempotency-key**', route => { lookups += 1; return route.fulfill({ json: null }); });
  await page.goto(`${root}/assets`);
  await expect(page.locator('.episode-asset-detail')).toBeVisible({ timeout: 15000 });
  const generate = page.getByRole('button', { name: '生成图片', exact: true });
  await expect(generate).toBeEnabled(); await generate.click();
  await openSaveDetails(page);
  await expect(page.getByRole('button', { name: '核对原生成请求', exact: true })).toBeVisible();
  await expect(generate).toBeDisabled();
  await page.getByRole('textbox', { name: '画面要求', exact: true }).fill('网络失败后仍保留的个人输入');
  await openSaveDetails(page);
  await page.getByRole('button', { name: '核对原生成请求', exact: true }).click();
  await expect.poll(() => lookups).toBe(1); expect(submissions).toBe(1);
  page.removeAllListeners('dialog'); page.on('dialog', dialog => { void dialog.accept(); });
  await page.reload();
  await openSaveDetails(page);
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
    if (operation === 'lookup') { await openSaveDetails(page); await page.getByRole('button', { name: '核对原生成请求', exact: true }).click(); }
    await expect.poll(() => waiting).toBe(true);
    await page.evaluate(() => window.dispatchEvent(new Event('session-expired')));
    const login = page.getByRole('dialog', { name: '重新登录，继续创作', exact: true });
    await login.getByLabel('密码', { exact: true }).fill('fixture-password');
    await login.getByRole('button', { name: '登录并返回编辑', exact: true }).click();
    await expect(login).toHaveCount(0);
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
  await page.getByRole('button', { name: '创建资产', exact: true }).first().click();
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
  const key = 'short-drama:asset-drafts:v1:anonymous:10';
  const raw = JSON.stringify([{ id: 'draft:broken', kind: 'character', name: '损坏恢复稿', label: '', description: '', prompt: '', tags: [], scene_time: '', status: 'local', message: '', attempt: null, generation: {} }]);
  await page.addInitScript(({ key, raw }) => { localStorage.setItem(key, raw); }, { key, raw });
  await page.goto(`${root}/assets`);
  await openSaveDetails(page);
  await expect(page.locator('.standard-save-details .studio-feedback-details').getByText('本机素材记录无法读取。原记录已保留，请下载后核对。', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '下载本机恢复记录', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '创建资产', exact: true }).first()).toBeDisabled();
  expect(await page.evaluate(key => localStorage.getItem(key), key)).toBe(raw);
  expect(data.creates).toHaveLength(0);
  expect(data.errors).toEqual([]);
});

test('an incomplete local asset switches stages without confirmation and restores its input', async ({ page }) => {
  const data = await assetFixture(page);
  await page.goto(`${root}/assets`);
  await page.getByRole('button', { name: '创建资产', exact: true }).first().click();
  await page.getByRole('textbox', { name: '描述', exact: true }).fill('名称和类型稍后再填，先保留人物设定');
  await expect(page.getByText('本机草稿已保留', { exact: true })).toHaveCount(0);
  const nav = page.getByRole('navigation', { name: '创作流程', exact: true });
  await nav.getByRole('button', { name: /剧本创作/ }).click();
  await expect(page).toHaveURL(new RegExp(`${root}/source`));
  await expect(page.getByRole('dialog', { name: /未保存|确认操作/ })).toHaveCount(0);
  await nav.getByRole('button', { name: /资产生成/ }).click();
  await expect(page.getByRole('textbox', { name: '描述', exact: true })).toHaveValue('名称和类型稍后再填，先保留人物设定');
  expect(data.creates).toHaveLength(0); expect(data.errors).toEqual([]);
});


test('asset metadata, references and preview tools follow the compact refinement', async ({ page }) => {
  const data = await fixture(page, true, String(test.info().project.use.baseURL));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${root}/assets?asset=501`);
  const detail = page.locator('.episode-asset-detail');
  await expect(detail).toBeVisible();
  await expect(page.locator('.standard-assets-heading h2')).toHaveText('资产库 3');
  for (const [label, count] of [['全部', 3], ['角色', 1], ['场景', 1], ['道具', 1]] as const) {
    await expect(page.locator('.standard-asset-category-tabs').getByRole('button', { name: label, exact: true }).locator('small')).toHaveText(String(count));
  }
  await expect(page.getByRole('textbox', { name: '搜索素材', exact: true })).toHaveAttribute('placeholder', '搜索名称或描述');
  await expect(page.locator('.standard-assets-filters .ant-input-search-button')).toHaveCount(0);
  expect((await page.locator('.standard-asset-category-tabs').boundingBox())!.height).toBe(27);
  const card = page.locator('.standard-asset-card').first();
  const cardBounds = await card.boundingBox();
  expect(cardBounds!.width).toBe(210); expect(cardBounds!.height).toBe(171);
  expect((await card.locator('.standard-asset-card-media').boundingBox())!.height).toBe(135);
  expect((await card.locator('.standard-asset-card-caption').boundingBox())!.height).toBe(34);
  const heading = await detail.locator('.standard-object-heading').boundingBox();
  const preview = await detail.locator('.standard-object-preview').boundingBox();
  const settings = await detail.locator('.standard-object-settings').boundingBox();
  expect(heading!.height).toBe(45);
  await expect(detail.locator('.standard-object-tabs')).toHaveCount(0);
  expect(preview!.y).toBe(heading!.y + heading!.height); expect(settings!.y).toBe(preview!.y);
  expect((await detail.locator('.standard-asset-settings-heading').boundingBox())!.height).toBe(44);
  const name = await page.getByRole('textbox', { name: '素材名称', exact: true }).boundingBox();
  const type = await detail.locator('.standard-asset-name-type .ant-select').boundingBox();
  expect(name!.y).toBe(type!.y); expect(Math.abs(name!.width / type!.width - 1.35)).toBeLessThan(0.02);
  expect((await page.getByRole('textbox', { name: '描述', exact: true }).boundingBox())!.height).toBe(82);
  const reference = page.getByRole('button', { name: '添加个人参考图片', exact: true });
  await expect(reference).toBeEnabled();
  expect((await reference.boundingBox())!.height).toBe(58);
  expect((await reference.boundingBox())!.width).toBe(64);
  await expect(reference).toHaveClass(/standard-reference-add-tile/);
  await expect(reference).toHaveText('');
  await expect(detail.getByRole('button', { name: '图片', exact: true })).toHaveCount(0);
  await expect(detail.locator('.image-group-caption')).toHaveCount(0);
  await expect(detail.locator('.standard-asset-status')).toHaveText('已生图');
  const subject = detail.locator('.image-subject').first();
  await subject.hover();
  const tools = subject.getByRole('toolbar', { name: '第 1 张图片工具', exact: true });
  await expect(tools).toBeVisible();
  await tools.getByRole('button', { name: '预览第 1 张图片', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '图片预览', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(detail.getByRole('button', { name: '共享素材到项目', exact: true })).toHaveCount(0);
  await detail.getByRole('button', { name: '生成记录', exact: true }).click();
  await page.locator('.asset-generation-history-dialog').getByRole('button', { name: '素材更多操作', exact: true }).click();
  await expect(page.getByRole('button', { name: '上传作品图片', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '从资产库选择', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '删除素材', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.standard-asset-more-tools:visible')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(page.locator('.asset-generation-history-dialog')).toHaveCount(0);
  await page.locator('.standard-asset-category-tabs').getByRole('button', { name: '场景', exact: true }).click();
  await expect(page.locator('.standard-asset-card')).toHaveCount(1);
  await expect(page.locator('.standard-assets-heading h2')).toHaveText('资产库 3');
  await pickType(page, '场景');
  await expect.poll(() => data.asset.kind).toBe('scene');
  await expect(page.locator('.standard-asset-category-tabs').getByRole('button', { name: '场景', exact: true }).locator('small')).toHaveText('2');
  await expect(page.locator('.standard-asset-category-tabs').getByRole('button', { name: '角色', exact: true }).locator('small')).toHaveText('0');
  await expect(page.locator('.standard-asset-card')).toHaveCount(2);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('asset image preview exposes the saved primary without an adoption step', async ({ page }) => {
  const data = await fixture(page, true, String(test.info().project.use.baseURL));
  await page.goto(`${root}/assets?asset=501`);
  const detail = page.locator('.episode-asset-detail');
  await expect(detail.locator('.image-primary-badge')).toHaveText('主图');
  await expect(detail.getByRole('button', { name: '采用此结果', exact: true })).toHaveCount(0);
  await expect(detail.getByRole('button', { name: '最新生成', exact: true })).toHaveCount(0);
  expect(data.requests.some(request => request.method === 'POST' && request.path === '/assets/501/confirm')).toBe(false);
  expect(data.asset.media_id).toBe('9901');
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
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
  await expect(add).toHaveClass(/standard-reference-add-tile/);
  await expect(add).toHaveText('');
  expect((await add.boundingBox())!.width).toBe(64);
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
  await expect(add).toHaveClass(/standard-reference-add-tile/);
  expect((await add.boundingBox())!.width).toBe(64); expect((await add.boundingBox())!.height).toBe(58);
  expect(data.asset.media_id).toBe('9901');
  expect(data.requests.some(request => request.method === 'POST' && request.path === '/assets/501/confirm')).toBe(false);
  await page.getByRole('button', { name: '移除个人参考图片 9901', exact: true }).click();
  await expect.poll(() => data.creationDrafts.get('501:asset_image')?.reference_media_ids).toEqual([]);
  await expect(add).toHaveClass(/standard-reference-add-tile/);
  await expect(add).toHaveText('');
  expect(data.asset.media_id).toBe('9901'); expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('asset references keep shared-first numbering through selection, saving and atomic removal', async ({ page }) => {
  const data = await referenceFixture(page);
  const patches: any[] = [];
  await page.route(`**/api/v1${root}/drafts/501/asset_image`, route => {
    if (route.request().method() === 'PATCH') patches.push(route.request().postDataJSON());
    return route.fallback();
  });
  await page.goto(`${root}/assets`);
  const prompt = page.getByRole('textbox', { name: '画面要求', exact: true });
  const menu = page.getByRole('listbox', { name: '引用参考图片', exact: true });
  await prompt.fill('主体 @');
  await expect(menu.getByRole('option')).toHaveCount(4);
  await expect(menu.getByRole('option').nth(0)).toContainText('图片1');
  await expect(menu.getByRole('option').nth(1)).toContainText('图片2');
  await expect(menu.getByRole('option').nth(1)).toContainText('重复的共享图');
  await prompt.press('ArrowDown'); await prompt.press('Enter');
  await expect.poll(() => data.creationDrafts.get('501:asset_image')?.input_text).toBe('主体 @图片2 ');
  await prompt.press('Control+z');
  await expect.poll(() => data.creationDrafts.get('501:asset_image')?.input_text).toBe('主体 @');
  await expect(prompt.locator('.asset-prompt-chip')).toHaveCount(0);
  await prompt.press('Control+End');
  await expect(menu).toBeVisible();
  await prompt.press('ArrowDown'); await prompt.press('Enter');
  await expect.poll(() => data.creationDrafts.get('501:asset_image')?.input_text).toBe('主体 @图片2 ');
  await prompt.pressSequentially('与 @');
  await menu.getByRole('option').filter({ hasText: /^图片4/ }).click();
  await prompt.pressSequentially('及 @');
  await menu.getByRole('option').filter({ hasText: /^图片3/ }).click();
  const original = '主体 @图片2 与 @图片4 及 @图片3 ';
  await expect.poll(() => data.creationDrafts.get('501:asset_image')?.input_text).toBe(original);
  expect(data.creationDrafts.get('501:asset_image')?.reference_media_ids).toEqual(['9901', '9902', '9903']);
  await expect(prompt.locator('.asset-prompt-chip')).toHaveCount(3);
  await page.reload();
  await expect(prompt.locator('.asset-prompt-chip')).toHaveCount(3);
  await expect(prompt.locator('[data-asset-prompt-token="@图片4"]')).toHaveCount(1);
  patches.length = 0;
  await page.getByRole('button', { name: '移除个人参考图片 9902', exact: true }).click();
  const updated = '主体 @图片2 与 @图片3 及  ';
  await expect.poll(() => data.creationDrafts.get('501:asset_image')?.input_text).toBe(updated);
  expect(data.creationDrafts.get('501:asset_image')?.reference_media_ids).toEqual(['9901', '9903']);
  expect(patches).toHaveLength(1);
  expect(patches[0]).toMatchObject({ input_text: updated, reference_media_ids: ['9901', '9903'] });
  await expect(prompt.locator('.asset-prompt-chip')).toHaveCount(2);
  await expect(prompt.locator('[data-asset-prompt-token="@图片4"]')).toHaveCount(0);
  await expect(page.locator('.standard-asset-references > header small')).toHaveText('3 / 16');
  const saved = data.creationDrafts.get('501:asset_image')!;
  saved.reference_images = saved.reference_images!.map(item => ({ ...item, url: item.media_id === '9903' ? null : image }));
  await page.reload();
  await expect(prompt.locator('.asset-prompt-chip')).toHaveCount(2);
  await expect(page.getByRole('button', { name: '刷新预览', exact: true })).toBeVisible();
  data.referenceState['asset/501'].unshift({ media_id: '9800', name: '新增共享参考', url: image });
  saved.reference_images = saved.reference_images!.map(item => ({ ...item, url: image }));
  patches.length = 0;
  await page.getByRole('button', { name: '刷新预览', exact: true }).click();
  const refreshed = '主体 @图片3 与 @图片4 及  ';
  await expect.poll(() => data.creationDrafts.get('501:asset_image')?.input_text).toBe(refreshed);
  expect(patches).toHaveLength(1);
  expect(patches[0]).toMatchObject({ input_text: refreshed, reference_media_ids: ['9901', '9903'] });
  await expect(prompt.locator('[data-asset-prompt-token="@图片3"]')).toHaveAttribute('title', '重复的共享图');
  await expect(prompt.locator('[data-asset-prompt-token="@图片4"]')).toHaveAttribute('title', '个人参考');
  await expect(page.locator('.standard-asset-references > header small')).toHaveText('4 / 16');
  await expect(page.getByRole('button', { name: '刷新预览', exact: true })).toHaveCount(0);
  await prompt.focus(); await prompt.press('Control+a');
  const pasted = '<img src=x onerror="window.pasteExecuted=true"> & <script>bad()</script>';
  await prompt.evaluate((element, text) => {
    const clipboardData = new DataTransfer(); clipboardData.setData('text/plain', text);
    clipboardData.setData('text/html', '<img src=x onerror="window.pasteExecuted=true">');
    element.dispatchEvent(new ClipboardEvent('paste', { clipboardData, bubbles: true, cancelable: true }));
  }, pasted);
  await expect.poll(() => data.creationDrafts.get('501:asset_image')?.input_text).toBe(pasted);
  await expect(prompt).toHaveText(pasted);
  await expect(prompt.locator('img, script')).toHaveCount(0);
  expect(await page.evaluate(() => Reflect.get(window, 'pasteExecuted'))).toBeUndefined();
  expect(data.requests.some(request => request.method === 'PATCH' && request.path === '/assets/501')).toBe(false);
  expect(data.asset.media_id).toBe('9901');
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

test('desktop asset reference keyboard selection and Escape preserve the settings draft', async ({ page }) => {
  const data = await referenceFixture(page);
  await page.setViewportSize({ width: 1280, height: 640 });
  await page.goto(`${root}/assets`);
  const trigger = page.getByRole('button', { name: '展开生成设置', exact: true });
  const settings = page.locator('.standard-object-settings[aria-label="素材生成设置"]');
  const prompt = settings.getByRole('textbox', { name: '画面要求', exact: true });
  const menu = page.getByRole('listbox', { name: '引用参考图片', exact: true });
  await prompt.fill('桌面 @');
  await expect(menu).toBeInViewport({ ratio: 1 });
  const bounds = (await menu.boundingBox())!;
  expect(bounds.x).toBeGreaterThanOrEqual(0); expect(bounds.x + bounds.width).toBeLessThanOrEqual(1280);
  await prompt.press('ArrowUp');
  await expect(menu.getByRole('option').last()).toHaveAttribute('aria-selected', 'true');
  await prompt.press('Tab');
  await expect(menu).toBeHidden();
  await expect.poll(() => data.creationDrafts.get('501:asset_image')?.input_text).toBe('桌面 @图片4 ');
  await prompt.pressSequentially('@');
  await expect(menu).toBeVisible();
  await prompt.press('Escape');
  await expect(menu).toBeHidden(); await expect(settings).toBeVisible(); await expect(prompt).toBeFocused();
  await prompt.press('Escape'); await expect(settings).toBeVisible();
  await settings.getByRole('button', { name: '关闭生成设置', exact: true }).click();
  await expect(settings).toBeHidden(); await expect(trigger).toBeFocused();
  await trigger.click();
  await expect(prompt.locator('[data-asset-prompt-token="@图片4"]')).toHaveCount(1);
  await expect.poll(() => data.creationDrafts.get('501:asset_image')?.input_text).toBe('桌面 @图片4 @');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
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
  await expect(page.locator('.standard-assets-heading h2')).toHaveText('资产库 —');
  const list = page.locator('.standard-assets-scroll');
  const preview = page.locator('.standard-object-preview');
  const failedList = await list.boundingBox();
  const failedPreview = await preview.boundingBox();
  await expect(page.locator('.standard-assets-list > .ant-alert')).toHaveCount(0);
  await openSaveDetails(page);
  await expect(page.getByRole('button', { name: '重试读取分类数量', exact: true })).toBeVisible();
  await expect(page.locator('.standard-asset-card')).toHaveCount(3);
  failCounts = false;
  await page.getByRole('button', { name: '重试读取分类数量', exact: true }).click();
  await expect(page.locator('.standard-assets-heading h2')).toHaveText('资产库 3');
  await expect(page.locator('.standard-asset-category-tabs').getByRole('button', { name: '角色', exact: true }).locator('small')).toHaveText('1');
  await expect(page.getByRole('button', { name: '重试读取分类数量', exact: true })).toHaveCount(0);
  expect(await list.boundingBox()).toEqual(failedList);
  expect(await preview.boundingBox()).toEqual(failedPreview);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});


test('main asset actions reuse the history image operations', async ({ page }) => {
  const data = await fixture(page, true, String(test.info().project.use.baseURL));
  await page.goto(`${root}/assets?asset=501`);
  const main = page.locator('.episode-asset-detail');
  const more = main.getByRole('button', { name: '素材更多操作', exact: true });
  await more.click();
  await expect(page.locator('.asset-generation-history-dialog')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '上传作品图片', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '删除素材', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(more).toHaveAttribute('aria-expanded', 'false');
  await expect(more).toBeFocused();
  await more.click();
  await page.getByRole('button', { name: '从资产库选择', exact: true }).click();
  const picker = page.getByRole('dialog', { name: '从图片资产库选择', exact: true });
  await expect(picker).toContainText('选择后更新当前素材主图');
  await picker.getByRole('button', { name: '关闭弹窗', exact: true }).click();
  await expect(main.getByRole('textbox', { name: '素材名称', exact: true })).toHaveValue(data.asset.name);
  await page.getByRole('button', { name: '创建资产', exact: true }).click();
  await expect(main.getByRole('button', { name: '素材更多操作', exact: true })).toHaveCount(0);
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});

for (const viewport of [{ width: 1280, height: 720 }, { width: 1366, height: 768 }, { width: 1440, height: 900 }, { width: 1920, height: 1080 }]) {
  test(`asset heading keeps its count and tools on one row at ${viewport.width}px`, async ({ page }, info) => {
    const data = await fixture(page, true, String(test.info().project.use.baseURL));
    await page.setViewportSize(viewport);
    await page.goto(`${root}/assets?asset=501`);
    const heading = page.locator('.standard-assets-heading');
    await expect(heading.locator('h2 small')).toHaveText('3');
    const label = (await heading.locator('.standard-assets-title-text').boundingBox())!;
    const count = (await heading.locator('h2 small').boundingBox())!;
    const tools = (await heading.locator('.standard-assets-tools').boundingBox())!;
    const box = (await heading.boundingBox())!;
    expect(Math.abs(label.y + label.height / 2 - count.y - count.height / 2)).toBeLessThanOrEqual(1);
    expect(label.x + label.width).toBeLessThanOrEqual(count.x);
    expect(count.x + count.width + 4).toBeLessThanOrEqual(tools.x);
    expect(tools.x + tools.width).toBeLessThanOrEqual(box.x + box.width - 8);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await heading.screenshot({ path: info.outputPath(`asset-heading-${viewport.width}.png`) });
    expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
  });
}
