import { expect, test, type Page } from '@playwright/test';
import { fixture, root } from './studio-fixture';
import { captureRefinement, refinementViewports } from './refinement-visual';

async function generationFixture(page: Page, scene: 'novel_script' | 'script_shots') {
  const base = await fixture(page, true);
  const state = { status: 'queued', submitted: false, submissions: 0, omitNext: false, polls: 0 };
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
  const actions = await Promise.all([generate.boundingBox(), history.boundingBox()]);
  expect(Math.abs(actions[0]!.y - actions[1]!.y)).toBeLessThan(2);
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
  await expect(toolbar.getByRole('button')).toHaveText(['新增分镜', '提取分镜', '历史记录']);
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
  await toolbar.getByRole('button', { name: '历史记录', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '分镜生成记录', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await toolbar.getByRole('button', { name: '提取分镜', exact: true }).click();
  await expect(settings.getByLabel('分镜要求描述', { exact: true })).toHaveValue('保持人物视线连续。');
  await expect(generate).toBeDisabled();
  data.state.status = 'failed';
  await expect(generate).toBeEnabled({ timeout: 10000 });
  expect(data.state.submissions).toBe(1);
  const firstCard = page.locator('.storyboard-item').first();
  expect(await firstCard.evaluate(node => getComputedStyle(node).borderRadius)).toBe('12px');
  expect(data.errors).toEqual([]);
});

test('asset previews and menus stay independent from card editing in the right rail', async ({ page }) => {
  const data = await fixture(page, true);
  await page.goto(`${root}/assets`);
  const card = page.locator('.episode-asset-card').first();
  const detail = page.locator('.episode-asset-detail');
  await expect(card.getByRole('button', { name: '编辑素材', exact: true })).toHaveCount(0);
  await card.getByRole('button', { name: '预览林晚', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '图片预览', exact: true })).toBeVisible();
  await expect(detail).toHaveCount(0);
  await page.keyboard.press('Escape');
  await card.getByRole('button', { name: '林晚更多操作', exact: true }).click();
  await expect(page.getByRole('menuitem', { name: '共享到项目', exact: true })).toBeVisible();
  await expect(page.getByRole('menuitem', { name: '从本集删除', exact: true })).toBeVisible();
  await expect(detail).toHaveCount(0);
  await page.keyboard.press('Escape');
  const box = await card.boundingBox();
  await card.click({ position: { x: box!.width - 80, y: box!.height - 16 } });
  await expect(detail).toBeVisible();
  await expect(page.locator('.creation-editor-target .episode-asset-detail')).toBeVisible();
  await expect(page.locator('.agent-work-pane .episode-asset-detail')).toHaveCount(0);
  await expect(detail.getByRole('button', { name: '保存并生成图片', exact: true })).toBeVisible();
  await detail.getByRole('button', { name: '返回素材列表', exact: true }).click();
  await expect(card.getByRole('button', { name: '林晚', exact: true })).toBeFocused();
  expect(data.errors).toEqual([]);
});

test('asset extraction opens from the toolbar before the project library action', async ({ page }) => {
  const data = await fixture(page, true);
  await page.goto(`${root}/assets`);
  const extract = page.getByRole('button', { name: '从剧本提取素材', exact: true });
  const library = page.getByRole('button', { name: '从项目库添加', exact: true });
  const buttons = await Promise.all([extract.boundingBox(), library.boundingBox()]);
  expect(buttons[0]!.x).toBeLessThan(buttons[1]!.x);
  await extract.click();
  await expect(page.locator('.asset-extraction-dialog')).toBeVisible();
  await expect(page.locator('.creation-slot .extraction-settings')).toHaveCount(0);
  expect(data.errors).toEqual([]);
});

test('narrow material editing opens the AI pane and returns focus to the card', async ({ page }) => {
  const data = await fixture(page, true);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${root}/assets`);
  const workspace = page.getByRole('tablist', { name: '创作工作区', exact: true });
  const entry = page.getByRole('button', { name: '林晚', exact: true });
  await expect(workspace.getByRole('tab', { name: '作品', exact: true })).toHaveAttribute('aria-selected', 'true');
  await entry.focus();
  await page.keyboard.press('Enter');
  await expect(workspace.getByRole('tab', { name: 'AI 创作', exact: true })).toHaveAttribute('aria-selected', 'true');
  const detail = page.locator('.creation-editor-target .episode-asset-detail');
  await expect(detail).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await detail.getByRole('button', { name: '返回素材列表', exact: true }).click();
  await expect(detail).toHaveCount(0);
  await expect(workspace.getByRole('tab', { name: '作品', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(entry).toBeFocused();
  expect(data.errors).toEqual([]);
});

test('material cards, right editor and storyboard remain usable at the review viewports', async ({ page }) => {
  const data = await fixture(page, true);
  await page.goto(`${root}/assets`);
  for (const viewport of refinementViewports) {
    await page.setViewportSize(viewport);
    const workTab = page.getByRole('tab', { name: '作品', exact: true });
    if (await workTab.isVisible()) await workTab.click();
    const entry = page.getByRole('button', { name: '林晚', exact: true });
    await expect(entry).toBeVisible();
    await captureRefinement(page, 'material-cards', viewport.width);
    await entry.click();
    const detail = page.locator('.creation-editor-target .episode-asset-detail');
    await expect(detail).toBeVisible();
    await captureRefinement(page, 'material-editor', viewport.width);
    await detail.getByRole('button', { name: '返回素材列表', exact: true }).click();
    await expect(entry).toBeFocused();
  }
  await page.goto(`${root}/storyboard`);
  for (const viewport of refinementViewports) {
    await page.setViewportSize(viewport);
    await expect(page.locator('.storyboard-item').first()).toBeVisible();
    await captureRefinement(page, 'storyboard-cards', viewport.width);
  }
  expect(data.errors).toEqual([]); expect(data.unexpected).toEqual([]);
});
