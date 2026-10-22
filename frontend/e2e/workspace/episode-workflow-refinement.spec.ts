import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from '../support/studio-fixture';
import { captureRefinement, refinementViewports } from '../support/refinement-visual';

async function generationFixture(page: Page, scene: 'novel_script' | 'script_shots') {
  const base = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  const state = { status: 'queued', submitted: false, submissions: 0, omitNext: false, polls: 0 };
  if (scene === 'novel_script') {
    let draft = { id: '801', row_version: '1', draft_version: '1', source_version: '1', project_id: '10', episode_id: '20', object_id: '20', purpose: 'novel_adaptation', model_config_id: '77', input_text: '保留雨夜车站的人物对白。', image_settings: null, video_settings: null, reference_media_ids: [] };
    await page.route(`**/api/v1${root}/drafts**`, async route => {
      if (route.request().method() !== 'GET') draft = { ...draft, ...route.request().postDataJSON(), row_version: String(BigInt(draft.row_version) + 1n), draft_version: String(BigInt(draft.draft_version) + 1n) };
      return route.fulfill({ json: draft });
    });
  }
  await page.route('**/api/v1/ai/generations**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === 'POST' && url.pathname.endsWith('/text')) {
      state.submissions++;
      state.submitted = true;
      state.omitNext = true;
      return route.fulfill({ json: { generation_id: '9501', service_type: 'text', status: 'queued' } });
    }
    if (request.method() === 'GET' && url.searchParams.get('source_scene') === scene) {
      state.polls++;
      const omitted = state.omitNext;
      state.omitNext = false;
      const items = state.submitted && !omitted ? [{ generation_id: '9501', service_type: 'text', status: state.status, created_at: '2026-10-03T00:00:00Z', can_cancel: false, can_retry: false, can_resume: false }] : [];
      return route.fulfill({ json: { items, total: items.length, offset: 0, limit: 20 } });
    }
    return route.fallback();
  });
  return { ...base, state };
}

test('script generation stays busy through receipt visibility, queue and completion', async ({ page }) => {
  const data = await generationFixture(page, 'novel_script');
  await page.goto(`${root}/source`);
  const generate = page.getByRole('button', { name: '生成剧本', exact: true });
  const history = page.getByRole('button', { name: '生成记录', exact: true });
  await expect(generate).toBeEnabled();
  await expect(page.locator('.generation-composer-footer').getByRole('button', { name: '生成剧本', exact: true })).toBeVisible();
  await expect(page.locator('.generation-composer-footer').getByRole('combobox', { name: '文本模型', exact: true })).toBeVisible();
  await expect(page.locator('.novel-script-heading').getByRole('button', { name: '生成记录', exact: true })).toBeVisible();
  await generate.click();
  await expect.poll(() => data.state.submissions).toBe(1);
  await expect(generate).toBeDisabled();
  await expect(generate).toHaveClass(/ant-btn-loading/);
  await history.click();
  await expect(page.getByRole('dialog', { name: '剧本生成记录', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  data.state.status = 'running';
  await expect.poll(() => data.state.polls).toBeGreaterThan(2);
  await expect(generate).toBeDisabled();
  data.state.status = 'succeeded';
  await expect(generate).toBeEnabled({ timeout: 10000 });
  expect(data.state.submissions).toBe(1);
  expect(data.errors).toEqual([]);
});

test('storyboard extraction uses a settings dialog that can close during an active task', async ({ page }) => {
  const data = await generationFixture(page, 'script_shots');
  await page.goto(`${root}/storyboard`);
  const toolbar = page.locator('.storyboard-heading-actions');
  await expect(toolbar.getByRole('button')).toHaveCount(2);
  await expect(toolbar.getByRole('button', { name: '新增分镜', exact: true })).toBeVisible();
  await toolbar.getByRole('button', { name: '提取分镜', exact: true }).click();
  const settings = page.getByRole('dialog', { name: '提取分镜', exact: true });
  const generate = settings.getByRole('button', { name: '生成分镜', exact: true });
  await settings.getByLabel('分镜要求描述', { exact: true }).fill('保持人物视线连续。');
  await generate.click();
  await expect.poll(() => data.state.submissions).toBe(1);
  await expect(generate).toBeDisabled();
  await expect(generate).toHaveClass(/ant-btn-loading/);
  await settings.getByRole('button', { name: '关闭', exact: true }).click();
  await expect(settings).toHaveCount(0);
  await page.getByRole('button', { name: '分镜生成记录', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '分镜生成记录', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await toolbar.getByRole('button', { name: '提取分镜', exact: true }).click();
  await expect(settings.getByLabel('分镜要求描述', { exact: true })).toHaveValue('保持人物视线连续。');
  await expect(generate).toBeDisabled();
  data.state.status = 'failed';
  await expect(generate).toBeEnabled({ timeout: 10000 });
  expect(data.state.submissions).toBe(1);
  const firstCard = page.locator('.storyboard-item').first();
  expect(await firstCard.evaluate(node => getComputedStyle(node).borderRadius)).toBe('8px');
  expect(data.errors).toEqual([]);
});

test('asset cards select the shared preview and settings panel without a details modal', async ({ page }) => {
  const data = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.goto(`${root}/assets`);
  const card = page.locator('.standard-asset-card').first();
  const detail = page.locator('.episode-asset-detail');
  await expect(card.getByRole('button', { name: '编辑素材', exact: true })).toHaveCount(0);
  await card.getByRole('button', { name: '林晚', exact: true }).click();
  await expect(detail).toBeVisible();
  await detail.locator('.image-subject-open').click();
  await expect(page.getByRole('dialog', { name: '图片预览', exact: true })).toBeVisible();
  await expect(detail).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(card.getByRole('button', { name: '林晚更多操作', exact: true })).toHaveCount(0);
  await detail.getByRole('button', { name: '生成记录', exact: true }).click();
  await page.locator('.asset-generation-history-dialog').getByRole('button', { name: '素材更多操作', exact: true }).click();
  await expect(page.getByRole('button', { name: '共享素材到项目', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '从本集移除素材', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: '共享素材到项目', exact: true })).toBeHidden();
  await page.keyboard.press('Escape');
  await expect(page.locator('.asset-generation-history-dialog')).toHaveCount(0);
  const box = await card.boundingBox();
  await card.click({ position: { x: box!.width - 80, y: box!.height - 16 } });
  await expect(detail).toBeVisible();
  await expect(page.locator('.creation-editor-target .episode-asset-detail')).toBeVisible();
  await expect(page.locator('.episode-work-stage .episode-asset-detail')).toHaveCount(0);
  await expect(detail.getByRole('button', { name: '生成图片', exact: true })).toBeVisible();
  await detail.getByRole('button', { name: '收起生成设置', exact: true }).click();
  await expect(detail.getByRole('textbox', { name: '描述', exact: true })).toBeHidden();
  await expect(detail.locator('.image-subject-open')).toBeVisible();
  await detail.getByRole('button', { name: '展开生成设置', exact: true }).click();
  await expect(detail.getByRole('textbox', { name: '描述', exact: true })).toHaveValue(data.asset.description);
  expect(data.errors).toEqual([]);
});

test('asset extraction opens from the toolbar before the project library action', async ({ page }) => {
  const data = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.goto(`${root}/assets`);
  const extract = page.getByRole('button', { name: '从剧本提取素材', exact: true });
  const library = page.getByRole('button', { name: '导入素材', exact: true });
  const buttons = await Promise.all([extract.boundingBox(), library.boundingBox()]);
  expect(buttons[0]!.x).toBeLessThan(buttons[1]!.x);
  await extract.click();
  await expect(page.locator('.asset-extraction-dialog')).toBeVisible();
  await expect(page.locator('.creation-slot .extraction-settings')).toHaveCount(0);
  expect(data.errors).toEqual([]);
});

test('narrow material settings preserve input and restore the settings trigger focus', async ({ page }) => {
  const data = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${root}/assets`);
  const entry = page.getByRole('button', { name: '林晚', exact: true });
  await entry.focus();
  await page.keyboard.press('Enter');
  const detail = page.locator('.creation-editor-target .episode-asset-detail');
  await expect(detail).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  const open = detail.getByRole('button', { name: '展开生成设置', exact: true });
  await open.click();
  await expect(detail.getByRole('dialog', { name: '素材生成设置', exact: true })).toBeVisible();
  await expect(detail.getByRole('textbox', { name: '描述', exact: true })).toHaveValue(data.asset.description);
  await page.keyboard.press('Escape');
  await expect(detail.getByRole('dialog', { name: '素材生成设置', exact: true })).toHaveCount(0);
  await expect(open).toBeFocused();
  expect(data.errors).toEqual([]);
});

test('material cards, right editor and storyboard remain usable at the review viewports', async ({ page }) => {
  const data = await fixture(page, true, new URL(test.info().project.use.baseURL!).origin);
  await page.goto(`${root}/assets`);
  for (const viewport of refinementViewports) {
    await page.setViewportSize(viewport);
    const entry = page.getByRole('button', { name: '林晚', exact: true });
    await expect(entry).toBeVisible();
    await captureRefinement(page, 'material-cards', viewport.width);
    await entry.click();
    const detail = page.locator('.creation-editor-target .episode-asset-detail');
    await expect(detail).toBeVisible();
    await captureRefinement(page, 'material-editor', viewport.width);
    const close = detail.getByRole('button', { name: '收起生成设置', exact: true });
    if (await close.isVisible()) await close.click();
    const expand = detail.getByRole('button', { name: '展开生成设置', exact: true });
    if (viewport.width > 700 && await expand.isVisible()) await expand.click();
  }
  await page.goto(`${root}/storyboard`);
  for (const viewport of refinementViewports) {
    await page.setViewportSize(viewport);
    await expect(page.locator('.storyboard-item').first()).toBeVisible();
    await captureRefinement(page, 'storyboard-cards', viewport.width);
  }
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});
